from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import WorkOrder, ProcessRoute, ConflictRecord
from app.schemas import (
    WorkOrderCreate, WorkOrder as WorkOrderSchema,
    WorkOrderScheduleResult, LockToggleResponse
)
from app.scheduler import schedule_order, reschedule_unlocked_orders, release_material_locks_for_order

router = APIRouter(prefix="/orders", tags=["orders"])


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

    db_order = WorkOrder(
        order_no=order.order_no,
        product_name=order.product_name,
        expected_start_time=order.expected_start_time,
        deadline=order.deadline,
        status="pending",
        is_locked=False,
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    result = schedule_order(db, db_order)

    if result["success"]:
        reschedule_unlocked_orders(db, exclude_order_id=db_order.id)

    db.refresh(db_order)
    return WorkOrderScheduleResult(
        success=result["success"],
        order_id=db_order.id,
        order_no=db_order.order_no,
        status=db_order.status,
        bottleneck_step=result.get("bottleneck_step"),
        message=result.get("message"),
        schedule_entries=db_order.schedule_entries,
    )


@router.get("/", response_model=List[WorkOrderSchema])
def list_orders(status: str = None, db: Session = Depends(get_db)):
    query = db.query(WorkOrder)
    if status:
        query = query.filter(WorkOrder.status == status)
    return query.order_by(WorkOrder.id).all()


@router.get("/{order_id}", response_model=WorkOrderSchema)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


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
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    release_material_locks_for_order(db, order_id)
    db.delete(order)
    db.commit()

    reschedule_unlocked_orders(db)

    return None


@router.post("/{order_id}/reschedule", response_model=WorkOrderScheduleResult)
def reschedule_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.is_locked:
        raise HTTPException(status_code=400, detail="Locked order cannot be rescheduled")

    from app.models import ScheduleEntry
    release_material_locks_for_order(db, order_id)
    old_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
    for e in old_entries:
        db.delete(e)
    db.commit()

    result = schedule_order(db, order)

    if result["success"]:
        reschedule_unlocked_orders(db, exclude_order_id=order.id)

    db.refresh(order)
    return WorkOrderScheduleResult(
        success=result["success"],
        order_id=order.id,
        order_no=order.order_no,
        status=order.status,
        bottleneck_step=result.get("bottleneck_step"),
        message=result.get("message"),
        schedule_entries=order.schedule_entries,
    )
