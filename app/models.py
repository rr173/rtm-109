from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean, Time
from sqlalchemy.orm import relationship
from app.database import Base
import datetime


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    device_type = Column(String, index=True, nullable=False)
    daily_start = Column(String, nullable=False, default="08:00")
    daily_end = Column(String, nullable=False, default="20:00")
    max_batch_size = Column(Integer, nullable=False, default=1)

    schedule_entries = relationship(
        "ScheduleEntry",
        back_populates="device",
        foreign_keys="ScheduleEntry.device_id"
    )
    schedule_entries_migrated_from = relationship(
        "ScheduleEntry",
        foreign_keys="ScheduleEntry.migrated_from_device_id"
    )
    maintenance_plans = relationship("MaintenancePlan", back_populates="device", cascade="all, delete-orphan")


class MaintenancePlan(Base):
    __tablename__ = "maintenance_plans"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    description = Column(String, nullable=True)

    device = relationship("Device", back_populates="maintenance_plans")


class ProductFamily(Base):
    __tablename__ = "product_families"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)

    routes = relationship("ProcessRoute", back_populates="product_family")
    from_changeover_rules = relationship(
        "ChangeoverRule",
        foreign_keys="ChangeoverRule.from_product_family_id",
        back_populates="from_product_family"
    )
    to_changeover_rules = relationship(
        "ChangeoverRule",
        foreign_keys="ChangeoverRule.to_product_family_id",
        back_populates="to_product_family"
    )


class ChangeoverRule(Base):
    __tablename__ = "changeover_rules"

    id = Column(Integer, primary_key=True, index=True)
    device_type = Column(String, index=True, nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True, index=True)
    from_product_family_id = Column(Integer, ForeignKey("product_families.id"), nullable=True, index=True)
    to_product_family_id = Column(Integer, ForeignKey("product_families.id"), nullable=True, index=True)
    from_product_name = Column(String, nullable=True)
    to_product_name = Column(String, nullable=True)
    changeover_minutes = Column(Integer, nullable=False)
    changeover_type = Column(String, nullable=False, default="cross_family")
    description = Column(String, nullable=True)

    device = relationship("Device")
    from_product_family = relationship(
        "ProductFamily",
        foreign_keys=[from_product_family_id],
        back_populates="from_changeover_rules"
    )
    to_product_family = relationship(
        "ProductFamily",
        foreign_keys=[to_product_family_id],
        back_populates="to_changeover_rules"
    )


class ProcessRoute(Base):
    __tablename__ = "process_routes"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String, unique=True, index=True, nullable=False)
    product_family_id = Column(Integer, ForeignKey("product_families.id"), nullable=True, index=True)

    steps = relationship("ProcessStep", back_populates="route", order_by="ProcessStep.step_order")
    product_family = relationship("ProductFamily", back_populates="routes")


class FixtureType(Base):
    __tablename__ = "fixture_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
    turn_over_minutes = Column(Integer, nullable=False, default=0)

    fixtures = relationship("Fixture", back_populates="fixture_type", cascade="all, delete-orphan")
    step_requirements = relationship("ProcessStep", back_populates="fixture_type")


class Fixture(Base):
    __tablename__ = "fixtures"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    fixture_type_id = Column(Integer, ForeignKey("fixture_types.id"), nullable=False)
    compatible_device_types = Column(String, nullable=False)
    status = Column(String, default="available")

    fixture_type = relationship("FixtureType", back_populates="fixtures")
    schedule_entries = relationship("ScheduleEntry", back_populates="fixture")


class ProcessStep(Base):
    __tablename__ = "process_steps"

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("process_routes.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    device_type = Column(String, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    min_gap_after = Column(Integer, default=0)
    fixture_type_id = Column(Integer, ForeignKey("fixture_types.id"), nullable=True)
    is_outsource = Column(Boolean, default=False)
    outsource_process_type = Column(String, nullable=True)

    route = relationship("ProcessRoute", back_populates="steps")
    material_requirements = relationship("StepMaterialRequirement", back_populates="step", cascade="all, delete-orphan")
    fixture_type = relationship("FixtureType", back_populates="step_requirements")
    outsourcing_configs = relationship("StepOutsourcingConfig", back_populates="step", cascade="all, delete-orphan")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String, index=True, nullable=False)
    product_name = Column(String, nullable=False)
    expected_start_time = Column(DateTime, nullable=False)
    deadline = Column(DateTime, nullable=False)
    status = Column(String, default="pending")
    is_locked = Column(Boolean, default=False)
    priority = Column(Integer, default=5)
    last_insertion_at = Column(DateTime, nullable=True)
    bottleneck_step = Column(String, nullable=True)
    total_quantity = Column(Integer, nullable=False, default=1)
    is_split = Column(Boolean, default=False)
    total_sub_batches = Column(Integer, default=0)
    is_blocked = Column(Boolean, default=False)
    blocked_reason = Column(String, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)
    source_order_id = Column(Integer, nullable=True)

    schedule_entries = relationship("ScheduleEntry", back_populates="order", cascade="all, delete-orphan")
    sub_batches = relationship("SubBatch", back_populates="order", cascade="all, delete-orphan")
    insertion_histories = relationship("InsertionHistory", back_populates="order", cascade="all, delete-orphan")


class SubBatch(Base):
    __tablename__ = "sub_batches"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    batch_no = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, default="pending")
    actual_start_time = Column(DateTime, nullable=True)
    actual_end_time = Column(DateTime, nullable=True)
    parent_sub_batch_id = Column(Integer, ForeignKey("sub_batches.id"), nullable=True)
    is_replenishment = Column(Boolean, default=False)
    replenish_level = Column(Integer, default=0)
    replenish_from_step = Column(Integer, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)
    source_sub_batch_id = Column(Integer, nullable=True)

    order = relationship("WorkOrder", back_populates="sub_batches")
    schedule_entries = relationship("ScheduleEntry", back_populates="sub_batch", cascade="all, delete-orphan")
    step_progresses = relationship("SubBatchStepProgress", back_populates="sub_batch", cascade="all, delete-orphan")
    parent_sub_batch = relationship("SubBatch", remote_side=[id], back_populates="replenishment_children")
    replenishment_children = relationship("SubBatch", back_populates="parent_sub_batch")


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    sub_batch_id = Column(Integer, ForeignKey("sub_batches.id"), nullable=True)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=True)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    is_completed = Column(Boolean, default=False)
    actual_completion_time = Column(DateTime, nullable=True)
    migrated_from_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    is_migrated = Column(Boolean, default=False)
    fixture_turn_over_end_time = Column(DateTime, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)
    source_schedule_entry_id = Column(Integer, nullable=True)
    changeover_start_time = Column(DateTime, nullable=True)
    changeover_end_time = Column(DateTime, nullable=True)
    changeover_minutes = Column(Integer, default=0)
    changeover_type = Column(String, nullable=True)
    prev_product_name = Column(String, nullable=True)

    order = relationship("WorkOrder", back_populates="schedule_entries")
    sub_batch = relationship("SubBatch", back_populates="schedule_entries")
    device = relationship("Device", back_populates="schedule_entries", foreign_keys=[device_id])
    fixture = relationship("Fixture", back_populates="schedule_entries")
    migrated_from_device = relationship(
        "Device",
        foreign_keys=[migrated_from_device_id],
        overlaps="schedule_entries_migrated_from"
    )


class SubBatchStepProgress(Base):
    __tablename__ = "sub_batch_step_progress"

    id = Column(Integer, primary_key=True, index=True)
    sub_batch_id = Column(Integer, ForeignKey("sub_batches.id"), nullable=False)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False)
    is_completed = Column(Boolean, default=False)
    actual_completion_time = Column(DateTime, nullable=True)
    good_quantity = Column(Integer, default=0)
    scrap_quantity = Column(Integer, default=0)
    reported_at = Column(DateTime, default=datetime.datetime.utcnow)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)

    sub_batch = relationship("SubBatch", back_populates="step_progresses")


class ConflictRecord(Base):
    __tablename__ = "conflict_records"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    conflict_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    detected_at = Column(DateTime, default=datetime.datetime.utcnow)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)


class Material(Base):
    __tablename__ = "materials"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    unit = Column(String, nullable=False)
    total_quantity = Column(Integer, default=0)
    description = Column(String, nullable=True)

    step_requirements = relationship("StepMaterialRequirement", back_populates="material")
    locks = relationship("MaterialLock", back_populates="material")


class StepMaterialRequirement(Base):
    __tablename__ = "step_material_requirements"

    id = Column(Integer, primary_key=True, index=True)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)

    step = relationship("ProcessStep", back_populates="material_requirements")
    material = relationship("Material", back_populates="step_requirements")


class MaterialLock(Base):
    __tablename__ = "material_locks"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)

    material = relationship("Material", back_populates="locks")
    order = relationship("WorkOrder")


class DeviceFault(Base):
    __tablename__ = "device_faults"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    fault_time = Column(DateTime, nullable=False)
    expected_recovery_time = Column(DateTime, nullable=False)
    actual_recovery_time = Column(DateTime, nullable=True)
    status = Column(String, default="active", nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)

    device = relationship("Device")


class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    description = Column(String, nullable=True)
    status = Column(String, default="draft", nullable=False)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    published_at = Column(DateTime, nullable=True)
    published_by = Column(String, nullable=True)
    baseline_hash = Column(String, nullable=True)
    baseline_timestamp = Column(DateTime, nullable=True)

    work_orders = relationship(
        "WorkOrder",
        primaryjoin="and_(foreign(WorkOrder.scenario_id)==Scenario.id)",
        overlaps="work_orders"
    )
    sub_batches = relationship(
        "SubBatch",
        primaryjoin="and_(foreign(SubBatch.scenario_id)==Scenario.id)",
        overlaps="sub_batches"
    )
    schedule_entries = relationship(
        "ScheduleEntry",
        primaryjoin="and_(foreign(ScheduleEntry.scenario_id)==Scenario.id)",
        overlaps="schedule_entries"
    )
    conflict_records = relationship(
        "ConflictRecord",
        primaryjoin="and_(foreign(ConflictRecord.scenario_id)==Scenario.id)",
        overlaps="conflict_records"
    )
    material_locks = relationship(
        "MaterialLock",
        primaryjoin="and_(foreign(MaterialLock.scenario_id)==Scenario.id)",
        overlaps="material_locks"
    )
    device_faults = relationship(
        "DeviceFault",
        primaryjoin="and_(foreign(DeviceFault.scenario_id)==Scenario.id)",
        overlaps="device_faults"
    )
    maintenance_overrides = relationship(
        "ScenarioMaintenanceOverride",
        back_populates="scenario",
        cascade="all, delete-orphan"
    )
    device_overrides = relationship(
        "ScenarioDeviceOverride",
        back_populates="scenario",
        cascade="all, delete-orphan"
    )
    fixture_overrides = relationship(
        "ScenarioFixtureOverride",
        back_populates="scenario",
        cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "ScenarioAuditLog",
        back_populates="scenario",
        cascade="all, delete-orphan"
    )
    outsourcing_factories = relationship(
        "OutsourcingFactory",
        primaryjoin="and_(foreign(OutsourcingFactory.scenario_id)==Scenario.id)"
    )
    outsourcing_schedule_entries = relationship(
        "OutsourcingScheduleEntry",
        primaryjoin="and_(foreign(OutsourcingScheduleEntry.scenario_id)==Scenario.id)"
    )


class ScenarioAuditLog(Base):
    __tablename__ = "scenario_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, index=True)
    action = Column(String, nullable=False)
    operator = Column(String, nullable=True)
    details = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    scenario = relationship("Scenario", back_populates="audit_logs")


class ScenarioMaintenanceOverride(Base):
    __tablename__ = "scenario_maintenance_overrides"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, index=True)
    maintenance_plan_id = Column(Integer, ForeignKey("maintenance_plans.id"), nullable=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    override_type = Column(String, nullable=False)
    new_start_time = Column(String, nullable=True)
    new_end_time = Column(String, nullable=True)
    new_day_of_week = Column(Integer, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    scenario = relationship("Scenario", back_populates="maintenance_overrides")


class ScenarioDeviceOverride(Base):
    __tablename__ = "scenario_device_overrides"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    override_type = Column(String, nullable=False)
    effective_from = Column(DateTime, nullable=True)
    effective_to = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    scenario = relationship("Scenario", back_populates="device_overrides")


class ScenarioFixtureOverride(Base):
    __tablename__ = "scenario_fixture_overrides"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=False, index=True)
    fixture_type_id = Column(Integer, ForeignKey("fixture_types.id"), nullable=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=True)
    override_type = Column(String, nullable=False)
    quantity_change = Column(Integer, default=0)
    temp_fixture_code = Column(String, nullable=True)
    temp_status = Column(String, nullable=True)
    effective_from = Column(DateTime, nullable=True)
    effective_to = Column(DateTime, nullable=True)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    scenario = relationship("Scenario", back_populates="fixture_overrides")


class OutsourcingFactory(Base):
    __tablename__ = "outsourcing_factories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    code = Column(String, unique=True, index=True, nullable=False)
    contact_person = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    daily_start = Column(String, nullable=False, default="08:00")
    daily_end = Column(String, nullable=False, default="18:00")
    max_concurrent_jobs = Column(Integer, nullable=False, default=5)
    transport_to_minutes = Column(Integer, nullable=False, default=120)
    transport_back_minutes = Column(Integer, nullable=False, default=120)
    waiting_before_process_minutes = Column(Integer, nullable=False, default=30)
    is_active = Column(Boolean, default=True)
    description = Column(String, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)

    capabilities = relationship("OutsourcingCapability", back_populates="factory", cascade="all, delete-orphan")
    schedule_entries = relationship(
        "OutsourcingScheduleEntry",
        back_populates="factory",
        cascade="all, delete-orphan"
    )


class OutsourcingCapability(Base):
    __tablename__ = "outsourcing_capabilities"

    id = Column(Integer, primary_key=True, index=True)
    factory_id = Column(Integer, ForeignKey("outsourcing_factories.id"), nullable=False, index=True)
    process_type = Column(String, nullable=False, index=True)
    base_duration_minutes = Column(Integer, nullable=False, default=60)
    duration_per_unit_minutes = Column(Integer, nullable=False, default=10)
    efficiency_factor = Column(Integer, nullable=False, default=100)
    min_batch_quantity = Column(Integer, default=1)
    max_batch_quantity = Column(Integer, nullable=True)
    quality_grade = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    factory = relationship("OutsourcingFactory", back_populates="capabilities")


class StepOutsourcingConfig(Base):
    __tablename__ = "step_outsourcing_configs"

    id = Column(Integer, primary_key=True, index=True)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False, index=True)
    factory_id = Column(Integer, ForeignKey("outsourcing_factories.id"), nullable=False, index=True)
    priority = Column(Integer, default=0)
    is_preferred = Column(Boolean, default=False)

    step = relationship("ProcessStep", back_populates="outsourcing_configs")
    factory = relationship("OutsourcingFactory")


class OutsourcingScheduleEntry(Base):
    __tablename__ = "outsourcing_schedule_entries"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False, index=True)
    sub_batch_id = Column(Integer, ForeignKey("sub_batches.id"), nullable=True, index=True)
    step_id = Column(Integer, ForeignKey("process_steps.id"), nullable=False, index=True)
    factory_id = Column(Integer, ForeignKey("outsourcing_factories.id"), nullable=False, index=True)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    node_type = Column(String, nullable=False)
    node_sequence = Column(Integer, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    is_completed = Column(Boolean, default=False)
    actual_start_time = Column(DateTime, nullable=True)
    actual_end_time = Column(DateTime, nullable=True)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)
    source_entry_id = Column(Integer, nullable=True)

    order = relationship("WorkOrder")
    sub_batch = relationship("SubBatch")
    step = relationship("ProcessStep")
    factory = relationship("OutsourcingFactory", back_populates="schedule_entries")


class InsertionHistory(Base):
    __tablename__ = "insertion_histories"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False, index=True)
    order_no = Column(String, index=True, nullable=False)
    old_priority = Column(Integer, nullable=False)
    new_priority = Column(Integer, nullable=False)
    operator = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    affected_orders_count = Column(Integer, default=0)
    delayed_orders_count = Column(Integer, default=0)
    blocked_orders_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    scenario_id = Column(Integer, ForeignKey("scenarios.id"), nullable=True, index=True)

    order = relationship("WorkOrder", back_populates="insertion_histories")
    affected_orders = relationship("InsertionAffectedOrder", back_populates="insertion_history", cascade="all, delete-orphan")


class InsertionAffectedOrder(Base):
    __tablename__ = "insertion_affected_orders"

    id = Column(Integer, primary_key=True, index=True)
    insertion_history_id = Column(Integer, ForeignKey("insertion_histories.id"), nullable=False, index=True)
    affected_order_id = Column(Integer, ForeignKey("work_orders.id"), nullable=False, index=True)
    affected_order_no = Column(String, nullable=False)
    impact_type = Column(String, nullable=False)
    delay_minutes = Column(Integer, default=0)
    blocked_reason = Column(String, nullable=True)
    original_start_time = Column(DateTime, nullable=True)
    new_start_time = Column(DateTime, nullable=True)

    insertion_history = relationship("InsertionHistory", back_populates="affected_orders")


class CapacityReservation(Base):
    __tablename__ = "capacity_reservations"

    id = Column(Integer, primary_key=True, index=True)
    reservation_no = Column(String, unique=True, index=True, nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    customer_name = Column(String, nullable=True)
    sales_person = Column(String, nullable=True)
    status = Column(String, default="active", nullable=False, index=True)
    expire_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
    release_reason = Column(String, nullable=True)
    trial_earliest_delivery = Column(DateTime, nullable=True)
    trial_expected_delivery = Column(DateTime, nullable=True)
    trial_can_meet_deadline = Column(Boolean, default=True)
    trial_bottleneck_type = Column(String, nullable=True)
    trial_bottleneck_step = Column(String, nullable=True)
    trial_bottleneck_detail = Column(String, nullable=True)

    slots = relationship(
        "CapacityReservationSlot",
        back_populates="reservation",
        cascade="all, delete-orphan"
    )


class CapacityReservationSlot(Base):
    __tablename__ = "capacity_reservation_slots"

    id = Column(Integer, primary_key=True, index=True)
    reservation_id = Column(Integer, ForeignKey("capacity_reservations.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    fixture_id = Column(Integer, ForeignKey("fixtures.id"), nullable=True)
    step_order = Column(Integer, nullable=False)
    step_name = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    fixture_turn_over_end_time = Column(DateTime, nullable=True)

    reservation = relationship("CapacityReservation", back_populates="slots")
    device = relationship("Device")
    fixture = relationship("Fixture")


class OptimizationTask(Base):
    __tablename__ = "optimization_tasks"

    id = Column(Integer, primary_key=True, index=True)
    order_ids = Column(String, nullable=False)
    objective = Column(String, nullable=False)
    max_duration_seconds = Column(Integer, nullable=False, default=300)
    status = Column(String, default="pending", nullable=False, index=True)
    explored_count = Column(Integer, default=0)
    current_best_value = Column(Integer, nullable=True)
    baseline_value = Column(Integer, nullable=True)
    result_schedule_json = Column(String, nullable=True)
    baseline_schedule_json = Column(String, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    is_applied = Column(Boolean, default=False)
    applied_at = Column(DateTime, nullable=True)
    applied_by = Column(String, nullable=True)
    baseline_hash = Column(String, nullable=True)
    baseline_timestamp = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)

    trajectories = relationship(
        "OptimizationTrajectory",
        back_populates="task",
        cascade="all, delete-orphan"
    )


class OptimizationTrajectory(Base):
    __tablename__ = "optimization_trajectories"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("optimization_tasks.id"), nullable=False, index=True)
    iteration = Column(Integer, nullable=False)
    objective_value = Column(Integer, nullable=False)
    is_best = Column(Boolean, default=False)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow)

    task = relationship("OptimizationTask", back_populates="trajectories")
