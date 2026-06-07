from pydantic import BaseModel, Field
from datetime import datetime, time
from typing import List, Optional


class DeviceBase(BaseModel):
    name: str
    device_type: str
    daily_start: str = "08:00"
    daily_end: str = "20:00"


class DeviceCreate(DeviceBase):
    pass


class Device(DeviceBase):
    id: int

    class Config:
        from_attributes = True


class ProcessStepBase(BaseModel):
    step_order: int
    step_name: str
    device_type: str
    duration_minutes: int
    min_gap_after: int = 0


class ProcessStepCreate(ProcessStepBase):
    pass


class ProcessStep(ProcessStepBase):
    id: int
    route_id: int

    class Config:
        from_attributes = True


class ProcessRouteBase(BaseModel):
    product_name: str


class ProcessRouteCreate(ProcessRouteBase):
    steps: List[ProcessStepCreate]


class ProcessRoute(ProcessRouteBase):
    id: int
    steps: List[ProcessStep] = []

    class Config:
        from_attributes = True


class ScheduleEntryBase(BaseModel):
    step_id: int
    device_id: int
    step_order: int
    step_name: str
    start_time: datetime
    end_time: datetime


class ScheduleEntry(ScheduleEntryBase):
    id: int
    order_id: int

    class Config:
        from_attributes = True


class WorkOrderBase(BaseModel):
    order_no: str
    product_name: str
    expected_start_time: datetime
    deadline: datetime


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrder(WorkOrderBase):
    id: int
    status: str
    is_locked: bool
    bottleneck_step: Optional[str] = None
    schedule_entries: List[ScheduleEntry] = []

    class Config:
        from_attributes = True


class WorkOrderScheduleResult(BaseModel):
    success: bool
    order_id: int
    order_no: str
    status: str
    bottleneck_step: Optional[str] = None
    message: Optional[str] = None
    schedule_entries: List[ScheduleEntry] = []


class ScheduleGanttEntry(BaseModel):
    id: int
    order_no: str
    step_name: str
    start_time: datetime
    end_time: datetime
    is_locked: bool


class DeviceGantt(BaseModel):
    device_id: int
    device_name: str
    device_type: str
    entries: List[ScheduleGanttEntry]


class GanttResponse(BaseModel):
    date: str
    devices: List[DeviceGantt]


class ConflictRecord(BaseModel):
    id: int
    order_id: int
    conflict_type: str
    description: str
    detected_at: datetime

    class Config:
        from_attributes = True


class ConflictListResponse(BaseModel):
    conflicts: List[ConflictRecord]
    total: int


class LockToggleResponse(BaseModel):
    success: bool
    order_id: int
    is_locked: bool
    message: str
