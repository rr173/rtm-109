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


class StepMaterialRequirementBase(BaseModel):
    material_id: int
    quantity: int


class StepMaterialRequirementCreate(StepMaterialRequirementBase):
    pass


class StepMaterialRequirement(StepMaterialRequirementBase):
    id: int
    step_id: int
    material_name: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessStepCreate(ProcessStepBase):
    material_requirements: List[StepMaterialRequirementCreate] = []


class ProcessStep(ProcessStepBase):
    id: int
    route_id: int
    material_requirements: List[StepMaterialRequirement] = []

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


class MaintenancePlanBase(BaseModel):
    day_of_week: int
    start_time: str
    end_time: str
    description: Optional[str] = None


class MaintenancePlanCreate(MaintenancePlanBase):
    device_id: int


class MaintenancePlanUpdate(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    description: Optional[str] = None


class MaintenancePlan(MaintenancePlanBase):
    id: int
    device_id: int

    class Config:
        from_attributes = True


class TimelineEntry(BaseModel):
    type: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    order_no: Optional[str] = None
    step_name: Optional[str] = None
    is_locked: Optional[bool] = None


class DayTimeline(BaseModel):
    date: str
    entries: List[TimelineEntry]


class DeviceTimelineResponse(BaseModel):
    device_id: int
    device_name: str
    device_type: str
    days: List[DayTimeline]


class MaterialBase(BaseModel):
    name: str
    unit: str
    description: Optional[str] = None


class MaterialCreate(MaterialBase):
    initial_quantity: int = Field(0, ge=0, description="初始库存数量，不能为负数")


class MaterialUpdate(BaseModel):
    name: Optional[str] = None
    unit: Optional[str] = None
    description: Optional[str] = None


class Material(MaterialBase):
    id: int
    total_quantity: int

    class Config:
        from_attributes = True


class MaterialInventoryResponse(BaseModel):
    id: int
    name: str
    unit: str
    total_quantity: int
    locked_quantity: int
    available_quantity: int
    description: Optional[str] = None


class StockInRequest(BaseModel):
    quantity: int = Field(..., gt=0, description="入库数量，必须大于0")
    remark: Optional[str] = None


class MaterialLockDetail(BaseModel):
    id: int
    order_id: int
    step_id: int
    step_name: str
    material_id: int
    material_name: str
    quantity: int
    unit: str
    created_at: datetime


class OrderMaterialLocksResponse(BaseModel):
    order_id: int
    order_no: str
    locks: List[MaterialLockDetail]
    total_locked_quantity: int = 0
