from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from app.database import get_db
from app.capacity_reservation_service import (
    trial_schedule, lock_reservation, list_reservations, release_reservation
)
from app.schemas import (
    TrialScheduleRequest, TrialScheduleResponse, TrialScheduleItemResult,
    TrialScheduleStepEntry,
    CapacityReservationLockRequest,
    CapacityReservationSlotInfo,
    CapacityReservationInfo, CapacityReservationListResponse,
    CapacityReservationReleaseRequest, CapacityReservationReleaseResponse
)

router = APIRouter(prefix="/capacity-reservation", tags=["capacity-reservation"])


class TrialAndLockRequest(BaseModel):
    items: List[dict] = Field(..., min_length=1, max_length=50, description="试算工单列表")
    lock_indices: List[int] = Field([], description="要锁定的试算结果索引列表，为空则不锁定")
    customer_name: Optional[str] = None
    sales_person: Optional[str] = None
    lock_duration_hours: int = Field(24, ge=1, le=168, description="锁定时长(小时)")


class TrialAndLockResponse(BaseModel):
    trial: TrialScheduleResponse
    locked_reservations: List[dict] = []


@router.post("/trial", response_model=TrialScheduleResponse)
def trial_schedule_api(request: TrialScheduleRequest, db: Session = Depends(get_db)):
    items = []
    for item in request.items:
        items.append({
            "product_name": item.product_name,
            "quantity": item.quantity,
            "expected_delivery_date": item.expected_delivery_date,
        })

    result = trial_schedule(db, items)

    trial_results = []
    for r in result["results"]:
        entries = []
        for e in r.get("schedule_entries", []):
            entries.append(TrialScheduleStepEntry(
                step_order=e["step_order"],
                step_name=e["step_name"],
                device_id=e["device_id"],
                device_name=e["device_name"],
                device_type=e["device_type"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                changeover_minutes=e.get("changeover_minutes", 0),
                fixture_id=e.get("fixture_id"),
                fixture_code=e.get("fixture_code"),
            ))
        trial_results.append(TrialScheduleItemResult(
            product_name=r["product_name"],
            quantity=r["quantity"],
            expected_delivery_date=r["expected_delivery_date"],
            can_meet_deadline=r["can_meet_deadline"],
            earliest_delivery_time=r.get("earliest_delivery_time"),
            bottleneck_type=r.get("bottleneck_type"),
            bottleneck_step=r.get("bottleneck_step"),
            bottleneck_detail=r.get("bottleneck_detail"),
            schedule_entries=entries,
        ))

    return TrialScheduleResponse(
        success=result["success"],
        message=result["message"],
        results=trial_results,
    )


@router.post("/trial-and-lock")
def trial_and_lock_api(request: TrialAndLockRequest, db: Session = Depends(get_db)):
    trial_items = []
    for item in request.items:
        if "product_name" not in item or "quantity" not in item or "expected_delivery_date" not in item:
            raise HTTPException(
                status_code=400,
                detail="每条 item 必须包含 product_name, quantity, expected_delivery_date"
            )
        trial_items.append(item)

    result = trial_schedule(db, trial_items)

    trial_results = []
    for r in result["results"]:
        entries = []
        for e in r.get("schedule_entries", []):
            entries.append(TrialScheduleStepEntry(
                step_order=e["step_order"],
                step_name=e["step_name"],
                device_id=e["device_id"],
                device_name=e["device_name"],
                device_type=e["device_type"],
                start_time=e["start_time"],
                end_time=e["end_time"],
                changeover_minutes=e.get("changeover_minutes", 0),
                fixture_id=e.get("fixture_id"),
                fixture_code=e.get("fixture_code"),
            ))
        trial_results.append(TrialScheduleItemResult(
            product_name=r["product_name"],
            quantity=r["quantity"],
            expected_delivery_date=r["expected_delivery_date"],
            can_meet_deadline=r["can_meet_deadline"],
            earliest_delivery_time=r.get("earliest_delivery_time"),
            bottleneck_type=r.get("bottleneck_type"),
            bottleneck_step=r.get("bottleneck_step"),
            bottleneck_detail=r.get("bottleneck_detail"),
            schedule_entries=entries,
        ))

    trial_response = TrialScheduleResponse(
        success=result["success"],
        message=result["message"],
        results=trial_results,
    )

    locked = []
    for idx in request.lock_indices:
        lock_result = lock_reservation(
            db,
            trial_results=result["results"],
            trial_result_index=idx,
            customer_name=request.customer_name,
            sales_person=request.sales_person,
            lock_duration_hours=request.lock_duration_hours
        )
        locked.append(lock_result)

    return {
        "trial": trial_response.model_dump(),
        "locked_reservations": locked,
    }


@router.post("/lock", response_model=dict)
def lock_from_trial_api(request: CapacityReservationLockRequest, db: Session = Depends(get_db)):
    items = []
    for item in request.items:
        items.append({
            "product_name": item.product_name,
            "quantity": item.quantity,
            "expected_delivery_date": item.expected_delivery_date,
        })

    result = trial_schedule(db, items)

    lock_result = lock_reservation(
        db,
        trial_results=result["results"],
        trial_result_index=request.trial_result_index,
        customer_name=request.customer_name,
        sales_person=request.sales_person,
        lock_duration_hours=request.lock_duration_hours
    )

    if not lock_result["success"]:
        raise HTTPException(status_code=400, detail=lock_result["message"])

    return lock_result


@router.get("/reservations", response_model=CapacityReservationListResponse)
def list_reservations_api(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    result = list_reservations(db, status=status)

    reservation_infos = []
    for r in result["reservations"]:
        slots = []
        for s in r.get("slots", []):
            slots.append(CapacityReservationSlotInfo(
                id=s["id"],
                device_id=s["device_id"],
                device_name=s.get("device_name"),
                fixture_id=s.get("fixture_id"),
                fixture_code=s.get("fixture_code"),
                step_order=s["step_order"],
                step_name=s["step_name"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                fixture_turn_over_end_time=s.get("fixture_turn_over_end_time"),
            ))
        reservation_infos.append(CapacityReservationInfo(
            id=r["id"],
            reservation_no=r["reservation_no"],
            product_name=r["product_name"],
            quantity=r["quantity"],
            customer_name=r.get("customer_name"),
            sales_person=r.get("sales_person"),
            status=r["status"],
            expire_at=r["expire_at"],
            created_at=r["created_at"],
            released_at=r.get("released_at"),
            release_reason=r.get("release_reason"),
            trial_earliest_delivery=r.get("trial_earliest_delivery"),
            trial_expected_delivery=r.get("trial_expected_delivery"),
            trial_can_meet_deadline=r.get("trial_can_meet_deadline", True),
            trial_bottleneck_type=r.get("trial_bottleneck_type"),
            trial_bottleneck_step=r.get("trial_bottleneck_step"),
            trial_bottleneck_detail=r.get("trial_bottleneck_detail"),
            slots=slots,
            remaining_seconds=r.get("remaining_seconds"),
        ))

    return CapacityReservationListResponse(
        reservations=reservation_infos,
        total=result["total"],
        active_count=result["active_count"],
    )


@router.delete("/reservations/{reservation_id}", response_model=CapacityReservationReleaseResponse)
def release_reservation_api(
    reservation_id: int,
    request: CapacityReservationReleaseRequest = None,
    db: Session = Depends(get_db)
):
    reason = None
    if request:
        reason = request.reason

    result = release_reservation(db, reservation_id, reason=reason)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return CapacityReservationReleaseResponse(
        success=result["success"],
        message=result["message"],
        reservation_id=result["reservation_id"],
        reservation_no=result["reservation_no"],
    )
