from pydantic import BaseModel, Field
from datetime import datetime, time
from typing import List, Optional


class DeviceBase(BaseModel):
    name: str
    device_type: str
    daily_start: str = "08:00"
    daily_end: str = "20:00"
    max_batch_size: int = 1


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


class SubBatchBase(BaseModel):
    batch_no: str
    quantity: int
    status: str = "pending"


class SubBatch(SubBatchBase):
    id: int
    order_id: int
    actual_start_time: Optional[datetime] = None
    actual_end_time: Optional[datetime] = None
    parent_sub_batch_id: Optional[int] = None
    is_replenishment: bool = False
    replenish_level: int = 0
    replenish_from_step: Optional[int] = None

    class Config:
        from_attributes = True


class StepProgressBase(BaseModel):
    step_order: int
    step_name: str
    is_completed: bool = False
    actual_completion_time: Optional[datetime] = None
    good_quantity: int = 0
    scrap_quantity: int = 0


class StepProgress(StepProgressBase):
    id: int
    sub_batch_id: int
    step_id: int
    reported_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProgressReportRequest(BaseModel):
    sub_batch_id: Optional[int] = None
    order_id: Optional[int] = None
    step_order: int
    actual_completion_time: datetime
    good_quantity: int = Field(..., ge=0, description="良品数量，不能为负数")


class ProgressReportResponse(BaseModel):
    success: bool
    message: str
    sub_batch_id: int
    step_order: int
    good_quantity: int
    scrap_quantity: int
    is_completed: bool
    replenishment_created: bool = False
    replenishment_sub_batch_id: Optional[int] = None
    replenishment_batch_no: Optional[str] = None
    order_progress: Optional["WorkOrderSummary"] = None


class SubBatchScheduleResult(BaseModel):
    sub_batch_id: int
    batch_no: str
    quantity: int
    status: str
    is_replenishment: bool = False
    replenish_level: int = 0
    parent_sub_batch_id: Optional[int] = None
    schedule_entries: List["ScheduleEntry"] = []
    step_progresses: List[StepProgress] = []


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
    sub_batch_id: Optional[int] = None
    batch_no: Optional[str] = None
    device_name: Optional[str] = None

    class Config:
        from_attributes = True


class WorkOrderBase(BaseModel):
    order_no: str
    product_name: str
    expected_start_time: datetime
    deadline: datetime
    total_quantity: int = 1


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrderScheduleResult(BaseModel):
    success: bool
    order_id: int
    order_no: str
    status: str
    is_split: bool = False
    total_sub_batches: int = 0
    bottleneck_step: Optional[str] = None
    message: Optional[str] = None
    schedule_entries: List[ScheduleEntry] = []
    sub_batches: List[SubBatchScheduleResult] = []


SubBatchScheduleResult.model_rebuild()


class ScheduleGanttEntry(BaseModel):
    id: int
    order_no: str
    batch_no: Optional[str] = None
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


class IdlePeriod(BaseModel):
    start_time: datetime
    end_time: datetime
    duration_minutes: int


class DeviceEfficiency(BaseModel):
    device_id: int
    device_name: str
    device_type: str
    utilization_rate: float
    scheduled_minutes: int
    available_minutes: int
    idle_periods: List[IdlePeriod]
    avg_waiting_time_minutes: float


class DeviceTypeEfficiency(BaseModel):
    device_type: str
    device_count: int
    avg_utilization_rate: float
    max_utilization_diff: float
    devices: List[DeviceEfficiency]


class EfficiencyStatsRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    group_by_type: bool = True


class EfficiencyStatsResponse(BaseModel):
    start_time: datetime
    end_time: datetime
    total_devices: int
    device_efficiencies: List[DeviceEfficiency]
    device_type_efficiencies: List[DeviceTypeEfficiency]


class SimulatedWorkOrder(BaseModel):
    product_name: str
    quantity: int = Field(..., ge=1, description="产品数量，必须大于0")
    expected_start_time: datetime


class HighRiskDeviceType(BaseModel):
    device_type: str
    date: str
    utilization_rate: float
    scheduled_minutes: int
    available_minutes: int


class FailedSimulatedOrder(BaseModel):
    product_name: str
    quantity: int
    expected_start_time: datetime
    reason: str
    bottleneck_step: Optional[str] = None


class DeviceRecommendation(BaseModel):
    device_type: str
    recommended_count: int
    reason: str


class BottleneckPredictionRequest(BaseModel):
    future_days: int = Field(..., ge=1, le=365, description="预测未来N天")
    simulated_orders: List[SimulatedWorkOrder] = Field(..., description="模拟工单列表，最多50条")


class SimulatedScheduleEntry(BaseModel):
    step_order: int
    step_name: str
    device_id: int
    device_name: str
    device_type: str
    start_time: datetime
    end_time: datetime


class SimulatedOrderResult(BaseModel):
    product_name: str
    quantity: int
    expected_start_time: datetime
    scheduled: bool
    schedule_entries: List[SimulatedScheduleEntry] = []
    failure_reason: Optional[str] = None
    bottleneck_step: Optional[str] = None


class BottleneckPredictionResponse(BaseModel):
    future_days: int
    total_simulated_orders: int
    high_risk_device_types: List[HighRiskDeviceType]
    failed_orders: List[FailedSimulatedOrder]
    device_recommendations: List[DeviceRecommendation]
    simulated_results: List[SimulatedOrderResult]


class WorkOrder(WorkOrderBase):
    id: int
    status: str
    is_locked: bool
    bottleneck_step: Optional[str] = None
    is_split: bool = False
    total_sub_batches: int = 0
    is_blocked: bool = False
    blocked_reason: Optional[str] = None
    schedule_entries: List[ScheduleEntry] = []
    sub_batches: List[SubBatch] = []

    class Config:
        from_attributes = True


class WorkOrderSummary(BaseModel):
    order_id: int
    order_no: str
    product_name: str
    total_quantity: int
    status: str
    is_blocked: bool
    blocked_reason: Optional[str]
    is_split: bool
    total_sub_batches: int
    completed_sub_batches: int
    total_steps: int
    completed_steps: int
    progress_percent: float
    expected_start_time: datetime
    deadline: datetime
    estimated_completion_time: Optional[datetime] = None
    bottleneck_step: Optional[str] = None


class DeviceFaultBase(BaseModel):
    device_id: int
    expected_recovery_time: datetime
    description: Optional[str] = None


class DeviceFaultCreate(DeviceFaultBase):
    fault_time: Optional[datetime] = None


class DeviceFaultResolve(BaseModel):
    actual_recovery_time: Optional[datetime] = None


class DeviceFault(DeviceFaultBase):
    id: int
    fault_time: datetime
    actual_recovery_time: Optional[datetime] = None
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    device_name: Optional[str] = None

    class Config:
        from_attributes = True


class MigratedEntry(BaseModel):
    schedule_entry_id: int
    order_id: int
    order_no: str
    sub_batch_id: Optional[int]
    sub_batch_no: Optional[str]
    step_order: int
    step_name: str
    from_device_id: int
    from_device_name: str
    to_device_id: int
    to_device_name: str
    original_start_time: datetime
    original_end_time: datetime
    new_start_time: datetime
    new_end_time: datetime


class BlockedOrder(BaseModel):
    order_id: int
    order_no: str
    blocked_reason: str
    affected_step: Optional[str] = None
    affected_sub_batch: Optional[str] = None


class FaultReportResponse(BaseModel):
    success: bool
    message: str
    fault_id: int
    device_id: int
    device_name: str
    fault_time: datetime
    expected_recovery_time: datetime
    affected_orders_count: int
    migrated_entries: List[MigratedEntry]
    blocked_orders: List[BlockedOrder]
    cascade_blocked_orders: List[BlockedOrder]


class FaultResolveResponse(BaseModel):
    success: bool
    message: str
    fault_id: int
    device_id: int
    device_name: str
    status: str
    resolved_at: datetime


class DeviceFaultListResponse(BaseModel):
    faults: List[DeviceFault]
    total: int
    active_count: int


SubBatchScheduleResult.model_rebuild()
ProgressReportResponse.model_rebuild()
