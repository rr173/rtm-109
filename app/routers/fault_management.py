from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime
from typing import List, Optional
from app.database import get_db
from app.models import Device, DeviceFault, WorkOrder
from app.schemas import (
    DeviceFaultCreate,
    DeviceFaultResolve,
    DeviceFault as DeviceFaultSchema,
    FaultReportResponse,
    FaultResolveResponse,
    DeviceFaultListResponse,
    MigratedEntry,
    BlockedOrder,
)
from app.scheduler import (
    report_device_fault,
    resolve_device_fault,
    get_device_faults,
    get_active_device_fault,
)

router = APIRouter(prefix="/faults", tags=["fault_management"])


@router.post("/report", response_model=FaultReportResponse, status_code=201)
def report_fault(
    fault_data: DeviceFaultCreate,
    db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == fault_data.device_id).first()
    if not device:
        raise HTTPException(
            status_code=404,
            detail=f"设备 ID {fault_data.device_id} 不存在"
        )
    
    existing_fault = get_active_device_fault(db, fault_data.device_id)
    if existing_fault:
        raise HTTPException(
            status_code=400,
            detail=f"设备 '{device.name}' 已有活跃故障记录 (ID: {existing_fault.id})，不能重复报告"
        )
    
    fault_time = fault_data.fault_time or datetime.now()
    
    if fault_data.expected_recovery_time <= fault_time:
        raise HTTPException(
            status_code=400,
            detail="预计恢复时间必须晚于故障时间"
        )
    
    result = report_device_fault(
        db,
        device_id=fault_data.device_id,
        expected_recovery_time=fault_data.expected_recovery_time,
        fault_time=fault_time,
        description=fault_data.description
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return FaultReportResponse(
        success=result["success"],
        message=result["message"],
        fault_id=result["fault_id"],
        device_id=result["device_id"],
        device_name=result["device_name"],
        fault_time=result["fault_time"],
        expected_recovery_time=result["expected_recovery_time"],
        affected_orders_count=result["affected_orders_count"],
        migrated_entries=[MigratedEntry(**m) for m in result["migrated_entries"]],
        blocked_orders=[BlockedOrder(**b) for b in result["blocked_orders"]],
        cascade_blocked_orders=[BlockedOrder(**b) for b in result["cascade_blocked_orders"]]
    )


@router.post("/{device_id}/resolve", response_model=FaultResolveResponse)
def resolve_fault(
    device_id: int,
    resolve_data: Optional[DeviceFaultResolve] = None,
    db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(
            status_code=404,
            detail=f"设备 ID {device_id} 不存在"
        )
    
    active_fault = get_active_device_fault(db, device_id)
    if not active_fault:
        raise HTTPException(
            status_code=400,
            detail=f"设备 '{device.name}' 没有活跃的故障记录"
        )
    
    actual_recovery_time = resolve_data.actual_recovery_time if resolve_data else None
    
    result = resolve_device_fault(
        db,
        device_id=device_id,
        actual_recovery_time=actual_recovery_time
    )
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    
    return FaultResolveResponse(
        success=result["success"],
        message=result["message"],
        fault_id=result["fault_id"],
        device_id=result["device_id"],
        device_name=result["device_name"],
        status=result["status"],
        resolved_at=result["resolved_at"]
    )


@router.get("/", response_model=DeviceFaultListResponse)
def list_faults(
    device_id: Optional[int] = Query(None, description="按设备ID过滤"),
    status: Optional[str] = Query(None, description="按状态过滤 (active/resolved)"),
    include_resolved: bool = Query(False, description="是否包含已解除的故障"),
    db: Session = Depends(get_db)
):
    faults, total, active_count = get_device_faults(
        db,
        device_id=device_id,
        status=status,
        include_resolved=include_resolved
    )
    
    fault_schemas = []
    for fault in faults:
        device = fault.device
        fault_schema = DeviceFaultSchema(
            id=fault.id,
            device_id=fault.device_id,
            expected_recovery_time=fault.expected_recovery_time,
            description=fault.description,
            fault_time=fault.fault_time,
            actual_recovery_time=fault.actual_recovery_time,
            status=fault.status,
            created_at=fault.created_at,
            resolved_at=fault.resolved_at,
            device_name=device.name if device else None
        )
        fault_schemas.append(fault_schema)
    
    return DeviceFaultListResponse(
        faults=fault_schemas,
        total=total,
        active_count=active_count
    )


@router.get("/{fault_id}", response_model=DeviceFaultSchema)
def get_fault_detail(
    fault_id: int,
    db: Session = Depends(get_db)
):
    fault = db.query(DeviceFault).options(
        joinedload(DeviceFault.device)
    ).filter(DeviceFault.id == fault_id).first()
    
    if not fault:
        raise HTTPException(status_code=404, detail=f"故障记录 ID {fault_id} 不存在")
    
    device = fault.device
    return DeviceFaultSchema(
        id=fault.id,
        device_id=fault.device_id,
        expected_recovery_time=fault.expected_recovery_time,
        description=fault.description,
        fault_time=fault.fault_time,
        actual_recovery_time=fault.actual_recovery_time,
        status=fault.status,
        created_at=fault.created_at,
        resolved_at=fault.resolved_at,
        device_name=device.name if device else None
    )


@router.get("/device/{device_id}/active")
def get_device_active_fault(
    device_id: int,
    db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail=f"设备 ID {device_id} 不存在")
    
    active_fault = get_active_device_fault(db, device_id)
    if not active_fault:
        return {
            "device_id": device_id,
            "device_name": device.name,
            "has_active_fault": False,
            "fault": None
        }
    
    return {
        "device_id": device_id,
        "device_name": device.name,
        "has_active_fault": True,
        "fault": DeviceFaultSchema(
            id=active_fault.id,
            device_id=active_fault.device_id,
            expected_recovery_time=active_fault.expected_recovery_time,
            description=active_fault.description,
            fault_time=active_fault.fault_time,
            actual_recovery_time=active_fault.actual_recovery_time,
            status=active_fault.status,
            created_at=active_fault.created_at,
            resolved_at=active_fault.resolved_at,
            device_name=device.name
        )
    }
