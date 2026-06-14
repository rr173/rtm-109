from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, date, timedelta
from typing import List
from app.database import get_db
from app.models import Device, ScheduleEntry, WorkOrder, ConflictRecord, SubBatch
from app.schemas import (
    GanttResponse, DeviceGantt, ScheduleGanttEntry,
    ConflictListResponse, ConflictRecord as ConflictSchema
)

router = APIRouter(prefix="/schedule", tags=["schedule"])


@router.get("/gantt", response_model=GanttResponse)
def get_gantt(
    date_str: str = Query(..., description="Date in YYYY-MM-DD format"),
    db: Session = Depends(get_db)
):
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    devices = db.query(Device).order_by(Device.id).all()

    device_gantts = []
    for device in devices:
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order),
            joinedload(ScheduleEntry.sub_batch)
        ).filter(
            ScheduleEntry.device_id == device.id,
            ScheduleEntry.start_time < day_end,
            ScheduleEntry.end_time > day_start,
            ScheduleEntry.scenario_id.is_(None)
        ).order_by(ScheduleEntry.start_time).all()

        gantt_entries = []
        for entry in entries:
            order = entry.order
            gantt_entries.append(ScheduleGanttEntry(
                id=entry.id,
                order_no=order.order_no if order else "unknown",
                batch_no=entry.sub_batch.batch_no if entry.sub_batch else None,
                step_name=entry.step_name,
                start_time=entry.start_time,
                end_time=entry.end_time,
                is_locked=order.is_locked if order else False,
                changeover_start_time=entry.changeover_start_time,
                changeover_minutes=entry.changeover_minutes,
                prev_product_name=entry.prev_product_name,
            ))

        device_gantts.append(DeviceGantt(
            device_id=device.id,
            device_name=device.name,
            device_type=device.device_type,
            entries=gantt_entries,
        ))

    return GanttResponse(
        date=date_str,
        devices=device_gantts,
    )


@router.get("/conflicts", response_model=ConflictListResponse)
def get_conflicts(
    date_str: str = Query(None, description="Filter by date (YYYY-MM-DD)"),
    conflict_type: str = Query(None, description="Filter by conflict type"),
    db: Session = Depends(get_db)
):
    query = db.query(ConflictRecord).filter(ConflictRecord.scenario_id.is_(None))

    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        day_start = datetime.combine(target_date, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        query = query.filter(
            ConflictRecord.detected_at >= day_start,
            ConflictRecord.detected_at < day_end
        )

    if conflict_type:
        query = query.filter(ConflictRecord.conflict_type == conflict_type)

    conflicts = query.order_by(ConflictRecord.detected_at.desc()).all()

    return ConflictListResponse(
        conflicts=[ConflictSchema.from_orm(c) for c in conflicts],
        total=len(conflicts),
    )
