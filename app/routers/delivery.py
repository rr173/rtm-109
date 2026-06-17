from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.schemas import (
    SetDeliveryPlanRequest, DeliveryPlanListResponse,
    DeliveryPlan as DeliveryPlanSchema, DeliveryProgressResponse,
    BatchDeliveryRequest, BatchDeliveryResponse,
    BatchDeliveryRecord as BatchDeliveryRecordSchema,
    DeliveryConflictInfo
)
from app.delivery_service import (
    set_delivery_plan, get_delivery_plans, execute_batch_delivery,
    get_delivery_progress, get_delivery_conflicts, cancel_order_with_delivery
)

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.post("/plans", response_model=DeliveryPlanListResponse)
def create_delivery_plan(request: SetDeliveryPlanRequest, db: Session = Depends(get_db)):
    plan_dicts = [
        {
            "plan_index": p.plan_index,
            "planned_quantity": p.planned_quantity,
            "expected_delivery_date": p.expected_delivery_date
        }
        for p in request.plans
    ]

    success, result = set_delivery_plan(db, request.order_id, plan_dicts)
    if not success:
        raise HTTPException(status_code=400, detail=result.get("message", "设置交付计划失败"))

    plans_result = get_delivery_plans(db, request.order_id)
    if not plans_result:
        raise HTTPException(status_code=404, detail="工单不存在")

    plan_schemas = [DeliveryPlanSchema(**p) for p in plans_result["plans"]]

    return DeliveryPlanListResponse(
        order_id=plans_result["order_id"],
        order_no=plans_result["order_no"],
        total_quantity=plans_result["total_quantity"],
        total_planned_quantity=plans_result["total_planned_quantity"],
        total_delivered_quantity=plans_result["total_delivered_quantity"],
        plans=plan_schemas
    )


@router.get("/orders/{order_id}/plans", response_model=DeliveryPlanListResponse)
def list_delivery_plans(order_id: int, db: Session = Depends(get_db)):
    plans_result = get_delivery_plans(db, order_id)
    if not plans_result:
        raise HTTPException(status_code=404, detail="工单不存在")

    plan_schemas = [DeliveryPlanSchema(**p) for p in plans_result["plans"]]

    return DeliveryPlanListResponse(
        order_id=plans_result["order_id"],
        order_no=plans_result["order_no"],
        total_quantity=plans_result["total_quantity"],
        total_planned_quantity=plans_result["total_planned_quantity"],
        total_delivered_quantity=plans_result["total_delivered_quantity"],
        plans=plan_schemas
    )


@router.get("/orders/{order_id}/progress", response_model=DeliveryProgressResponse)
def get_order_delivery_progress(order_id: int, db: Session = Depends(get_db)):
    progress = get_delivery_progress(db, order_id)
    if not progress:
        raise HTTPException(status_code=404, detail="工单不存在")
    return DeliveryProgressResponse(**progress)


@router.post("/deliver", response_model=BatchDeliveryResponse)
def deliver_batch(request: BatchDeliveryRequest, db: Session = Depends(get_db)):
    success, result = execute_batch_delivery(
        db=db,
        delivery_plan_id=request.delivery_plan_id,
        actual_quantity=request.actual_quantity,
        delivered_at=request.delivered_at,
        accepted_by=request.accepted_by,
        remarks=request.remarks
    )

    if not success:
        raise HTTPException(status_code=400, detail=result.get("message", "交付失败"))

    record_schema = None
    if result.get("delivery_record"):
        record_schema = BatchDeliveryRecordSchema(**result["delivery_record"])

    return BatchDeliveryResponse(
        success=True,
        message=result.get("message", "交付成功"),
        delivery_record=record_schema,
        plan_status=result.get("plan_status"),
        remaining_quantity=result.get("remaining_quantity")
    )


@router.get("/conflicts", response_model=List[DeliveryConflictInfo])
def list_delivery_conflicts(order_id: Optional[int] = None, db: Session = Depends(get_db)):
    conflicts = get_delivery_conflicts(db, order_id)
    return [DeliveryConflictInfo(**c) for c in conflicts]


@router.delete("/orders/{order_id}/cancel-with-delivery")
def cancel_order_keeping_deliveries(order_id: int, db: Session = Depends(get_db)):
    success, result = cancel_order_with_delivery(db, order_id)
    if not success:
        raise HTTPException(status_code=400, detail=result.get("message", "撤销失败"))
    return result
