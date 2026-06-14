from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional

from app.database import get_db
from app.models import (
    Scenario, ScenarioAuditLog, ScenarioMaintenanceOverride,
    ScenarioDeviceOverride, ScenarioFixtureOverride
)
from app.schemas import (
    ScenarioCreate, ScenarioUpdate, Scenario as ScenarioSchema,
    ScenarioListResponse, ScenarioAuditLogListResponse,
    ScenarioMaintenanceOverrideCreate, ScenarioMaintenanceOverride as ScenarioMaintSchema,
    ScenarioDeviceOverrideCreate, ScenarioDeviceOverride as ScenarioDevSchema,
    ScenarioFixtureOverrideCreate, ScenarioFixtureOverride as ScenarioFixSchema,
    ScenarioDiffResponse, DelayedOrderDiff, DeviceLoadDiff, OverdueOrderDiff,
    ScenarioConstraintCheckResult, ScenarioPublishResponse,
    ScenarioUrgentOrderRequest, GanttResponse, DeviceGantt, ScheduleGanttEntry,
    ConflictListResponse, ConflictRecord as ConflictSchema, WorkOrderScheduleResult,
    ScheduleEntry as ScheduleEntrySchema, SubBatchScheduleResult, StepProgress
)
from app.scenario_service import (
    create_scenario, list_scenarios, get_scenario, delete_scenario,
    add_urgent_order_to_scenario, set_device_unavailable,
    extend_maintenance_window, adjust_fixture_quantity,
    get_scenario_gantt, get_scenario_conflicts, compute_scenario_diff,
    check_publish_constraints, publish_scenario, get_scenario_audit_logs
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _enrich_schedule_entries_scenario(db: Session, entries):
    from app.models import Device, Fixture, SubBatch
    enriched = []
    for e in entries:
        enriched_entry = ScheduleEntrySchema.from_orm(e)
        sub_batch = db.query(SubBatch).filter(SubBatch.id == e.sub_batch_id).first()
        if sub_batch:
            enriched_entry.batch_no = sub_batch.batch_no
        device = db.query(Device).filter(Device.id == e.device_id).first()
        if device:
            enriched_entry.device_name = device.name
        if e.fixture_id:
            fixture = db.query(Fixture).filter(Fixture.id == e.fixture_id).first()
            if fixture:
                enriched_entry.fixture_id = e.fixture_id
                enriched_entry.fixture_code = fixture.code
        enriched_entry.fixture_turn_over_end_time = e.fixture_turn_over_end_time
        enriched.append(enriched_entry)
    return enriched


def _build_sub_batch_results_scenario(db: Session, order_id: int, scenario_id: int):
    from app.models import SubBatch, SubBatchStepProgress
    results = []
    sub_batches = db.query(SubBatch).filter(
        SubBatch.order_id == order_id,
        SubBatch.scenario_id == scenario_id
    ).all()
    for sb in sub_batches:
        from app.models import ScheduleEntry
        entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.sub_batch_id == sb.id,
            ScheduleEntry.scenario_id == scenario_id
        ).all()
        enriched_entries = _enrich_schedule_entries_scenario(db, entries)
        progresses = db.query(SubBatchStepProgress).filter(
            SubBatchStepProgress.sub_batch_id == sb.id,
            SubBatchStepProgress.scenario_id == scenario_id
        ).order_by(SubBatchStepProgress.step_order).all()
        results.append(SubBatchScheduleResult(
            sub_batch_id=sb.id,
            batch_no=sb.batch_no,
            quantity=sb.quantity,
            status=sb.status,
            is_replenishment=sb.is_replenishment,
            replenish_level=sb.replenish_level,
            parent_sub_batch_id=sb.parent_sub_batch_id,
            schedule_entries=enriched_entries,
            step_progresses=[StepProgress.from_orm(p) for p in progresses]
        ))
    return results


@router.post("/", response_model=ScenarioSchema, status_code=201)
def create_scenario_api(
    data: ScenarioCreate,
    created_by: Optional[str] = Query(None, description="创建人"),
    db: Session = Depends(get_db)
):
    scenario = create_scenario(db, data.name, data.description, created_by)
    return ScenarioSchema.from_orm(scenario)


@router.get("/", response_model=ScenarioListResponse)
def list_scenarios_api(
    status: Optional[str] = Query(None, description="筛选状态: draft/published"),
    db: Session = Depends(get_db)
):
    scenarios = list_scenarios(db, status)
    return ScenarioListResponse(
        scenarios=[ScenarioSchema.from_orm(s) for s in scenarios],
        total=len(scenarios)
    )


@router.get("/{scenario_id}", response_model=ScenarioSchema)
def get_scenario_api(scenario_id: int, db: Session = Depends(get_db)):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    return ScenarioSchema.from_orm(scenario)


@router.put("/{scenario_id}", response_model=ScenarioSchema)
def update_scenario_api(scenario_id: int, data: ScenarioUpdate, db: Session = Depends(get_db)):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    if scenario.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿状态的预案可以修改基本信息")
    if data.name is not None:
        scenario.name = data.name
    if data.description is not None:
        scenario.description = data.description
    db.commit()
    db.refresh(scenario)
    return ScenarioSchema.from_orm(scenario)


@router.delete("/{scenario_id}")
def delete_scenario_api(
    scenario_id: int,
    operator: Optional[str] = Query(None, description="操作人"),
    db: Session = Depends(get_db)
):
    ok = delete_scenario(db, scenario_id, operator)
    if not ok:
        raise HTTPException(status_code=404, detail="预案不存在")
    return {"success": True, "message": "预案已删除"}


@router.post("/{scenario_id}/urgent-orders", response_model=WorkOrderScheduleResult)
def add_urgent_order_api(
    scenario_id: int,
    data: ScenarioUrgentOrderRequest,
    operator: Optional[str] = Query(None, description="操作人"),
    db: Session = Depends(get_db)
):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")

    order_dict = {
        "order_no": data.order_no,
        "product_name": data.product_name,
        "expected_start_time": data.expected_start_time,
        "deadline": data.deadline,
        "total_quantity": data.total_quantity,
        "priority": data.priority
    }

    ok, result = add_urgent_order_to_scenario(db, scenario_id, order_dict, operator)
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("message", "操作失败"))

    from app.models import WorkOrder
    db_order = db.query(WorkOrder).filter(
        WorkOrder.scenario_id == scenario_id,
        WorkOrder.order_no == data.order_no
    ).first()

    if not db_order:
        raise HTTPException(status_code=400, detail="创建工单失败")

    enriched_entries = _enrich_schedule_entries_scenario(db, db_order.schedule_entries)
    sub_batch_results = _build_sub_batch_results_scenario(db, db_order.id, scenario_id)

    return WorkOrderScheduleResult(
        success=result.get("success", True),
        order_id=db_order.id,
        order_no=db_order.order_no,
        status=db_order.status,
        is_split=result.get("is_split", db_order.is_split),
        total_sub_batches=result.get("total_sub_batches", db_order.total_sub_batches),
        bottleneck_step=result.get("bottleneck_step"),
        bottleneck_type=result.get("bottleneck_type"),
        bottleneck_fixture_type=result.get("bottleneck_fixture_type"),
        message=result.get("message"),
        schedule_entries=enriched_entries,
        sub_batches=sub_batch_results,
    )


@router.post("/{scenario_id}/device-overrides", response_model=ScenarioDevSchema)
def disable_device_api(
    scenario_id: int,
    data: ScenarioDeviceOverrideCreate,
    operator: Optional[str] = Query(None, description="操作人"),
    db: Session = Depends(get_db)
):
    ok, result = set_device_unavailable(
        db, scenario_id, data.device_id,
        data.effective_from or datetime.utcnow(),
        data.effective_to, data.reason, operator
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("message", "操作失败"))

    override = db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id
    ).order_by(ScenarioDeviceOverride.id.desc()).first()
    return ScenarioDevSchema.from_orm(override)


@router.get("/{scenario_id}/device-overrides", response_model=List[ScenarioDevSchema])
def list_device_overrides_api(scenario_id: int, db: Session = Depends(get_db)):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    overrides = db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id
    ).all()
    return [ScenarioDevSchema.from_orm(o) for o in overrides]


@router.post("/{scenario_id}/maintenance-overrides", response_model=ScenarioMaintSchema)
def extend_maintenance_api(
    scenario_id: int,
    data: ScenarioMaintenanceOverrideCreate,
    operator: Optional[str] = Query(None, description="操作人"),
    db: Session = Depends(get_db)
):
    ok, result = extend_maintenance_window(
        db, scenario_id, data.maintenance_plan_id or 0,
        data.new_start_time, data.new_end_time, data.new_day_of_week,
        data.description, operator
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("message", "操作失败"))

    override = db.query(ScenarioMaintenanceOverride).filter(
        ScenarioMaintenanceOverride.scenario_id == scenario_id
    ).order_by(ScenarioMaintenanceOverride.id.desc()).first()
    return ScenarioMaintSchema.from_orm(override)


@router.get("/{scenario_id}/maintenance-overrides", response_model=List[ScenarioMaintSchema])
def list_maintenance_overrides_api(scenario_id: int, db: Session = Depends(get_db)):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    overrides = db.query(ScenarioMaintenanceOverride).filter(
        ScenarioMaintenanceOverride.scenario_id == scenario_id
    ).all()
    return [ScenarioMaintSchema.from_orm(o) for o in overrides]


@router.post("/{scenario_id}/fixture-overrides", response_model=ScenarioFixSchema)
def adjust_fixture_api(
    scenario_id: int,
    data: ScenarioFixtureOverrideCreate,
    operator: Optional[str] = Query(None, description="操作人"),
    db: Session = Depends(get_db)
):
    if not data.fixture_type_id and not data.fixture_id:
        raise HTTPException(status_code=400, detail="fixture_type_id 或 fixture_id 必须提供一个")

    ok, result = adjust_fixture_quantity(
        db, scenario_id, data.fixture_type_id or 0,
        data.quantity_change, data.reason, operator
    )
    if not ok:
        raise HTTPException(status_code=400, detail=result.get("message", "操作失败"))

    override = db.query(ScenarioFixtureOverride).filter(
        ScenarioFixtureOverride.scenario_id == scenario_id
    ).order_by(ScenarioFixtureOverride.id.desc()).first()
    return ScenarioFixSchema.from_orm(override)


@router.get("/{scenario_id}/fixture-overrides", response_model=List[ScenarioFixSchema])
def list_fixture_overrides_api(scenario_id: int, db: Session = Depends(get_db)):
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    overrides = db.query(ScenarioFixtureOverride).filter(
        ScenarioFixtureOverride.scenario_id == scenario_id
    ).all()
    return [ScenarioFixSchema.from_orm(o) for o in overrides]


@router.get("/{scenario_id}/gantt", response_model=GanttResponse)
def get_scenario_gantt_api(
    scenario_id: int,
    date_str: str = Query(..., description="日期 YYYY-MM-DD"),
    db: Session = Depends(get_db)
):
    result = get_scenario_gantt(db, scenario_id, date_str)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    devices = []
    for d in result["devices"]:
        entries = []
        for e in d["entries"]:
            entries.append(ScheduleGanttEntry(
                id=e["id"],
                order_no=e["order_no"],
                batch_no=e["batch_no"],
                step_name=e["step_name"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                is_locked=e["is_locked"],
                entry_type=e.get("entry_type", "production"),
                changeover_start_time=e.get("changeover_start_time"),
                changeover_end_time=e.get("changeover_end_time"),
                changeover_minutes=e.get("changeover_minutes", 0),
                changeover_type=e.get("changeover_type"),
                prev_product_name=e.get("prev_product_name"),
            ))
        devices.append(DeviceGantt(
            device_id=d["device_id"],
            device_name=d["device_name"],
            device_type=d["device_type"],
            entries=entries
        ))

    return GanttResponse(date=date_str, devices=devices)


@router.get("/{scenario_id}/conflicts", response_model=ConflictListResponse)
def get_scenario_conflicts_api(
    scenario_id: int,
    date_str: str = Query(None, description="筛选日期 YYYY-MM-DD"),
    conflict_type: str = Query(None, description="筛选冲突类型"),
    db: Session = Depends(get_db)
):
    result = get_scenario_conflicts(db, scenario_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    conflicts = result["conflicts"]
    if date_str:
        try:
            from datetime import date as dt_date
            target = dt_date.fromisoformat(date_str)
            day_start = datetime.combine(target, datetime.min.time())
            day_end = day_start.replace(hour=23, minute=59, second=59)
            conflicts = [
                c for c in conflicts
                if day_start <= c["detected_at"] <= day_end
            ]
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误")

    if conflict_type:
        conflicts = [c for c in conflicts if c["conflict_type"] == conflict_type]

    return ConflictListResponse(
        conflicts=[ConflictSchema(**c) for c in conflicts],
        total=len(conflicts)
    )


@router.get("/{scenario_id}/diff", response_model=ScenarioDiffResponse)
def get_scenario_diff_api(scenario_id: int, db: Session = Depends(get_db)):
    result = compute_scenario_diff(db, scenario_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    delayed = [DelayedOrderDiff(**d) for d in result["delayed_orders"]]
    loads = [DeviceLoadDiff(**d) for d in result["device_load_changes"]]
    overdue = [OverdueOrderDiff(**d) for d in result["overdue_orders"]]

    return ScenarioDiffResponse(
        scenario_id=result["scenario_id"],
        scenario_name=result["scenario_name"],
        baseline_unchanged=result["baseline_unchanged"],
        delayed_orders=delayed,
        device_load_changes=loads,
        overdue_orders=overdue,
        total_delayed=result["total_delayed"],
        total_devices_changed=result["total_devices_changed"],
        total_overdue_changed=result["total_overdue_changed"]
    )


@router.get("/{scenario_id}/publish-check", response_model=ScenarioConstraintCheckResult)
def get_publish_constraints_api(scenario_id: int, db: Session = Depends(get_db)):
    result = check_publish_constraints(db, scenario_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return ScenarioConstraintCheckResult(**result)


@router.post("/{scenario_id}/publish", response_model=ScenarioPublishResponse)
def publish_scenario_api(
    scenario_id: int,
    operator: Optional[str] = Query(None, description="发布人"),
    db: Session = Depends(get_db)
):
    ok, result = publish_scenario(db, scenario_id, operator)
    if not ok:
        constraints = result.get("constraints")
        if constraints:
            constraints_obj = ScenarioConstraintCheckResult(**constraints)
        else:
            constraints_obj = None
        raise HTTPException(
            status_code=400,
            detail={
                "message": result.get("message", "发布失败"),
                "constraints": constraints_obj.dict() if constraints_obj else None
            }
        )

    constraints = result.get("constraints")
    constraints_obj = ScenarioConstraintCheckResult(**constraints) if constraints else None

    return ScenarioPublishResponse(
        success=True,
        message=result["message"],
        scenario_id=result["scenario_id"],
        published_at=result.get("published_at"),
        constraints=constraints_obj
    )


@router.get("/{scenario_id}/audit-logs", response_model=ScenarioAuditLogListResponse)
def get_audit_logs_api(scenario_id: int, db: Session = Depends(get_db)):
    result = get_scenario_audit_logs(db, scenario_id)
    return ScenarioAuditLogListResponse(
        logs=[ScenarioAuditLog(
            id=l["id"],
            scenario_id=result["scenario_id"],
            action=l["action"],
            operator=l["operator"],
            details=l["details"],
            created_at=l["created_at"]
        ) for l in result["logs"]],
        total=result["total"]
    )
