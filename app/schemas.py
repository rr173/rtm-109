from pydantic import BaseModel, Field
from datetime import datetime, time, date
from typing import List, Optional, Dict


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


class FixtureTypeBase(BaseModel):
    name: str
    description: Optional[str] = None
    turn_over_minutes: int = 0


class FixtureTypeCreate(FixtureTypeBase):
    pass


class FixtureTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    turn_over_minutes: Optional[int] = None


class FixtureType(FixtureTypeBase):
    id: int

    class Config:
        from_attributes = True


class FixtureBase(BaseModel):
    code: str
    fixture_type_id: int
    compatible_device_types: str
    status: str = "available"


class FixtureCreate(FixtureBase):
    pass


class FixtureUpdate(BaseModel):
    code: Optional[str] = None
    fixture_type_id: Optional[int] = None
    compatible_device_types: Optional[str] = None
    status: Optional[str] = None


class Fixture(FixtureBase):
    id: int
    fixture_type_name: Optional[str] = None

    class Config:
        from_attributes = True


class FixtureOccupancyEntry(BaseModel):
    schedule_entry_id: int
    order_id: Optional[int] = None
    order_no: Optional[str] = None
    sub_batch_id: Optional[int] = None
    sub_batch_no: Optional[str] = None
    step_order: int
    step_name: str
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    turn_over_end_time: Optional[datetime] = None
    fixture_release_time: Optional[datetime] = None
    status: str
    is_producing: bool
    is_in_turn_over: bool


class FixtureTimelineEntry(BaseModel):
    type: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    order_no: Optional[str] = None
    sub_batch_no: Optional[str] = None
    step_name: Optional[str] = None


class FixtureDayTimeline(BaseModel):
    date: str
    entries: List[FixtureTimelineEntry]


class FixtureTimelineResponse(BaseModel):
    fixture_id: int
    fixture_code: str
    fixture_type_name: Optional[str] = None
    status: str
    current_occupancy: Optional[FixtureOccupancyEntry] = None
    days: List[FixtureDayTimeline]


class ProcessStepBase(BaseModel):
    step_order: int
    step_name: str
    device_type: str
    duration_minutes: int
    min_gap_after: int = 0
    fixture_type_id: Optional[int] = None
    is_outsource: bool = False
    outsource_process_type: Optional[str] = None
    required_skill_id: Optional[int] = None
    required_skill_level: Optional[int] = None


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


class StepOutsourcingConfigBase(BaseModel):
    factory_id: int
    priority: int = 0
    is_preferred: bool = False


class StepOutsourcingConfigCreate(StepOutsourcingConfigBase):
    pass


class StepOutsourcingConfig(StepOutsourcingConfigBase):
    id: int
    step_id: int
    factory_name: Optional[str] = None
    factory_code: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessStepCreate(ProcessStepBase):
    material_requirements: List[StepMaterialRequirementCreate] = []
    outsourcing_configs: List[StepOutsourcingConfigCreate] = []


class ProcessStep(ProcessStepBase):
    id: int
    route_id: int
    material_requirements: List[StepMaterialRequirement] = []
    fixture_type_name: Optional[str] = None
    outsourcing_configs: List[StepOutsourcingConfig] = []
    required_skill_name: Optional[str] = None
    required_skill_code: Optional[str] = None

    class Config:
        from_attributes = True


class ProcessRouteBase(BaseModel):
    product_name: str
    product_family_id: Optional[int] = None


class ProcessRouteCreate(ProcessRouteBase):
    steps: List[ProcessStepCreate]


class ProcessRoute(ProcessRouteBase):
    id: int
    steps: List[ProcessStep] = []
    product_family_name: Optional[str] = None

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
    fixture_id: Optional[int] = None
    fixture_code: Optional[str] = None
    fixture_turn_over_end_time: Optional[datetime] = None
    changeover_start_time: Optional[datetime] = None
    changeover_end_time: Optional[datetime] = None
    changeover_minutes: int = 0
    changeover_type: Optional[str] = None
    prev_product_name: Optional[str] = None
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    operator_no: Optional[str] = None

    class Config:
        from_attributes = True


class WorkOrderBase(BaseModel):
    order_no: str
    product_name: str
    expected_start_time: datetime
    deadline: datetime
    total_quantity: int = 1
    priority: int = 5


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
    bottleneck_type: Optional[str] = None
    bottleneck_fixture_type: Optional[str] = None
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
    entry_type: str = "production"
    changeover_start_time: Optional[datetime] = None
    changeover_end_time: Optional[datetime] = None
    changeover_minutes: int = 0
    changeover_type: Optional[str] = None
    prev_product_name: Optional[str] = None


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
    changeover_type: Optional[str] = None
    prev_product_name: Optional[str] = None


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
    priority: int = 5
    last_insertion_at: Optional[datetime] = None
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


class ScenarioBase(BaseModel):
    name: str
    description: Optional[str] = None


class ScenarioCreate(ScenarioBase):
    pass


class ScenarioUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class Scenario(ScenarioBase):
    id: int
    status: str
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None
    published_by: Optional[str] = None
    baseline_hash: Optional[str] = None
    baseline_timestamp: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScenarioListResponse(BaseModel):
    scenarios: List[Scenario]
    total: int


class ScenarioAuditLogBase(BaseModel):
    action: str
    operator: Optional[str] = None
    details: Optional[str] = None


class ScenarioAuditLog(ScenarioAuditLogBase):
    id: int
    scenario_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScenarioAuditLogListResponse(BaseModel):
    logs: List[ScenarioAuditLog]
    total: int


class ScenarioMaintenanceOverrideBase(BaseModel):
    device_id: int
    override_type: str
    maintenance_plan_id: Optional[int] = None
    new_start_time: Optional[str] = None
    new_end_time: Optional[str] = None
    new_day_of_week: Optional[int] = None
    description: Optional[str] = None


class ScenarioMaintenanceOverrideCreate(ScenarioMaintenanceOverrideBase):
    pass


class ScenarioMaintenanceOverride(ScenarioMaintenanceOverrideBase):
    id: int
    scenario_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScenarioDeviceOverrideBase(BaseModel):
    device_id: int
    override_type: str
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    reason: Optional[str] = None


class ScenarioDeviceOverrideCreate(ScenarioDeviceOverrideBase):
    pass


class ScenarioDeviceOverride(ScenarioDeviceOverrideBase):
    id: int
    scenario_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScenarioFixtureOverrideBase(BaseModel):
    override_type: str
    fixture_type_id: Optional[int] = None
    fixture_id: Optional[int] = None
    quantity_change: int = 0
    temp_fixture_code: Optional[str] = None
    temp_status: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    reason: Optional[str] = None


class ScenarioFixtureOverrideCreate(ScenarioFixtureOverrideBase):
    pass


class ScenarioFixtureOverride(ScenarioFixtureOverrideBase):
    id: int
    scenario_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class DelayedOrderDiff(BaseModel):
    order_id: int
    order_no: str
    original_end_time: datetime
    scenario_end_time: datetime
    delay_minutes: int
    affected_step: Optional[str] = None


class DeviceLoadDiff(BaseModel):
    device_id: int
    device_name: str
    original_scheduled_minutes: int
    scenario_scheduled_minutes: int
    load_change_minutes: int
    load_change_percent: float


class OverdueOrderDiff(BaseModel):
    order_id: int
    order_no: str
    deadline: datetime
    original_end_time: Optional[datetime] = None
    scenario_end_time: Optional[datetime] = None
    originally_overdue: bool
    scenario_overdue: bool
    original_overdue_minutes: int
    scenario_overdue_minutes: int
    overdue_change: int


class ScenarioDiffResponse(BaseModel):
    scenario_id: int
    scenario_name: str
    baseline_unchanged: bool
    delayed_orders: List[DelayedOrderDiff]
    device_load_changes: List[DeviceLoadDiff]
    overdue_orders: List[OverdueOrderDiff]
    total_delayed: int
    total_devices_changed: int
    total_overdue_changed: int


class ScenarioConstraintCheckResult(BaseModel):
    can_publish: bool
    baseline_matches: bool
    baseline_message: str
    constraint_violations: List[str]
    active_conflicts_count: int


class ScenarioPublishResponse(BaseModel):
    success: bool
    message: str
    scenario_id: int
    published_at: Optional[datetime] = None
    constraints: Optional[ScenarioConstraintCheckResult] = None


class ScenarioUrgentOrderRequest(BaseModel):
    order_no: str
    product_name: str
    expected_start_time: datetime
    deadline: datetime
    total_quantity: int = 1
    priority: str = "high"


SubBatchScheduleResult.model_rebuild()
ProgressReportResponse.model_rebuild()


class ProductFamilyBase(BaseModel):
    name: str
    description: Optional[str] = None


class ProductFamilyCreate(ProductFamilyBase):
    pass


class ProductFamilyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProductFamily(ProductFamilyBase):
    id: int

    class Config:
        from_attributes = True


class ChangeoverRuleBase(BaseModel):
    device_type: str
    device_id: Optional[int] = None
    from_product_family_id: Optional[int] = None
    to_product_family_id: Optional[int] = None
    from_product_name: Optional[str] = None
    to_product_name: Optional[str] = None
    changeover_minutes: int
    changeover_type: str = "cross_family"
    description: Optional[str] = None


class ChangeoverRuleCreate(ChangeoverRuleBase):
    pass


class ChangeoverRuleUpdate(BaseModel):
    device_type: Optional[str] = None
    device_id: Optional[int] = None
    from_product_family_id: Optional[int] = None
    to_product_family_id: Optional[int] = None
    from_product_name: Optional[str] = None
    to_product_name: Optional[str] = None
    changeover_minutes: Optional[int] = None
    changeover_type: Optional[str] = None
    description: Optional[str] = None


class ChangeoverRule(ChangeoverRuleBase):
    id: int
    from_product_family_name: Optional[str] = None
    to_product_family_name: Optional[str] = None
    device_name: Optional[str] = None

    class Config:
        from_attributes = True


class ChangeoverRuleListResponse(BaseModel):
    rules: List[ChangeoverRule]
    total: int


class OutsourcingCapabilityBase(BaseModel):
    process_type: str
    base_duration_minutes: int = 60
    duration_per_unit_minutes: int = 10
    efficiency_factor: int = 100
    min_batch_quantity: int = 1
    max_batch_quantity: Optional[int] = None
    quality_grade: Optional[str] = None
    notes: Optional[str] = None


class OutsourcingCapabilityCreate(OutsourcingCapabilityBase):
    pass


class OutsourcingCapability(OutsourcingCapabilityBase):
    id: int
    factory_id: int

    class Config:
        from_attributes = True


class OutsourcingFactoryBase(BaseModel):
    name: str
    code: str
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    daily_start: str = "08:00"
    daily_end: str = "18:00"
    max_concurrent_jobs: int = 5
    transport_to_minutes: int = 120
    transport_back_minutes: int = 120
    waiting_before_process_minutes: int = 30
    is_active: bool = True
    description: Optional[str] = None


class OutsourcingFactoryCreate(OutsourcingFactoryBase):
    capabilities: List[OutsourcingCapabilityCreate] = []


class OutsourcingFactoryUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    address: Optional[str] = None
    daily_start: Optional[str] = None
    daily_end: Optional[str] = None
    max_concurrent_jobs: Optional[int] = None
    transport_to_minutes: Optional[int] = None
    transport_back_minutes: Optional[int] = None
    waiting_before_process_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


class OutsourcingFactory(OutsourcingFactoryBase):
    id: int
    capabilities: List[OutsourcingCapability] = []

    class Config:
        from_attributes = True


class OutsourcingScheduleEntryBase(BaseModel):
    step_id: int
    factory_id: int
    step_order: int
    step_name: str
    node_type: str
    node_sequence: int
    start_time: datetime
    end_time: datetime
    quantity: int = 1


class OutsourcingScheduleEntry(OutsourcingScheduleEntryBase):
    id: int
    order_id: int
    sub_batch_id: Optional[int] = None
    order_no: Optional[str] = None
    batch_no: Optional[str] = None
    factory_name: Optional[str] = None
    factory_code: Optional[str] = None
    is_completed: bool = False
    actual_start_time: Optional[datetime] = None
    actual_end_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class OutsourcingNodeDetail(BaseModel):
    node_type: str
    node_sequence: int
    start_time: datetime
    end_time: datetime
    description: str


class OrderOutsourcingStatus(BaseModel):
    order_id: int
    order_no: str
    overall_status: str
    current_step_order: Optional[int] = None
    current_step_name: Optional[str] = None
    current_node_type: Optional[str] = None
    current_factory_id: Optional[int] = None
    current_factory_name: Optional[str] = None
    current_node_start: Optional[datetime] = None
    current_node_end: Optional[datetime] = None
    outsourcing_nodes: List[OutsourcingNodeDetail] = []
    total_outsource_steps: int = 0
    completed_outsource_steps: int = 0


class FactoryLoadEntry(BaseModel):
    order_id: int
    order_no: str
    sub_batch_id: Optional[int] = None
    batch_no: Optional[str] = None
    step_order: int
    step_name: str
    node_type: str
    node_sequence: int
    start_time: datetime
    end_time: datetime
    quantity: int
    is_processing_node: bool


class FactoryDailyLoad(BaseModel):
    date: str
    total_scheduled_minutes: int
    available_minutes: int
    utilization_rate: float
    concurrent_peak: int
    max_concurrent: int
    entries: List[FactoryLoadEntry]


class FactoryLoadResponse(BaseModel):
    factory_id: int
    factory_name: str
    factory_code: str
    look_ahead_days: int
    days: List[FactoryDailyLoad]
    in_process_count: int
    queued_count: int
    in_transit_to_count: int
    in_transit_back_count: int
    returned_waiting_count: int


class OutsourcingBottleneck(BaseModel):
    factory_id: int
    factory_name: str
    bottleneck_type: str
    step_name: Optional[str] = None
    order_no: Optional[str] = None
    description: str
    detected_at: datetime


class OrderInsertionRequest(BaseModel):
    order_id: int
    new_priority: int = Field(..., ge=1, le=10, description="新的优先级，1-10，数字越大越紧急")
    operator: Optional[str] = None
    reason: Optional[str] = None


class AffectedOrderInfo(BaseModel):
    order_id: int
    order_no: str
    impact_type: str
    delay_minutes: int = 0
    blocked_reason: Optional[str] = None
    original_start_time: Optional[datetime] = None
    new_start_time: Optional[datetime] = None


class OrderInsertionResponse(BaseModel):
    success: bool
    message: str
    order_id: Optional[int] = None
    order_no: Optional[str] = None
    old_priority: Optional[int] = None
    new_priority: Optional[int] = None
    affected_orders: List[AffectedOrderInfo] = []
    delayed_count: int = 0
    blocked_count: int = 0
    blocked_by_locked: Optional[str] = None


class InsertionHistoryBase(BaseModel):
    order_id: int
    order_no: str
    old_priority: int
    new_priority: int
    operator: Optional[str] = None
    reason: Optional[str] = None
    affected_orders_count: int = 0
    delayed_orders_count: int = 0
    blocked_orders_count: int = 0


class InsertionHistory(InsertionHistoryBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class InsertionHistoryDetail(InsertionHistory):
    affected_orders: List[AffectedOrderInfo] = []


class InsertionHistoryListResponse(BaseModel):
    histories: List[InsertionHistory]
    total: int


class OptimizationObjective:
    MIN_MAKESPAN = "min_makespan"
    MIN_CHANGEOVER = "min_changeover"
    MIN_IDLE = "min_idle"
    ALLOWED = [MIN_MAKESPAN, MIN_CHANGEOVER, MIN_IDLE]


class OptimizationTaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ALLOWED = [PENDING, RUNNING, COMPLETED, CANCELLED, FAILED]


class OptimizationSubmitRequest(BaseModel):
    order_ids: List[int]
    objective: str
    max_duration_seconds: int = Field(300, ge=10, le=3600, description="最大搜索时长，10-3600秒")
    created_by: Optional[str] = None


class OptimizationTrajectoryPoint(BaseModel):
    iteration: int
    objective_value: int
    is_best: bool
    recorded_at: datetime


class OptimizationScheduleEntry(BaseModel):
    order_id: int
    order_no: str
    step_id: int
    step_order: int
    step_name: str
    device_id: int
    device_name: Optional[str] = None
    start_time: datetime
    end_time: datetime
    changeover_minutes: int = 0


class OptimizationMetrics(BaseModel):
    makespan_minutes: int
    total_changeover_minutes: int
    total_idle_minutes: int
    avg_device_utilization: float


class OptimizationImprovement(BaseModel):
    metric_name: str
    baseline_value: int
    optimized_value: int
    improvement_percent: float


class OptimizationTaskResponse(BaseModel):
    id: int
    order_ids: List[int]
    objective: str
    max_duration_seconds: int
    status: str
    explored_count: int
    current_best_value: Optional[int] = None
    baseline_value: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    is_applied: bool
    applied_at: Optional[datetime] = None
    remaining_seconds: Optional[int] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class OptimizationTaskDetailResponse(OptimizationTaskResponse):
    result_schedule: List[OptimizationScheduleEntry] = []
    baseline_schedule: List[OptimizationScheduleEntry] = []
    metrics: Optional[OptimizationMetrics] = None
    baseline_metrics: Optional[OptimizationMetrics] = None
    improvements: List[OptimizationImprovement] = []
    trajectories: List[OptimizationTrajectoryPoint] = []


class OptimizationTaskListResponse(BaseModel):
    tasks: List[OptimizationTaskResponse]
    total: int


class OptimizationApplyRequest(BaseModel):
    operator: Optional[str] = None


class OptimizationApplyResponse(BaseModel):
    success: bool
    message: str
    applied: bool = False
    conflict_reason: Optional[str] = None


class TrialScheduleItem(BaseModel):
    product_name: str
    quantity: int = Field(..., ge=1, description="产品数量，必须大于0")
    expected_delivery_date: datetime = Field(..., description="期望交付日期")


class TrialScheduleRequest(BaseModel):
    items: List[TrialScheduleItem] = Field(..., min_length=1, max_length=50, description="试算工单列表，最多50条")


class TrialScheduleStepEntry(BaseModel):
    step_order: int
    step_name: str
    device_id: int
    device_name: str
    device_type: str
    start_time: datetime
    end_time: datetime
    changeover_minutes: int = 0
    fixture_id: Optional[int] = None
    fixture_code: Optional[str] = None


class TrialScheduleItemResult(BaseModel):
    product_name: str
    quantity: int
    expected_delivery_date: datetime
    can_meet_deadline: bool
    earliest_delivery_time: Optional[datetime] = None
    bottleneck_type: Optional[str] = None
    bottleneck_step: Optional[str] = None
    bottleneck_detail: Optional[str] = None
    schedule_entries: List[TrialScheduleStepEntry] = []


class TrialScheduleResponse(BaseModel):
    success: bool
    message: str
    results: List[TrialScheduleItemResult]


class CapacityReservationLockRequest(BaseModel):
    items: List[TrialScheduleItem] = Field(..., min_length=1, max_length=50, description="试算工单列表，会重新试算后锁定指定索引")
    trial_result_index: int = Field(0, ge=0, description="要锁定的试算结果索引")
    customer_name: Optional[str] = None
    sales_person: Optional[str] = None
    lock_duration_hours: int = Field(24, ge=1, le=168, description="锁定时长(小时)，1-168，默认24小时")


class CapacityReservationSlotInfo(BaseModel):
    id: int
    device_id: int
    device_name: Optional[str] = None
    fixture_id: Optional[int] = None
    fixture_code: Optional[str] = None
    step_order: int
    step_name: str
    start_time: datetime
    end_time: datetime
    fixture_turn_over_end_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class CapacityReservationInfo(BaseModel):
    id: int
    reservation_no: str
    product_name: str
    quantity: int
    customer_name: Optional[str] = None
    sales_person: Optional[str] = None
    status: str
    expire_at: datetime
    created_at: datetime
    released_at: Optional[datetime] = None
    release_reason: Optional[str] = None
    trial_earliest_delivery: Optional[datetime] = None
    trial_expected_delivery: Optional[datetime] = None
    trial_can_meet_deadline: bool = True
    trial_bottleneck_type: Optional[str] = None
    trial_bottleneck_step: Optional[str] = None
    trial_bottleneck_detail: Optional[str] = None
    slots: List[CapacityReservationSlotInfo] = []
    remaining_seconds: Optional[int] = None

    class Config:
        from_attributes = True


class CapacityReservationListResponse(BaseModel):
    reservations: List[CapacityReservationInfo]
    total: int
    active_count: int


class CapacityReservationReleaseRequest(BaseModel):
    reason: Optional[str] = None


class CapacityReservationReleaseResponse(BaseModel):
    success: bool
    message: str
    reservation_id: int
    reservation_no: str


class ReservationBlockerInfo(BaseModel):
    reservation_id: int
    reservation_no: str
    product_name: str
    step_name: str
    step_order: int
    start_time: datetime
    end_time: datetime


class DeliveryPlanItem(BaseModel):
    plan_index: int = Field(..., ge=1, description="批次序号，从1开始")
    planned_quantity: int = Field(..., gt=0, description="本批计划交付数量")
    expected_delivery_date: datetime = Field(..., description="期望交付日期")


class SetDeliveryPlanRequest(BaseModel):
    order_id: int
    plans: List[DeliveryPlanItem] = Field(..., min_length=1, description="交付计划列表")


class DeliveryPlan(BaseModel):
    id: int
    order_id: int
    plan_index: int
    planned_quantity: int
    expected_delivery_date: datetime
    status: str
    actual_delivered_quantity: int = 0
    sub_batch_ids: List[int] = []
    estimated_completion_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeliveryPlanListResponse(BaseModel):
    order_id: int
    order_no: str
    total_quantity: int
    total_planned_quantity: int
    total_delivered_quantity: int
    plans: List[DeliveryPlan]


class BatchDeliveryRequest(BaseModel):
    delivery_plan_id: int
    actual_quantity: int = Field(..., gt=0, description="实际交付数量")
    delivered_at: Optional[datetime] = None
    accepted_by: Optional[str] = None
    remarks: Optional[str] = None


class BatchDeliveryRecord(BaseModel):
    id: int
    order_id: int
    delivery_plan_id: int
    actual_quantity: int
    delivered_at: datetime
    accepted_by: Optional[str] = None
    accepted_at: Optional[datetime] = None
    status: str
    remarks: Optional[str] = None

    class Config:
        from_attributes = True


class BatchDeliveryResponse(BaseModel):
    success: bool
    message: str
    delivery_record: Optional[BatchDeliveryRecord] = None
    plan_status: Optional[str] = None
    remaining_quantity: Optional[int] = None


class DeliveryProgressResponse(BaseModel):
    order_id: int
    order_no: str
    total_quantity: int
    total_planned_batches: int
    delivered_batches: int
    partially_delivered_batches: int
    total_planned_quantity: int
    total_delivered_quantity: int
    delivery_percent: float
    next_batch_plan_index: Optional[int] = None
    next_batch_planned_quantity: Optional[int] = None
    next_batch_expected_date: Optional[datetime] = None
    next_batch_estimated_delivery: Optional[datetime] = None
    next_batch_can_meet_deadline: Optional[bool] = None
    batches_detail: List[Dict] = []


class DeliveryConflictInfo(BaseModel):
    order_id: int
    order_no: str
    delivery_plan_id: int
    plan_index: int
    planned_quantity: int
    expected_delivery_date: datetime
    estimated_completion_time: Optional[datetime] = None
    delay_minutes: int
    delay_human: str


class SkillBase(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    compatible_device_types: str


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    compatible_device_types: Optional[str] = None


class Skill(SkillBase):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmployeeSkillBase(BaseModel):
    skill_id: int
    skill_level: int = 1
    certification_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    notes: Optional[str] = None


class EmployeeSkillCreate(EmployeeSkillBase):
    pass


class EmployeeSkill(EmployeeSkillBase):
    id: int
    employee_id: int
    skill_name: Optional[str] = None
    skill_code: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TeamBase(BaseModel):
    name: str
    description: Optional[str] = None
    leader_id: Optional[int] = None


class TeamCreate(TeamBase):
    pass


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    leader_id: Optional[int] = None


class Team(TeamBase):
    id: int
    leader_name: Optional[str] = None
    employee_count: int = 0
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmployeeBase(BaseModel):
    employee_no: str
    name: str
    team_id: Optional[int] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    status: str = "active"


class EmployeeCreate(EmployeeBase):
    skills: List[EmployeeSkillCreate] = []


class EmployeeUpdate(BaseModel):
    employee_no: Optional[str] = None
    name: Optional[str] = None
    team_id: Optional[int] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None


class Employee(EmployeeBase):
    id: int
    team_name: Optional[str] = None
    skills: List[EmployeeSkill] = []
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EmployeeWithDetails(Employee):
    pass


class ShiftScheduleBase(BaseModel):
    employee_id: int
    effective_date: date
    end_date: Optional[date] = None
    day_0: Optional[str] = None
    day_1: Optional[str] = None
    day_2: Optional[str] = None
    day_3: Optional[str] = None
    day_4: Optional[str] = None
    day_5: Optional[str] = None
    day_6: Optional[str] = None
    status: Optional[str] = "active"
    is_temporary: bool = False
    notes: Optional[str] = None


class ShiftScheduleCreate(ShiftScheduleBase):
    pass


class ShiftScheduleUpdate(BaseModel):
    effective_date: Optional[date] = None
    end_date: Optional[date] = None
    day_0: Optional[str] = None
    day_1: Optional[str] = None
    day_2: Optional[str] = None
    day_3: Optional[str] = None
    day_4: Optional[str] = None
    day_5: Optional[str] = None
    day_6: Optional[str] = None
    status: Optional[str] = None
    is_temporary: Optional[bool] = None
    notes: Optional[str] = None


class ShiftSchedule(ShiftScheduleBase):
    id: int
    employee_name: Optional[str] = None
    employee_no: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ScheduleEntryEmployeeBase(BaseModel):
    schedule_entry_id: int
    employee_id: int
    assignment_type: str = "primary"


class ScheduleEntryEmployeeCreate(ScheduleEntryEmployeeBase):
    pass


class ScheduleEntryEmployee(ScheduleEntryEmployeeBase):
    id: int
    employee_name: Optional[str] = None
    employee_no: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class StaffingCheckResult(BaseModel):
    has_available_staff: bool
    available_employees: List[int] = []
    missing_skill: Optional[str] = None
    missing_skill_level: Optional[int] = None
    shortage_count: int = 0
    detail: Optional[str] = None


class EmployeeTimelineEntry(BaseModel):
    type: str
    start_time: datetime
    end_time: datetime
    description: Optional[str] = None
    order_no: Optional[str] = None
    sub_batch_no: Optional[str] = None
    step_name: Optional[str] = None
    device_name: Optional[str] = None
    shift_type: Optional[str] = None


class EmployeeTimelineDay(BaseModel):
    date: str
    entries: List[EmployeeTimelineEntry]


class EmployeeTimelineResponse(BaseModel):
    employee_id: int
    employee_no: str
    employee_name: str
    days: List[EmployeeTimelineDay]


class TeamSkillCoverage(BaseModel):
    skill_id: int
    skill_name: str
    skill_code: str
    total_employees: int
    employees_by_level: Dict[int, int] = {}


class TeamDailyStatus(BaseModel):
    date: str
    team_id: int
    team_name: str
    total_employees: int
    on_duty_count: int
    on_rest_count: int
    skill_coverage: List[TeamSkillCoverage] = []


class SkillCoverageDetail(BaseModel):
    count: int
    max_level: int


class TeamDailySummary(BaseModel):
    team_id: int
    team_name: str
    check_date: date
    total_employees: int
    on_duty_count: int
    skill_coverage: Dict[str, SkillCoverageDetail] = {}


class DeviceStaffingStatus(BaseModel):
    device_id: int
    device_name: str
    device_type: str
    start_time: datetime
    end_time: datetime
    has_available_operator: bool
    available_operators: List[Dict] = []
    required_skill: Optional[str] = None
    required_skill_level: Optional[int] = None


class AvailableEmployeeInfo(BaseModel):
    employee_id: int
    employee_no: str
    name: str
    skill_level: Optional[int] = None


class DeviceStaffingCheckResult(BaseModel):
    device_id: int
    device_name: str
    check_time: datetime
    end_time: datetime
    has_available_staff: bool
    available_count: int
    available_employees: List[AvailableEmployeeInfo] = []
    missing_skill: Optional[str] = None
    missing_skill_level: Optional[int] = None
    detail: Optional[str] = None


class ScenarioStaffingOverrideBase(BaseModel):
    override_type: str
    employee_id: Optional[int] = None
    skill_id: Optional[int] = None
    shift_schedule_id: Optional[int] = None
    new_shift_type: Optional[str] = None
    new_start_time: Optional[str] = None
    new_end_time: Optional[str] = None
    new_is_rest_day: Optional[bool] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    reason: Optional[str] = None


class ScenarioStaffingOverrideCreate(ScenarioStaffingOverrideBase):
    pass


class ScenarioStaffingOverride(ScenarioStaffingOverrideBase):
    id: int
    scenario_id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BottleneckDetail(BaseModel):
    bottleneck_type: str
    bottleneck_step: Optional[str] = None
    bottleneck_fixture_type: Optional[str] = None
    bottleneck_skill: Optional[str] = None
    bottleneck_skill_level: Optional[int] = None
    message: Optional[str] = None

