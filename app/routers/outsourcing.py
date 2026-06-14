from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.database import get_db
from app.models import (
    OutsourcingFactory, OutsourcingCapability,
    StepOutsourcingConfig, ProcessStep, OutsourcingScheduleEntry
)
from app.schemas import (
    OutsourcingFactoryCreate, OutsourcingFactory as OutsourcingFactorySchema,
    OutsourcingFactoryUpdate, OutsourcingCapability as OutsourcingCapabilitySchema,
    StepOutsourcingConfig as StepOutsourcingConfigSchema,
    OutsourcingScheduleEntry as OutsourcingScheduleEntrySchema,
    OrderOutsourcingStatus, FactoryLoadResponse, FactoryDailyLoad,
    FactoryLoadEntry, OutsourcingBottleneck, OutsourcingNodeDetail,
    OrderOutsourcingStatus as OrderOutsourcingStatusSchema
)
from app.outsourcing_service import (
    get_order_outsourcing_status, get_factory_load,
    detect_outsourcing_bottlenecks, delete_outsourcing_entries_for_order
)
from datetime import datetime

router = APIRouter(prefix="/outsourcing", tags=["outsourcing"])


def _enrich_step_outsource_config(db: Session, config):
    factory = db.query(OutsourcingFactory).filter(
        OutsourcingFactory.id == config.factory_id
    ).first()
    if factory:
        config.factory_name = factory.name
        config.factory_code = factory.code
    return config


def _check_factory_in_use(db: Session, factory_id: int) -> List[str]:
    issues = []

    active_entries = db.query(OutsourcingScheduleEntry).filter(
        OutsourcingScheduleEntry.factory_id == factory_id,
        OutsourcingScheduleEntry.is_completed == False,
        OutsourcingScheduleEntry.scenario_id.is_(None)
    ).count()
    if active_entries > 0:
        issues.append(f"存在 {active_entries} 条未完成的外协排产任务")

    step_configs = db.query(StepOutsourcingConfig).filter(
        StepOutsourcingConfig.factory_id == factory_id
    ).count()
    if step_configs > 0:
        issues.append(f"被 {step_configs} 个工艺工序配置为可选外协厂")

    return issues


@router.post("/factories", response_model=OutsourcingFactorySchema, status_code=201)
def create_factory(factory: OutsourcingFactoryCreate, db: Session = Depends(get_db)):
    existing = db.query(OutsourcingFactory).filter(
        (OutsourcingFactory.name == factory.name) |
        (OutsourcingFactory.code == factory.code)
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"外协厂名称或编码已存在: name={factory.name}, code={factory.code}"
        )

    db_factory = OutsourcingFactory(
        name=factory.name,
        code=factory.code,
        contact_person=factory.contact_person,
        contact_phone=factory.contact_phone,
        address=factory.address,
        daily_start=factory.daily_start,
        daily_end=factory.daily_end,
        max_concurrent_jobs=factory.max_concurrent_jobs,
        transport_to_minutes=factory.transport_to_minutes,
        transport_back_minutes=factory.transport_back_minutes,
        waiting_before_process_minutes=factory.waiting_before_process_minutes,
        is_active=factory.is_active,
        description=factory.description
    )
    db.add(db_factory)
    db.flush()

    for cap in factory.capabilities:
        db_cap = OutsourcingCapability(
            factory_id=db_factory.id,
            process_type=cap.process_type,
            base_duration_minutes=cap.base_duration_minutes,
            duration_per_unit_minutes=cap.duration_per_unit_minutes,
            efficiency_factor=cap.efficiency_factor,
            min_batch_quantity=cap.min_batch_quantity,
            max_batch_quantity=cap.max_batch_quantity,
            quality_grade=cap.quality_grade,
            notes=cap.notes
        )
        db.add(db_cap)

    db.commit()
    db.refresh(db_factory)
    for cap in db_factory.capabilities:
        _ = cap
    return db_factory


@router.get("/factories", response_model=List[OutsourcingFactorySchema])
def list_factories(is_active: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities)
    )
    if is_active is not None:
        query = query.filter(OutsourcingFactory.is_active == is_active)
    factories = query.order_by(OutsourcingFactory.id).all()
    return factories


@router.get("/factories/{factory_id}", response_model=OutsourcingFactorySchema)
def get_factory(factory_id: int, db: Session = Depends(get_db)):
    factory = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities)
    ).filter(OutsourcingFactory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="外协厂不存在")
    return factory


@router.put("/factories/{factory_id}", response_model=OutsourcingFactorySchema)
def update_factory(
    factory_id: int,
    update: OutsourcingFactoryUpdate,
    db: Session = Depends(get_db)
):
    factory = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities)
    ).filter(OutsourcingFactory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="外协厂不存在")

    if update.is_active is not None and not update.is_active:
        issues = _check_factory_in_use(db, factory_id)
        if issues:
            raise HTTPException(
                status_code=400,
                detail=f"无法停用外协厂: {'; '.join(issues)}"
            )

    update_data = update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(factory, field, value)

    db.commit()
    db.refresh(factory)
    return factory


@router.delete("/factories/{factory_id}", status_code=204)
def delete_factory(factory_id: int, db: Session = Depends(get_db)):
    factory = db.query(OutsourcingFactory).filter(
        OutsourcingFactory.id == factory_id
    ).first()
    if not factory:
        raise HTTPException(status_code=404, detail="外协厂不存在")

    issues = _check_factory_in_use(db, factory_id)
    if issues:
        raise HTTPException(
            status_code=400,
            detail=f"无法删除外协厂: {'; '.join(issues)}"
        )

    db.query(StepOutsourcingConfig).filter(
        StepOutsourcingConfig.factory_id == factory_id
    ).delete(synchronize_session=False)

    db.delete(factory)
    db.commit()
    return None


@router.post(
    "/factories/{factory_id}/capabilities",
    response_model=OutsourcingCapabilitySchema,
    status_code=201
)
def add_factory_capability(
    factory_id: int,
    capability: OutsourcingCapabilitySchema,
    db: Session = Depends(get_db)
):
    factory = db.query(OutsourcingFactory).filter(
        OutsourcingFactory.id == factory_id
    ).first()
    if not factory:
        raise HTTPException(status_code=404, detail="外协厂不存在")

    existing = db.query(OutsourcingCapability).filter(
        OutsourcingCapability.factory_id == factory_id,
        OutsourcingCapability.process_type == capability.process_type
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"该外协厂已配置工序类型 '{capability.process_type}' 的能力"
        )

    db_cap = OutsourcingCapability(
        factory_id=factory_id,
        process_type=capability.process_type,
        base_duration_minutes=capability.base_duration_minutes,
        duration_per_unit_minutes=capability.duration_per_unit_minutes,
        efficiency_factor=capability.efficiency_factor,
        min_batch_quantity=capability.min_batch_quantity,
        max_batch_quantity=capability.max_batch_quantity,
        quality_grade=capability.quality_grade,
        notes=capability.notes
    )
    db.add(db_cap)
    db.commit()
    db.refresh(db_cap)
    return db_cap


@router.get("/orders/{order_id}/status", response_model=OrderOutsourcingStatusSchema)
def get_order_outsourcing_status_api(
    order_id: int,
    db: Session = Depends(get_db)
):
    status = get_order_outsourcing_status(db, order_id)
    if not status:
        raise HTTPException(status_code=404, detail="工单不存在")

    nodes = [
        OutsourcingNodeDetail(
            node_type=n["node_type"],
            node_sequence=n["node_sequence"],
            start_time=n["start_time"],
            end_time=n["end_time"],
            description=n["description"]
        )
        for n in status["outsourcing_nodes"]
    ]
    status["outsourcing_nodes"] = nodes
    return OrderOutsourcingStatusSchema(**status)


@router.get(
    "/factories/{factory_id}/load",
    response_model=FactoryLoadResponse
)
def get_factory_load_api(
    factory_id: int,
    look_ahead_days: int = 7,
    db: Session = Depends(get_db)
):
    if look_ahead_days < 1 or look_ahead_days > 365:
        raise HTTPException(
            status_code=400,
            detail="look_ahead_days 必须在 1 到 365 之间"
        )

    load = get_factory_load(db, factory_id, look_ahead_days)
    if not load:
        raise HTTPException(status_code=404, detail="外协厂不存在")

    days = []
    for day in load["days"]:
        entries = [
            FactoryLoadEntry(
                order_id=e["order_id"],
                order_no=e["order_no"] or "",
                sub_batch_id=e["sub_batch_id"],
                batch_no=e["batch_no"],
                step_order=e["step_order"],
                step_name=e["step_name"],
                node_type=e["node_type"],
                node_sequence=e["node_sequence"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                quantity=e["quantity"],
                is_processing_node=e["is_processing_node"]
            )
            for e in day["entries"]
        ]
        days.append(FactoryDailyLoad(
            date=day["date"],
            total_scheduled_minutes=day["total_scheduled_minutes"],
            available_minutes=day["available_minutes"],
            utilization_rate=day["utilization_rate"],
            concurrent_peak=day["concurrent_peak"],
            max_concurrent=day["max_concurrent"],
            entries=entries
        ))

    return FactoryLoadResponse(
        factory_id=load["factory_id"],
        factory_name=load["factory_name"],
        factory_code=load["factory_code"],
        look_ahead_days=load["look_ahead_days"],
        days=days,
        in_process_count=load["in_process_count"],
        queued_count=load["queued_count"],
        in_transit_to_count=load["in_transit_to_count"],
        in_transit_back_count=load["in_transit_back_count"],
        returned_waiting_count=load["returned_waiting_count"]
    )


@router.get(
    "/bottlenecks",
    response_model=List[OutsourcingBottleneck]
)
def get_outsourcing_bottlenecks_api(
    look_ahead_days: int = 7,
    db: Session = Depends(get_db)
):
    bottlenecks = detect_outsourcing_bottlenecks(db, look_ahead_days=look_ahead_days)
    return [
        OutsourcingBottleneck(
            factory_id=b["factory_id"],
            factory_name=b["factory_name"],
            bottleneck_type=b["bottleneck_type"],
            step_name=b.get("step_name"),
            order_no=b.get("order_no"),
            description=b["description"],
            detected_at=b["detected_at"]
        )
        for b in bottlenecks
    ]


@router.get(
    "/orders/{order_id}/entries",
    response_model=List[OutsourcingScheduleEntrySchema]
)
def list_order_outsourcing_entries(
    order_id: int,
    db: Session = Depends(get_db)
):
    from app.models import WorkOrder, SubBatch
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    entries = db.query(OutsourcingScheduleEntry).options(
        joinedload(OutsourcingScheduleEntry.factory),
        joinedload(OutsourcingScheduleEntry.sub_batch)
    ).filter(
        OutsourcingScheduleEntry.order_id == order_id,
        OutsourcingScheduleEntry.scenario_id.is_(None)
    ).order_by(
        OutsourcingScheduleEntry.step_order,
        OutsourcingScheduleEntry.node_sequence
    ).all()

    result = []
    for e in entries:
        schema_entry = OutsourcingScheduleEntrySchema.from_orm(e)
        schema_entry.order_no = order.order_no
        if e.sub_batch:
            schema_entry.batch_no = e.sub_batch.batch_no
        if e.factory:
            schema_entry.factory_name = e.factory.name
            schema_entry.factory_code = e.factory.code
        result.append(schema_entry)

    return result


@router.delete("/orders/{order_id}/entries", status_code=204)
def clear_order_outsourcing_entries(
    order_id: int,
    db: Session = Depends(get_db)
):
    from app.models import WorkOrder
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")

    count = delete_outsourcing_entries_for_order(db, order_id)
    db.commit()
    return None
