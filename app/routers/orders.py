from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from app.database import get_db
from app.models import WorkOrder, ProcessRoute, ConflictRecord, ScheduleEntry, SubBatch, Device
from app.schemas import (
    WorkOrderCreate, WorkOrder as WorkOrderSchema,
    WorkOrderScheduleResult, LockToggleResponse,
    WorkOrderSummary, SubBatch as SubBatchSchema,
    SubBatchScheduleResult, ScheduleEntry as ScheduleEntrySchema
)
from app.scheduler import (
    schedule_order, reschedule_unlocked_orders,
    release_material_locks_for_order,
    get_order_summary, release_sub_batches_for_order
)

router = APIRouter(prefix="/orders", tags=["orders"])


def _enrich_schedule_entries(db: Session, entries):
    enriched = []
    for e in entries:
        enriched_entry = ScheduleEntrySchema.from_orm(e)
        if e.sub_batch:
            enriched_entry.batch_no = e.sub_batch.batch_no
        if e.device:
            enriched_entry.device_name = e.device.name
        enriched.append(enriched_entry)
    return enriched


def _build_sub_batch_results(db: Session, order: WorkOrder):
    results = []
    for sb in order.sub_batches:
        entries = _enrich_schedule_entries(db, sb.schedule_entries)
        results.append(SubBatchScheduleResult(
            sub_batch_id=sb.id,
            batch_no=sb.batch_no,
            quantity=sb.quantity,
            status=sb.status,
            schedule_entries=entries
        ))
    return results


@router.post("/", response_model=WorkOrderScheduleResult, status_code=201)
def create_order(order: WorkOrderCreate, db: Session = Depends(get_db)):
    existing = db.query(WorkOrder).filter(WorkOrder.order_no == order.order_no).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Order with order_no '{order.order_no}' already exists")

    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
    if not route:
        raise HTTPException(status_code=400, detail=f"No process route found for product '{order.product_name}'")

    if order.expected_start_time >= order.deadline:
        raise HTTPException(status_code=400, detail="Expected start time must be before deadline")

    if order.total_quantity < 1:
        raise HTTPException(status_code=400, detail="total_quantity must be >= 1")

    db_order = WorkOrder(
        order_no=order.order_no,
        product_name=order.product_name,
        expected_start_time=order.expected_start_time,
        deadline=order.deadline,
        status="pending",
        is_locked=False,
        total_quantity=order.total_quantity,
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    result = schedule_order(db, db_order)

    if result["success"]:
        reschedule_unlocked_orders(db, exclude_order_id=db_order.id)

    db.refresh(db_order)
    for sb in db_order.sub_batches:
        _ = sb.schedule_entries

    enriched_entries = _enrich_schedule_entries(db, db_order.schedule_entries)
    sub_batch_results = _build_sub_batch_results(db, db_order)

    return WorkOrderScheduleResult(
        success=result["success"],
        order_id=db_order.id,
        order_no=db_order.order_no,
        status=db_order.status,
        is_split=result.get("is_split", db_order.is_split),
        total_sub_batches=result.get("total_sub_batches", db_order.total_sub_batches),
        bottleneck_step=result.get("bottleneck_step"),
        message=result.get("message"),
        schedule_entries=enriched_entries,
        sub_batches=sub_batch_results,
    )


@router.get("/", response_model=List[WorkOrderSchema])
def list_orders(status: str = None, db: Session = Depends(get_db)):
    query = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches),
        joinedload(WorkOrder.schedule_entries)
    )
    if status:
        query = query.filter(WorkOrder.status == status)
    orders = query.order_by(WorkOrder.id).all()
    for order in orders:
        for e in order.schedule_entries:
            _ = e.device
            _ = e.sub_batch
    return orders


@router.get("/{order_id}", response_model=WorkOrderSchema)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches),
        joinedload(WorkOrder.schedule_entries)
    ).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    for e in order.schedule_entries:
        _ = e.device
        _ = e.sub_batch
    return order


@router.get("/{order_id}/summary", response_model=WorkOrderSummary)
def get_order_summary_api(order_id: int, db: Session = Depends(get_db)):
    summary = get_order_summary(db, order_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Order not found")
    return WorkOrderSummary(**summary)


@router.get("/{order_id}/sub-batches", response_model=List[SubBatchScheduleResult])
def get_order_sub_batches(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches).joinedload(SubBatch.schedule_entries)
    ).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return _build_sub_batch_results(db, order)


@router.post("/{order_id}/lock", response_model=LockToggleResponse)
def lock_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "scheduled":
        raise HTTPException(status_code=400, detail="Only scheduled orders can be locked")

    order.is_locked = True
    db.commit()
    db.refresh(order)
    return LockToggleResponse(
        success=True,
        order_id=order.id,
        is_locked=True,
        message="Order locked successfully"
    )


@router.post("/{order_id}/unlock", response_model=LockToggleResponse)
def unlock_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order.is_locked = False
    db.commit()
    db.refresh(order)

    reschedule_unlocked_orders(db)

    db.refresh(order)
    return LockToggleResponse(
        success=True,
        order_id=order.id,
        is_locked=False,
        message="Order unlocked, rescheduling triggered"
    )


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches),
        joinedload(WorkOrder.schedule_entries)
    ).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    release_material_locks_for_order(db, order_id)
    release_sub_batches_for_order(db, order_id)
    db.delete(order)
    db.commit()

    reschedule_unlocked_orders(db)

    return None


@router.post("/{order_id}/reschedule", response_model=WorkOrderScheduleResult)
def reschedule_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches),
        joinedload(WorkOrder.schedule_entries)
    ).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.is_locked:
        raise HTTPException(status_code=400, detail="Locked order cannot be rescheduled")

    from app.models import ScheduleEntry
    release_material_locks_for_order(db, order_id)
    
    old_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
    for e in old_entries:
        db.delete(e)
    
    from app.models import SubBatch
    old_sub_batches = db.query(SubBatch).filter(SubBatch.order_id == order.id).all()
    for sb in old_sub_batches:
        db.delete(sb)
    
    order.is_split = False
    order.total_sub_batches = 0
    db.commit()

    result = schedule_order(db, order)

    if result["success"]:
        reschedule_unlocked_orders(db, exclude_order_id=order.id)

    db.refresh(order)
    for sb in order.sub_batches:
        _ = sb.schedule_entries

    enriched_entries = _enrich_schedule_entries(db, order.schedule_entries)
    sub_batch_results = _build_sub_batch_results(db, order)

    return WorkOrderScheduleResult(
        success=result["success"],
        order_id=order.id,
        order_no=order.order_no,
        status=order.status,
        is_split=result.get("is_split", order.is_split),
        total_sub_batches=result.get("total_sub_batches", order.total_sub_batches),
        bottleneck_step=result.get("bottleneck_step"),
        message=result.get("message"),
        schedule_entries=enriched_entries,
        sub_batches=sub_batch_results,
    )
