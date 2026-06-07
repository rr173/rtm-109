from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from typing import List
from app.database import get_db
from app.models import Device, MaintenancePlan, ScheduleEntry, WorkOrder
from app.schemas import (
    MaintenancePlan as MaintenancePlanSchema,
    MaintenancePlanCreate,
    MaintenancePlanUpdate,
    DeviceTimelineResponse,
    DayTimeline,
    TimelineEntry,
)
import re

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


def validate_time_format(time_str: str, field_name: str):
    if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format '{time_str}'. Expected format: HH:MM (e.g., 08:00, 14:30)"
        )


def validate_day_of_week(day: int):
    if day < 0 or day > 6:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid day_of_week '{day}'. Expected 0-6 (0=Monday, 6=Sunday)"
        )


@router.post("/plans", response_model=MaintenancePlanSchema, status_code=201)
def create_maintenance_plan(plan: MaintenancePlanCreate, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == plan.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    validate_day_of_week(plan.day_of_week)
    validate_time_format(plan.start_time, "start_time")
    validate_time_format(plan.end_time, "end_time")

    if plan.start_time >= plan.end_time:
        raise HTTPException(
            status_code=400,
            detail="start_time must be earlier than end_time"
        )

    db_plan = MaintenancePlan(
        device_id=plan.device_id,
        day_of_week=plan.day_of_week,
        start_time=plan.start_time,
        end_time=plan.end_time,
        description=plan.description,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.get("/plans", response_model=List[MaintenancePlanSchema])
def list_maintenance_plans(device_id: int = None, db: Session = Depends(get_db)):
    query = db.query(MaintenancePlan)
    if device_id:
        query = query.filter(MaintenancePlan.device_id == device_id)
    return query.order_by(MaintenancePlan.id).all()


@router.get("/plans/{plan_id}", response_model=MaintenancePlanSchema)
def get_maintenance_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Maintenance plan not found")
    return plan


@router.put("/plans/{plan_id}", response_model=MaintenancePlanSchema)
def update_maintenance_plan(plan_id: int, plan: MaintenancePlanUpdate, db: Session = Depends(get_db)):
    db_plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Maintenance plan not found")

    if plan.day_of_week is not None:
        validate_day_of_week(plan.day_of_week)
        db_plan.day_of_week = plan.day_of_week
    if plan.start_time is not None:
        validate_time_format(plan.start_time, "start_time")
        db_plan.start_time = plan.start_time
    if plan.end_time is not None:
        validate_time_format(plan.end_time, "end_time")
        db_plan.end_time = plan.end_time
    if plan.description is not None:
        db_plan.description = plan.description

    if db_plan.start_time >= db_plan.end_time:
        raise HTTPException(
            status_code=400,
            detail="start_time must be earlier than end_time"
        )

    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.delete("/plans/{plan_id}", status_code=204)
def delete_maintenance_plan(plan_id: int, db: Session = Depends(get_db)):
    db_plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Maintenance plan not found")
    db.delete(db_plan)
    db.commit()
    return None


@router.get("/device-timeline/{device_id}", response_model=DeviceTimelineResponse)
def get_device_timeline(
    device_id: int,
    days: int = Query(7, ge=1, le=30, description="Number of days to view"),
    start_date: str = Query(None, description="Start date in YYYY-MM-DD format (defaults to today)"),
    db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if start_date:
        try:
            start_dt = date.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    else:
        start_dt = date.today()

    end_dt = start_dt + timedelta(days=days)

    start_datetime = datetime.combine(start_dt, datetime.min.time())
    end_datetime = datetime.combine(end_dt, datetime.max.time())

    plans = db.query(MaintenancePlan).filter(MaintenancePlan.device_id == device_id).all()

    schedule_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time < end_datetime,
        ScheduleEntry.end_time > start_datetime
    ).order_by(ScheduleEntry.start_time).all()

    order_map = {}
    for entry in schedule_entries:
        if entry.order_id not in order_map:
            order = db.query(WorkOrder).filter(WorkOrder.id == entry.order_id).first()
            order_map[entry.order_id] = order

    day_timelines = []
    for day_offset in range(days):
        current_date = start_dt + timedelta(days=day_offset)
        day_start = datetime.combine(current_date, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        entries = []

        for plan in plans:
            if current_date.weekday() == plan.day_of_week:
                start_t = datetime.strptime(plan.start_time, "%H:%M").time()
                end_t = datetime.strptime(plan.end_time, "%H:%M").time()
                win_start = datetime.combine(current_date, start_t)
                win_end = datetime.combine(current_date, end_t)
                entries.append(TimelineEntry(
                    type="maintenance",
                    start_time=win_start,
                    end_time=win_end,
                    description=plan.description or "设备维护",
                ))

        for entry in schedule_entries:
            if entry.start_time < day_end and entry.end_time > day_start:
                order = order_map.get(entry.order_id)
                entries.append(TimelineEntry(
                    type="schedule",
                    start_time=entry.start_time,
                    end_time=entry.end_time,
                    order_no=order.order_no if order else "unknown",
                    step_name=entry.step_name,
                    is_locked=order.is_locked if order else False,
                ))

        entries.sort(key=lambda e: e.start_time)

        day_timelines.append(DayTimeline(
            date=current_date.isoformat(),
            entries=entries,
        ))

    return DeviceTimelineResponse(
        device_id=device.id,
        device_name=device.name,
        device_type=device.device_type,
        days=day_timelines,
    )
