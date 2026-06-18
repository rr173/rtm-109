from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, date, timedelta
from typing import List, Optional
from app.database import get_db
from app.models import (
    WorkOrder, ScheduleEntry, ScheduleGroup, ProductFamily, Device
)
from app.schemas import (
    GroupScheduleRequest, GroupScheduleResponse, GroupScheduleResult,
    GroupScheduleRecommendationResponse, GroupRecommendation,
    GroupListResponse, ScheduleGroup as ScheduleGroupSchema,
    GroupDetailResponse, ScheduleEntry as ScheduleEntrySchema,
    ForceGroupRequest, UnGroupRequest,
    GanttWithGroupFilterResponse, DeviceGanttWithFilter, ScheduleGanttEntry
)
from app.group_scheduling_service import (
    schedule_grouped_orders, recommend_groups,
    force_group_existing_orders, ungroup_orders,
    list_groups, get_group_detail
)

router = APIRouter(prefix="/group-scheduling", tags=["group-scheduling"])


def _enrich_schedule_entries(db: Session, entries):
    enriched = []
    for e in entries:
        enriched_entry = ScheduleEntrySchema.from_orm(e)
        if e.sub_batch:
            enriched_entry.batch_no = e.sub_batch.batch_no
        if e.device:
            enriched_entry.device_name = e.device.name
        if e.fixture:
            enriched_entry.fixture_id = e.fixture_id
            enriched_entry.fixture_code = e.fixture.code
            enriched_entry.fixture_turn_over_end_time = e.fixture_turn_over_end_time
        if e.group:
            enriched_entry.group_code = e.group.group_code
        enriched.append(enriched_entry)
    return enriched


@router.post("/schedule", response_model=GroupScheduleResponse)
def group_schedule(
    request: GroupScheduleRequest,
    db: Session = Depends(get_db)
):
    if not request.order_ids:
        raise HTTPException(status_code=400, detail="请提供至少一个工单ID")

    orders = db.query(WorkOrder).filter(
        WorkOrder.id.in_(request.order_ids)
    ).all()
    if len(orders) != len(request.order_ids):
        missing = set(request.order_ids) - set(o.id for o in orders)
        raise HTTPException(status_code=404, detail=f"工单不存在: {missing}")

    result = schedule_grouped_orders(
        db=db,
        order_ids=request.order_ids,
        force_group=request.force_group,
        allow_delay=request.allow_delay,
        scenario_id=request.scenario_id,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    schedule_results = []
    for r in result["results"]:
        schedule_results.append(GroupScheduleResult(
            group_id=r["group_id"],
            group_code=r["group_code"],
            success=r["success"],
            message=r["message"],
            order_ids=r["order_ids"],
            scheduled_order_ids=r["scheduled_order_ids"],
            failed_order_ids=r["failed_order_ids"],
            estimated_savings_minutes=r["estimated_savings_minutes"],
            actual_savings_minutes=r.get("actual_savings_minutes"),
        ))

    return GroupScheduleResponse(
        success=result["success"],
        message=result["message"],
        results=schedule_results,
        total_scheduled_orders=result["total_scheduled_orders"],
        total_failed_orders=result["total_failed_orders"],
        total_estimated_savings_minutes=result["total_estimated_savings_minutes"],
    )


@router.post("/recommend", response_model=GroupScheduleRecommendationResponse)
def get_group_recommendations(
    order_ids: List[int] = Query(..., description="待分析的工单ID列表"),
    db: Session = Depends(get_db)
):
    if not order_ids:
        raise HTTPException(status_code=400, detail="请提供至少一个工单ID")

    result = recommend_groups(db, order_ids)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    recommendations = []
    for r in result["recommendations"]:
        recommendations.append(GroupRecommendation(
            product_family_id=r["product_family_id"],
            product_family_name=r["product_family_name"],
            order_ids=r["order_ids"],
            order_nos=r["order_nos"],
            device_id=r["device_id"],
            device_name=r["device_name"],
            estimated_savings_minutes=r["estimated_savings_minutes"],
            current_changeover_minutes=r["current_changeover_minutes"],
            grouped_changeover_minutes=r["grouped_changeover_minutes"],
            priority_score=r["priority_score"],
        ))

    return GroupScheduleRecommendationResponse(
        success=result["success"],
        message=result["message"],
        recommendations=recommendations,
        total_estimated_savings_minutes=result["total_estimated_savings_minutes"],
    )


@router.post("/force", response_model=GroupScheduleResult)
def force_group(
    request: ForceGroupRequest,
    db: Session = Depends(get_db)
):
    result = force_group_existing_orders(
        db=db,
        order_ids=request.order_ids,
        created_by=request.created_by,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return GroupScheduleResult(
        group_id=result["group_id"],
        group_code=result["group_code"],
        success=True,
        message=result["message"],
        order_ids=request.order_ids,
        scheduled_order_ids=request.order_ids,
        failed_order_ids=[],
        estimated_savings_minutes=0,
        actual_savings_minutes=None,
    )


@router.post("/{group_id}/ungroup")
def ungroup(
    group_id: int,
    request: Optional[UnGroupRequest] = None,
    db: Session = Depends(get_db)
):
    order_ids = request.order_ids if request else None
    result = ungroup_orders(
        db=db,
        group_id=group_id,
        order_ids=order_ids,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return result


@router.get("/groups", response_model=GroupListResponse)
def list_schedule_groups(
    device_id: Optional[int] = Query(None, description="按设备过滤"),
    product_family_id: Optional[int] = Query(None, description="按产品族过滤"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    is_forced: Optional[bool] = Query(None, description="是否仅显示强制成组"),
    scenario_id: Optional[int] = Query(None, description="按场景过滤"),
    db: Session = Depends(get_db)
):
    groups, total = list_groups(
        db=db,
        device_id=device_id,
        product_family_id=product_family_id,
        status=status,
        is_forced=is_forced,
        scenario_id=scenario_id,
    )

    group_schemas = []
    for g in groups:
        group_schemas.append(ScheduleGroupSchema(
            id=g["id"],
            group_code=g["group_code"],
            device_id=g["device_id"],
            device_name=g["device_name"],
            product_family_id=g["product_family_id"],
            product_family_name=g["product_family_name"],
            group_type=g["group_type"],
            is_forced=g["is_forced"],
            status=g["status"],
            estimated_savings_minutes=g["estimated_savings_minutes"],
            actual_savings_minutes=g.get("actual_savings_minutes"),
            created_by=g["created_by"],
            created_at=g["created_at"],
            order_ids=g["order_ids"],
            order_nos=g["order_nos"],
            entry_count=g["entry_count"],
        ))

    return GroupListResponse(groups=group_schemas, total=total)


@router.get("/groups/{group_id}", response_model=GroupDetailResponse)
def get_group(group_id: int, db: Session = Depends(get_db)):
    detail = get_group_detail(db, group_id)
    if not detail:
        raise HTTPException(status_code=404, detail="成组不存在")

    group_dict = detail["group"]
    entries = _enrich_schedule_entries(db, detail["entries"])

    group_schema = ScheduleGroupSchema(
        id=group_dict["id"],
        group_code=group_dict["group_code"],
        device_id=group_dict["device_id"],
        device_name=group_dict["device_name"],
        product_family_id=group_dict["product_family_id"],
        product_family_name=group_dict["product_family_name"],
        group_type=group_dict["group_type"],
        is_forced=group_dict["is_forced"],
        status=group_dict["status"],
        estimated_savings_minutes=group_dict["estimated_savings_minutes"],
        actual_savings_minutes=group_dict.get("actual_savings_minutes"),
        created_by=group_dict["created_by"],
        created_at=group_dict["created_at"],
        order_ids=group_dict["order_ids"],
        order_nos=group_dict["order_nos"],
        entry_count=group_dict["entry_count"],
    )

    return GroupDetailResponse(group=group_schema, schedule_entries=entries)


@router.get("/gantt", response_model=GanttWithGroupFilterResponse)
def get_gantt_with_groups(
    date_str: str = Query(..., description="Date in YYYY-MM-DD format"),
    group_ids: Optional[List[int]] = Query(None, description="按成组ID过滤"),
    db: Session = Depends(get_db)
):
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    devices = db.query(Device).order_by(Device.id).all()

    groups_query = db.query(ScheduleGroup).options(
        joinedload(ScheduleGroup.product_family),
        joinedload(ScheduleGroup.device),
        joinedload(ScheduleGroup.entries),
    ).filter(ScheduleGroup.scenario_id.is_(None))

    if group_ids:
        groups_query = groups_query.filter(ScheduleGroup.id.in_(group_ids))

    all_groups = groups_query.order_by(ScheduleGroup.created_at.desc()).all()

    group_schemas = []
    for group in all_groups:
        order_ids = list(set(e.order_id for e in group.entries if e.order_id))
        orders = db.query(WorkOrder).filter(WorkOrder.id.in_(order_ids)).all() if order_ids else []
        order_nos = [o.order_no for o in orders]

        group_schemas.append(ScheduleGroupSchema(
            id=group.id,
            group_code=group.group_code,
            device_id=group.device_id,
            device_name=group.device.name if group.device else None,
            product_family_id=group.product_family_id,
            product_family_name=group.product_family.name if group.product_family else None,
            group_type=group.group_type,
            is_forced=group.is_forced,
            status=group.status,
            estimated_savings_minutes=group.estimated_savings_minutes,
            actual_savings_minutes=group.estimated_savings_minutes,
            created_by=group.created_by,
            created_at=group.created_at,
            order_ids=order_ids,
            order_nos=order_nos,
            entry_count=len(group.entries),
        ))

    device_gantts = []
    for device in devices:
        query = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order),
            joinedload(ScheduleEntry.sub_batch),
            joinedload(ScheduleEntry.group),
        ).filter(
            ScheduleEntry.device_id == device.id,
            ScheduleEntry.start_time < day_end,
            ScheduleEntry.end_time > day_start,
            ScheduleEntry.scenario_id.is_(None)
        )

        if group_ids:
            query = query.filter(
                (ScheduleEntry.group_id.is_(None)) |
                (ScheduleEntry.group_id.in_(group_ids))
            )

        entries = query.order_by(ScheduleEntry.start_time).all()

        device_group_ids = list(set(
            e.group_id for e in entries if e.group_id is not None
        ))

        gantt_entries = []
        for entry in entries:
            order = entry.order
            if entry.changeover_start_time and entry.changeover_end_time and entry.changeover_minutes > 0:
                gantt_entries.append(ScheduleGanttEntry(
                    id=entry.id * 10000,
                    order_no=order.order_no if order else "unknown",
                    batch_no=entry.sub_batch.batch_no if entry.sub_batch else None,
                    step_name=f"换型({entry.changeover_type or ''})",
                    start_time=entry.changeover_start_time,
                    end_time=entry.changeover_end_time,
                    is_locked=order.is_locked if order else False,
                    entry_type="changeover",
                    changeover_start_time=entry.changeover_start_time,
                    changeover_end_time=entry.changeover_end_time,
                    changeover_minutes=entry.changeover_minutes,
                    changeover_type=entry.changeover_type,
                    prev_product_name=entry.prev_product_name,
                    group_id=entry.group_id,
                    group_code=entry.group.group_code if entry.group else None,
                ))
            gantt_entries.append(ScheduleGanttEntry(
                id=entry.id,
                order_no=order.order_no if order else "unknown",
                batch_no=entry.sub_batch.batch_no if entry.sub_batch else None,
                step_name=entry.step_name,
                start_time=entry.start_time,
                end_time=entry.end_time,
                is_locked=order.is_locked if order else False,
                entry_type="production",
                changeover_start_time=entry.changeover_start_time,
                changeover_end_time=entry.changeover_end_time,
                changeover_minutes=entry.changeover_minutes or 0,
                changeover_type=entry.changeover_type,
                prev_product_name=entry.prev_product_name,
                group_id=entry.group_id,
                group_code=entry.group.group_code if entry.group else None,
            ))

        device_gantts.append(DeviceGanttWithFilter(
            device_id=device.id,
            device_name=device.name,
            device_type=device.device_type,
            group_ids=device_group_ids,
            entries=gantt_entries,
        ))

    return GanttWithGroupFilterResponse(
        date=date_str,
        devices=device_gantts,
        groups=group_schemas,
    )
