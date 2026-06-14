from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Device, ScheduleEntry
from app.schemas import DeviceCreate, Device as DeviceSchema
import re

router = APIRouter(prefix="/devices", tags=["devices"])


def validate_time_format(time_str: str, field_name: str):
    if not re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field_name} format '{time_str}'. Expected format: HH:MM (e.g., 08:00, 20:00)"
        )


def validate_max_batch_size(max_batch_size: int):
    if max_batch_size is None or max_batch_size < 1:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid max_batch_size: {max_batch_size}. Must be a positive integer (>= 1)."
        )


@router.post("/", response_model=DeviceSchema, status_code=201)
def create_device(device: DeviceCreate, db: Session = Depends(get_db)):
    existing = db.query(Device).filter(Device.name == device.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Device with name '{device.name}' already exists")

    validate_time_format(device.daily_start, "daily_start")
    validate_time_format(device.daily_end, "daily_end")
    validate_max_batch_size(device.max_batch_size)

    db_device = Device(
        name=device.name,
        device_type=device.device_type,
        daily_start=device.daily_start,
        daily_end=device.daily_end,
        max_batch_size=device.max_batch_size,
    )
    db.add(db_device)
    db.commit()
    db.refresh(db_device)
    return db_device


@router.get("/", response_model=List[DeviceSchema])
def list_devices(device_type: str = None, db: Session = Depends(get_db)):
    query = db.query(Device)
    if device_type:
        query = query.filter(Device.device_type == device_type)
    return query.order_by(Device.id).all()


@router.get("/{device_id}", response_model=DeviceSchema)
def get_device(device_id: int, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.put("/{device_id}", response_model=DeviceSchema)
def update_device(device_id: int, device: DeviceCreate, db: Session = Depends(get_db)):
    db_device = db.query(Device).filter(Device.id == device_id).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Device not found")

    existing = db.query(Device).filter(Device.name == device.name, Device.id != device_id).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Device with name '{device.name}' already exists")

    validate_time_format(device.daily_start, "daily_start")
    validate_time_format(device.daily_end, "daily_end")
    validate_max_batch_size(device.max_batch_size)

    db_device.name = device.name
    db_device.device_type = device.device_type
    db_device.daily_start = device.daily_start
    db_device.daily_end = device.daily_end
    db_device.max_batch_size = device.max_batch_size
    db.commit()
    db.refresh(db_device)
    return db_device


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int, db: Session = Depends(get_db)):
    db_device = db.query(Device).filter(Device.id == device_id).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="Device not found")

    scheduled_count = db.query(ScheduleEntry).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.scenario_id.is_(None)
    ).count()
    if scheduled_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete device '{db_device.name}': there are {scheduled_count} scheduled operations on it. Cancel related orders first."
        )

    db.delete(db_device)
    db.commit()
    return None
