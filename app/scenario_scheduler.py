from datetime import datetime, timedelta, time, date
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
import math

from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, ScheduleEntry,
    ConflictRecord, MaintenancePlan, Material, StepMaterialRequirement,
    MaterialLock, SubBatch, SubBatchStepProgress, DeviceFault,
    FixtureType, Fixture, ScenarioDeviceOverride, ScenarioMaintenanceOverride,
    ScenarioFixtureOverride
)


def parse_time_str(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def is_within_working_hours(dt: datetime, device: Device) -> bool:
    start = parse_time_str(device.daily_start)
    end = parse_time_str(device.daily_end)
    t = dt.time()
    return start <= t <= end


def get_next_working_start(dt: datetime, device: Device) -> datetime:
    start_time = parse_time_str(device.daily_start)
    end_time = parse_time_str(device.daily_end)

    if dt.time() > end_time:
        next_day = dt.date() + timedelta(days=1)
        return datetime.combine(next_day, start_time)
    elif dt.time() < start_time:
        return datetime.combine(dt.date(), start_time)
    else:
        return dt


def calculate_available_end(dt: datetime, device: Device) -> datetime:
    end_time = parse_time_str(device.daily_end)
    return datetime.combine(dt.date(), end_time)


def _get_disabled_device_ids(db: Session, scenario_id: int, at_time: Optional[datetime] = None) -> set:
    if at_time is None:
        at_time = datetime.utcnow()
    overrides = db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id,
        ScenarioDeviceOverride.override_type == "disable"
    ).all()
    disabled = set()
    for ov in overrides:
        from_ok = ov.effective_from is None or ov.effective_from <= at_time + timedelta(days=365)
        to_ok = ov.effective_to is None or ov.effective_to >= at_time
        if from_ok and to_ok:
            disabled.add(ov.device_id)
    return disabled


def _is_device_disabled_at(db: Session, scenario_id: int, device_id: int,
                           start_dt: datetime, end_dt: datetime) -> bool:
    overrides = db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id,
        ScenarioDeviceOverride.device_id == device_id,
        ScenarioDeviceOverride.override_type == "disable"
    ).all()
    for ov in overrides:
        ov_from = ov.effective_from or datetime.min
        ov_to = ov.effective_to or datetime.max
        if start_dt < ov_to and end_dt > ov_from:
            return True
    return False


def _get_extra_fixtures(db: Session, scenario_id: int) -> List[Fixture]:
    overrides = db.query(ScenarioFixtureOverride).filter(
        ScenarioFixtureOverride.scenario_id == scenario_id,
        ScenarioFixtureOverride.override_type == "add"
    ).all()
    temp_fixtures = []
    for ov in overrides:
        ft = db.query(FixtureType).filter(FixtureType.id == ov.fixture_type_id).first()
        if not ft:
            continue
        existing_fixtures = db.query(Fixture).filter(Fixture.fixture_type_id == ov.fixture_type_id).all()
        device_types = set()
        for f in existing_fixtures:
            for dt in f.compatible_device_types.split(","):
                device_types.add(dt.strip())
        compatible_types_str = ",".join(sorted(device_types)) if device_types else "通用"

        temp_fixtures.append(Fixture(
            id=900000 + ov.id,
            code=ov.temp_fixture_code or f"TEMP-{ov.id}",
            fixture_type_id=ov.fixture_type_id,
            compatible_device_types=compatible_types_str,
            status=ov.temp_status or "available"
        ))
    return temp_fixtures


def _get_reduced_fixture_type_count(db: Session, scenario_id: int, fixture_type_id: int) -> int:
    overrides = db.query(ScenarioFixtureOverride).filter(
        ScenarioFixtureOverride.scenario_id == scenario_id,
        ScenarioFixtureOverride.fixture_type_id == fixture_type_id,
        ScenarioFixtureOverride.override_type == "reduce"
    ).all()
    total_reduce = sum(abs(ov.quantity_change) for ov in overrides)
    return total_reduce


def get_device_occupied_slots_scenario(
    db: Session, scenario_id: int, device_id: int,
    exclude_order_id: Optional[int] = None
) -> List[Tuple[datetime, datetime, bool]]:
    query = db.query(ScheduleEntry).options(joinedload(ScheduleEntry.order)).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.device_id == device_id
    )
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    entries = query.order_by(ScheduleEntry.start_time).all()
    return [(e.start_time, e.end_time, e.order.is_locked if e.order else False) for e in entries]


def get_active_device_fault_scenario(db: Session, scenario_id: int, device_id: int) -> Optional[DeviceFault]:
    return db.query(DeviceFault).filter(
        DeviceFault.scenario_id == scenario_id,
        DeviceFault.device_id == device_id,
        DeviceFault.status == "active"
    ).first()


def get_maintenance_windows_in_range_scenario(
    db: Session, scenario_id: int, device_id: int,
    start_dt: datetime, end_dt: datetime
) -> List[Tuple[datetime, datetime, str]]:
    plans = db.query(MaintenancePlan).filter(MaintenancePlan.device_id == device_id).all()

    overrides = db.query(ScenarioMaintenanceOverride).filter(
        ScenarioMaintenanceOverride.scenario_id == scenario_id,
        ScenarioMaintenanceOverride.device_id == device_id
    ).all()
    plan_override_map = {}
    for ov in overrides:
        if ov.maintenance_plan_id:
            plan_override_map[ov.maintenance_plan_id] = ov

    windows = []
    for plan in plans:
        start_t_str = plan.start_time
        end_t_str = plan.end_time
        dow = plan.day_of_week
        desc = plan.description or "设备维护"

        if plan.id in plan_override_map:
            ov = plan_override_map[plan.id]
            if ov.new_start_time:
                start_t_str = ov.new_start_time
            if ov.new_end_time:
                end_t_str = ov.new_end_time
            if ov.new_day_of_week is not None:
                dow = ov.new_day_of_week
            if ov.description:
                desc = ov.description

        current = start_dt.date()
        while current <= end_dt.date():
            if current.weekday() == dow:
                try:
                    start_t = parse_time_str(start_t_str)
                    end_t = parse_time_str(end_t_str)
                    win_start = datetime.combine(current, start_t)
                    win_end = datetime.combine(current, end_t)
                    if win_end >= start_dt and win_start <= end_dt:
                        windows.append((win_start, win_end, desc))
                except Exception:
                    pass
            current += timedelta(days=1)
    windows.sort(key=lambda x: x[0])
    return windows


def find_next_maintenance_window_scenario(
    db: Session, scenario_id: int, device_id: int,
    from_dt: datetime, max_days: int = 365
) -> Optional[Tuple[datetime, datetime, str]]:
    end = from_dt + timedelta(days=max_days)
    windows = get_maintenance_windows_in_range_scenario(db, scenario_id, device_id, from_dt, end)
    for w in windows:
        if w[1] > from_dt:
            return w
    return None


def find_earliest_slot_with_siblings_scenario(
    db: Session, scenario_id: int, device: Device,
    earliest_start: datetime, duration_minutes: int,
    order_id: Optional[int] = None, respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)

    occupied = get_device_occupied_slots_scenario(db, scenario_id, device.id, exclude_order_id=order_id)

    if sibling_entries:
        for (dev_id, s, e) in sibling_entries:
            if dev_id == device.id:
                occupied.append((s, e, True))
        occupied.sort(key=lambda x: x[0])

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

        if _is_device_disabled_at(db, scenario_id, device.id, current_start,
                                  current_start + duration):
            disable_overrides = db.query(ScenarioDeviceOverride).filter(
                ScenarioDeviceOverride.scenario_id == scenario_id,
                ScenarioDeviceOverride.device_id == device.id,
                ScenarioDeviceOverride.override_type == "disable"
            ).all()
            max_to = current_start
            for ov in disable_overrides:
                ov_to = ov.effective_to or (current_start + timedelta(days=1))
                if ov_to > max_to:
                    max_to = ov_to
            current_start = max_to
            current_start = get_next_working_start(current_start, device)
            continue

        day_end = calculate_available_end(current_start, device)
        if current_start + duration > day_end:
            next_day = current_start.date() + timedelta(days=1)
            current_start = datetime.combine(next_day, parse_time_str(device.daily_start))
            continue

        for (occ_start, occ_end, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if current_start < occ_end and current_start + duration > occ_start:
                current_start = occ_end
                moved = True
                break

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        next_maint = find_next_maintenance_window_scenario(db, scenario_id, device.id, current_start)
        if next_maint:
            maint_start, maint_end, _ = next_maint
            if current_start >= maint_start and current_start < maint_end:
                current_start = maint_end
                moved = True
            elif current_start + duration > maint_start and current_start < maint_start:
                gap = maint_start - current_start
                if gap < duration:
                    current_start = maint_end
                    moved = True

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        return current_start

    return None


def get_fixture_occupied_slots_scenario(
    db: Session, scenario_id: int, fixture_id: int,
    exclude_order_id: Optional[int] = None,
    include_turn_over: bool = True
) -> List[Tuple[datetime, datetime, bool]]:
    query = db.query(ScheduleEntry).options(joinedload(ScheduleEntry.order)).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.fixture_id == fixture_id,
        ScheduleEntry.is_completed == False
    )
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    entries = query.order_by(ScheduleEntry.start_time).all()

    slots = []
    for e in entries:
        is_locked = e.order.is_locked if e.order else False
        end_time = e.fixture_turn_over_end_time if (include_turn_over and e.fixture_turn_over_end_time) else e.end_time
        slots.append((e.start_time, end_time, is_locked))
    return slots


def find_earliest_fixture_slot_scenario(
    db: Session, scenario_id: int, fixture: Fixture,
    earliest_start: datetime, duration_minutes: int,
    turn_over_minutes: int = 0,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Optional[datetime]:
    total_duration = timedelta(minutes=duration_minutes + turn_over_minutes)
    current_start = earliest_start

    occupied = get_fixture_occupied_slots_scenario(db, scenario_id, fixture.id, exclude_order_id, include_turn_over=True)

    if sibling_entries:
        for (fix_id, s, e) in sibling_entries:
            if fix_id == fixture.id:
                occupied.append((s, e, True))
        occupied.sort(key=lambda x: x[0])

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

        for (occ_start, occ_end, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if current_start < occ_end and current_start + total_duration > occ_start:
                current_start = occ_end
                moved = True
                break

        if moved:
            continue

        return current_start

    return None


def get_available_fixtures_for_step_scenario(
    db: Session, scenario_id: int, step: ProcessStep,
    device_type: str, earliest_start: datetime,
    duration_minutes: int, exclude_order_id: Optional[int] = None,
    exclude_fixture_ids: Optional[List[int]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    respect_locked: bool = True
) -> List[Tuple[Fixture, datetime]]:
    if step.fixture_type_id is None:
        return []

    fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
    if not fixture_type:
        return []

    fixtures = list(db.query(Fixture).filter(
        Fixture.fixture_type_id == step.fixture_type_id,
        Fixture.status == "available"
    ).all())

    reduce_count = _get_reduced_fixture_type_count(db, scenario_id, step.fixture_type_id)
    if reduce_count > 0:
        fixtures = fixtures[:max(0, len(fixtures) - reduce_count)]

    extra_fixtures = _get_extra_fixtures(db, scenario_id)
    for ef in extra_fixtures:
        if ef.fixture_type_id == step.fixture_type_id:
            fixtures.append(ef)

    if exclude_fixture_ids:
        fixtures = [f for f in fixtures if f.id not in exclude_fixture_ids]

    available_fixtures = []
    for fixture in fixtures:
        compatible_types = [t.strip() for t in fixture.compatible_device_types.split(",")]
        if device_type not in compatible_types:
            continue

        slot_start = find_earliest_fixture_slot_scenario(
            db, scenario_id, fixture, earliest_start, duration_minutes,
            turn_over_minutes=fixture_type.turn_over_minutes,
            exclude_order_id=exclude_order_id,
            respect_locked=respect_locked,
            sibling_entries=sibling_fixture_entries
        )

        if slot_start is not None:
            available_fixtures.append((fixture, slot_start))

    available_fixtures.sort(key=lambda x: (x[1], x[0].id))
    return available_fixtures


def _calculate_device_load_scenario(db: Session, scenario_id: int, device_id: int) -> int:
    entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.device_id == device_id
    ).all()
    total_minutes = 0
    for e in entries:
        delta = e.end_time - e.start_time
        total_minutes += int(delta.total_seconds() / 60)
    return total_minutes


def select_best_device_and_fixture_scenario(
    db: Session, scenario_id: int, step: ProcessStep,
    earliest_start: datetime, duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_device_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    exclude_device_ids: Optional[List[int]] = None
) -> Tuple[Optional[Device], Optional[Fixture], Optional[datetime], Optional[str], Optional[str]]:
    devices = db.query(Device).filter(Device.device_type == step.device_type)

    disabled_ids = _get_disabled_device_ids(db, scenario_id, earliest_start)
    if exclude_device_ids:
        for did in exclude_device_ids:
            disabled_ids.add(did)
    if disabled_ids:
        devices = devices.filter(~Device.id.in_(list(disabled_ids)))
    devices = devices.all()

    if not devices:
        return None, None, None, "device", None

    available_devices = []
    for device in devices:
        if get_active_device_fault_scenario(db, scenario_id, device.id):
            continue
        available_devices.append(device)

    if not available_devices:
        return None, None, None, "device", None

    needs_fixture = step.fixture_type_id is not None

    best_device = None
    best_fixture = None
    best_start = None
    bottleneck_type = None
    bottleneck_fixture_type = None

    device_earliest_starts = []
    for device in available_devices:
        device_slot = find_earliest_slot_with_siblings_scenario(
            db, scenario_id, device, earliest_start, duration_minutes,
            order_id=exclude_order_id, respect_locked=respect_locked,
            sibling_entries=sibling_device_entries
        )
        if device_slot is not None:
            device_earliest_starts.append((device, device_slot))

    if not device_earliest_starts:
        bottleneck_type = "device"
        return None, None, None, bottleneck_type, None

    if not needs_fixture:
        device_earliest_starts.sort(key=lambda x: (x[1], _calculate_device_load_scenario(db, scenario_id, x[0].id)))
        best_device, best_start = device_earliest_starts[0]
        return best_device, None, best_start, None, None

    fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
    if not fixture_type:
        return None, None, None, "fixture", None

    all_candidates = []
    has_device_available = False
    has_fixture_available = False

    for device, device_slot in device_earliest_starts:
        has_device_available = True
        fixtures_for_device = get_available_fixtures_for_step_scenario(
            db, scenario_id, step, device.device_type,
            max(earliest_start, device_slot), duration_minutes,
            exclude_order_id=exclude_order_id,
            sibling_fixture_entries=sibling_fixture_entries,
            respect_locked=respect_locked
        )

        if fixtures_for_device:
            has_fixture_available = True
            for fixture, fixture_slot in fixtures_for_device:
                combined_start = max(device_slot, fixture_slot)
                all_candidates.append((combined_start, device, fixture))

    if not all_candidates:
        if has_device_available and not has_fixture_available:
            bottleneck_type = "fixture"
            bottleneck_fixture_type = fixture_type.name
        else:
            bottleneck_type = "device"
        return None, None, None, bottleneck_type, bottleneck_fixture_type

    all_candidates.sort(key=lambda x: (x[0], _calculate_device_load_scenario(db, scenario_id, x[1].id)))
    best_start, best_device, best_fixture = all_candidates[0]

    return best_device, best_fixture, best_start, None, None


def get_material_available_quantity_scenario(db: Session, scenario_id: int, material_id: int) -> int:
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        return 0
    locked = db.query(func.coalesce(func.sum(MaterialLock.quantity), 0)).filter(
        MaterialLock.material_id == material_id,
        MaterialLock.scenario_id == scenario_id
    ).scalar()
    return material.total_quantity - locked


def check_materials_for_steps_scenario(db: Session, scenario_id: int, steps: List[ProcessStep],
                                       multiplier: int = 1) -> Tuple[bool, List[Dict]]:
    shortages = []
    material_needs = {}

    for step in steps:
        for req in step.material_requirements:
            mat_id = req.material_id
            if mat_id not in material_needs:
                material_needs[mat_id] = 0
            material_needs[mat_id] += req.quantity * multiplier

    for mat_id, needed in material_needs.items():
        available = get_material_available_quantity_scenario(db, scenario_id, mat_id)
        if available < needed:
            material = db.query(Material).filter(Material.id == mat_id).first()
            shortages.append({
                "material_id": mat_id,
                "material_name": material.name if material else f"Material-{mat_id}",
                "needed": needed,
                "available": available,
                "shortage": needed - available
            })

    return len(shortages) == 0, shortages


def lock_materials_for_order_scenario(db: Session, scenario_id: int, order_id: int,
                                      steps: List[ProcessStep], multiplier: int = 1) -> bool:
    for step in steps:
        for req in step.material_requirements:
            lock = MaterialLock(
                order_id=order_id,
                step_id=step.id,
                material_id=req.material_id,
                quantity=req.quantity * multiplier,
                scenario_id=scenario_id
            )
            db.add(lock)
    db.flush()
    return True


def release_material_locks_for_order_scenario(db: Session, scenario_id: int, order_id: int) -> int:
    locks = db.query(MaterialLock).filter(
        MaterialLock.scenario_id == scenario_id,
        MaterialLock.order_id == order_id
    ).all()
    count = len(locks)
    for lock in locks:
        db.delete(lock)
    db.flush()
    return count


def release_fixtures_for_order_scenario(db: Session, scenario_id: int, order_id: int) -> int:
    entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.fixture_id.isnot(None)
    ).all()
    for entry in entries:
        entry.fixture_id = None
        entry.fixture_turn_over_end_time = None
    db.flush()
    return len(entries)


def get_min_batch_size_for_route_scenario(db: Session, scenario_id: int, steps: List[ProcessStep]) -> int:
    device_types = set(step.device_type for step in steps)
    min_batch_size = None
    disabled_ids = _get_disabled_device_ids(db, scenario_id)
    for dt in device_types:
        devices = db.query(Device).filter(
            Device.device_type == dt,
            ~Device.id.in_(list(disabled_ids)) if disabled_ids else True
        ).all()
        if devices:
            type_min = min(d.max_batch_size for d in devices)
            if min_batch_size is None or type_min < min_batch_size:
                min_batch_size = type_min
    return min_batch_size if min_batch_size is not None else 1


def split_quantity_evenly(total_quantity: int, num_batches: int) -> List[int]:
    base = total_quantity // num_batches
    remainder = total_quantity % num_batches
    quantities = [base + 1 if i < remainder else base for i in range(num_batches)]
    return quantities


def plan_split_batches_scenario(
    db: Session, scenario_id: int, order: WorkOrder, steps: List[ProcessStep]
) -> Tuple[bool, List[Dict], Optional[str]]:
    total_quantity = order.total_quantity if order.total_quantity > 0 else 1

    if total_quantity == 1:
        return False, [], None

    min_batch_size = get_min_batch_size_for_route_scenario(db, scenario_id, steps)
    if min_batch_size >= total_quantity:
        return False, [], None

    num_batches = math.ceil(total_quantity / min_batch_size)
    if num_batches < 2:
        return False, [], None

    quantities = split_quantity_evenly(total_quantity, num_batches)

    batch_plans = []
    for i, qty in enumerate(quantities):
        batch_no = f"{order.order_no}-{str(i+1).zfill(3)}"
        batch_plans.append({
            "batch_no": batch_no,
            "quantity": qty,
            "index": i
        })

    return True, batch_plans, f"拆分为{num_batches}个子批次，基准批量={min_batch_size}"


def _schedule_single_sub_batch_scenario(
    db: Session, scenario_id: int, order: WorkOrder, sub_batch: SubBatch,
    steps: List[ProcessStep], respect_locked: bool = True,
    sibling_device_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Tuple[bool, List[Dict], Optional[str], Optional[str], Optional[str]]:
    prev_end_time = order.expected_start_time
    prev_step = None
    schedule_entries = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_fixture_type = None

    if sibling_device_entries is None:
        sibling_device_entries = []
    if sibling_fixture_entries is None:
        sibling_fixture_entries = []

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        device, fixture, start_time, bn_type, bn_fixture = select_best_device_and_fixture_scenario(
            db, scenario_id, step, earliest_start, step.duration_minutes,
            exclude_order_id=order.id, respect_locked=respect_locked,
            sibling_device_entries=sibling_device_entries,
            sibling_fixture_entries=sibling_fixture_entries
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            bottleneck_type = bn_type
            bottleneck_fixture_type = bn_fixture
            break

        end_time = start_time + timedelta(minutes=step.duration_minutes)

        if end_time > order.deadline:
            bottleneck_step = step.step_name
            bottleneck_type = "deadline"
            break

        turn_over_end_time = None
        if fixture:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if fixture_type and fixture_type.turn_over_minutes > 0:
                turn_over_end_time = end_time + timedelta(minutes=fixture_type.turn_over_minutes)

        schedule_entries.append({
            "step_id": step.id,
            "device_id": device.id,
            "fixture_id": fixture.id if (fixture and fixture.id < 900000) else None,
            "step_order": step.step_order,
            "step_name": step.step_name,
            "start_time": start_time,
            "end_time": end_time,
            "fixture_turn_over_end_time": turn_over_end_time,
        })

        sibling_device_entries.append((device.id, start_time, end_time))
        if fixture and turn_over_end_time:
            sibling_fixture_entries.append((fixture.id, start_time, turn_over_end_time))
        elif fixture:
            sibling_fixture_entries.append((fixture.id, start_time, end_time))

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        for e in schedule_entries:
            sibling_device_entries.pop()
            if e["fixture_id"]:
                sibling_fixture_entries.pop()
        return False, [], bottleneck_step, bottleneck_type, bottleneck_fixture_type

    return True, schedule_entries, None, None, None


def scenario_schedule_order(db: Session, order: WorkOrder, scenario_id: int,
                            respect_locked: bool = True) -> Dict:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
    if not route:
        return {
            "success": False,
            "message": f"Product '{order.product_name}' has no process route defined",
            "bottleneck_step": None
        }

    steps = sorted(route.steps, key=lambda s: s.step_order)
    if not steps:
        return {
            "success": False,
            "message": "Process route has no steps",
            "bottleneck_step": None
        }

    materials_ok, material_shortages = check_materials_for_steps_scenario(db, scenario_id, steps)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="material_shortage",
            description=f"物料不足: {'; '.join(shortage_descs)}",
            scenario_id=scenario_id
        )
        db.add(conflict)
        order.status = "failed"
        order.bottleneck_step = material_shortages[0]["material_name"]
        db.commit()
        return {
            "success": False,
            "message": f"物料库存不足: {'; '.join(shortage_descs)}",
            "bottleneck_step": material_shortages[0]["material_name"],
            "material_shortages": material_shortages
        }

    should_split, batch_plans, split_msg = plan_split_batches_scenario(db, scenario_id, order, steps)

    if not should_split:
        sub_batch = SubBatch(
            order_id=order.id,
            batch_no=order.order_no,
            quantity=order.total_quantity,
            scenario_id=scenario_id
        )
        db.add(sub_batch)
        db.flush()

        ok, entries, bn_step, bn_type, bn_fixture = _schedule_single_sub_batch_scenario(
            db, scenario_id, order, sub_batch, steps, respect_locked=respect_locked
        )

        if not ok:
            db.delete(sub_batch)
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=f"Bottleneck at step '{bn_step}': cannot schedule before deadline",
                scenario_id=scenario_id
            )
            db.add(conflict)
            order.status = "failed"
            order.bottleneck_step = bn_step
            db.commit()
            return {
                "success": False,
                "message": f"Cannot schedule order: bottleneck at step '{bn_step}'",
                "bottleneck_step": bn_step,
                "bottleneck_type": bn_type,
                "bottleneck_fixture_type": bn_fixture
            }

        for entry in entries:
            db_entry = ScheduleEntry(
                order_id=order.id,
                sub_batch_id=sub_batch.id,
                step_id=entry["step_id"],
                device_id=entry["device_id"],
                fixture_id=entry["fixture_id"],
                step_order=entry["step_order"],
                step_name=entry["step_name"],
                start_time=entry["start_time"],
                end_time=entry["end_time"],
                fixture_turn_over_end_time=entry["fixture_turn_over_end_time"],
                scenario_id=scenario_id
            )
            db.add(db_entry)

        lock_materials_for_order_scenario(db, scenario_id, order.id, steps)

        order.status = "scheduled"
        order.bottleneck_step = None
        order.is_split = False
        order.total_sub_batches = 1
        db.commit()
        db.refresh(order)

        return {
            "success": True,
            "message": "Order scheduled successfully",
            "is_split": False,
            "total_sub_batches": 1,
            "bottleneck_step": None
        }
    else:
        sibling_device_entries = []
        sibling_fixture_entries = []
        sub_batches = []
        all_entries = []
        any_failed = False
        failed_step = None
        failed_type = None
        failed_fixture = None

        for bp in batch_plans:
            sub_batch = SubBatch(
                order_id=order.id,
                batch_no=bp["batch_no"],
                quantity=bp["quantity"],
                scenario_id=scenario_id
            )
            db.add(sub_batch)
            db.flush()

            ok, entries, bn_step, bn_type, bn_fixture = _schedule_single_sub_batch_scenario(
                db, scenario_id, order, sub_batch, steps,
                respect_locked=respect_locked,
                sibling_device_entries=sibling_device_entries,
                sibling_fixture_entries=sibling_fixture_entries
            )

            if not ok:
                any_failed = True
                failed_step = bn_step
                failed_type = bn_type
                failed_fixture = bn_fixture
                db.delete(sub_batch)
                break

            sub_batches.append(sub_batch)
            all_entries.append((sub_batch, entries))

        if any_failed:
            for sb in sub_batches:
                db.delete(sb)
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=f"Bottleneck at step '{failed_step}': cannot schedule before deadline (split mode)",
                scenario_id=scenario_id
            )
            db.add(conflict)
            order.status = "failed"
            order.bottleneck_step = failed_step
            order.is_split = False
            order.total_sub_batches = 0
            db.commit()
            return {
                "success": False,
                "message": f"Cannot schedule order (split): bottleneck at step '{failed_step}'",
                "bottleneck_step": failed_step,
                "bottleneck_type": failed_type,
                "bottleneck_fixture_type": failed_fixture
            }

        for sb, entries in all_entries:
            for entry in entries:
                db_entry = ScheduleEntry(
                    order_id=order.id,
                    sub_batch_id=sb.id,
                    step_id=entry["step_id"],
                    device_id=entry["device_id"],
                    fixture_id=entry["fixture_id"],
                    step_order=entry["step_order"],
                    step_name=entry["step_name"],
                    start_time=entry["start_time"],
                    end_time=entry["end_time"],
                    fixture_turn_over_end_time=entry["fixture_turn_over_end_time"],
                    scenario_id=scenario_id
                )
                db.add(db_entry)

        lock_materials_for_order_scenario(db, scenario_id, order.id, steps)

        order.status = "scheduled"
        order.bottleneck_step = None
        order.is_split = True
        order.total_sub_batches = len(sub_batches)
        db.commit()
        db.refresh(order)

        return {
            "success": True,
            "message": f"Order scheduled successfully: {split_msg}",
            "is_split": True,
            "total_sub_batches": len(sub_batches),
            "bottleneck_step": None
        }


def scenario_reschedule_unlocked_orders(db: Session, scenario_id: int,
                                        exclude_order_id: Optional[int] = None) -> None:
    query = db.query(WorkOrder).filter(
        WorkOrder.scenario_id == scenario_id,
        WorkOrder.is_locked == False,
        WorkOrder.status == "scheduled"
    )
    if exclude_order_id is not None:
        query = query.filter(WorkOrder.id != exclude_order_id)
    unlocked_orders = query.order_by(WorkOrder.id).all()

    for order in unlocked_orders:
        old_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.order_id == order.id
        ).all()
        old_start_times = {e.step_order: e.start_time for e in old_entries}
        old_end_times = {e.step_order: e.end_time for e in old_entries}
        old_first_start = min((e.start_time for e in old_entries), default=None)
        old_last_end = max((e.end_time for e in old_entries), default=None)

        release_material_locks_for_order_scenario(db, scenario_id, order.id)
        release_fixtures_for_order_scenario(db, scenario_id, order.id)

        db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.order_id == order.id
        ).delete(synchronize_session=False)
        db.query(SubBatch).filter(
            SubBatch.scenario_id == scenario_id,
            SubBatch.order_id == order.id
        ).delete(synchronize_session=False)
        order.is_split = False
        order.total_sub_batches = 0
        db.commit()
        db.expire_all()

        order = db.query(WorkOrder).filter(
            WorkOrder.id == order.id,
            WorkOrder.scenario_id == scenario_id
        ).first()
        if not order:
            continue

        result = scenario_schedule_order(db, order, scenario_id, respect_locked=False)

        if not result["success"]:
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=f"Order cannot be scheduled after rescheduling: {result.get('message', '')}",
                scenario_id=scenario_id
            )
            db.add(conflict)
            db.commit()
        else:
            db.refresh(order)
            new_entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.scenario_id == scenario_id,
                ScheduleEntry.order_id == order.id
            ).all()
            new_start_times = {e.step_order: e.start_time for e in new_entries}
            new_last_end = max((e.end_time for e in new_entries), default=None)

            max_delay_minutes = 0
            delayed_step = None
            for step_order in old_start_times:
                if step_order in new_start_times:
                    delay = (new_start_times[step_order] - old_start_times[step_order]).total_seconds() / 60
                    if delay > max_delay_minutes:
                        max_delay_minutes = int(delay)
                        delayed_step = next(
                            (e.step_name for e in new_entries if e.step_order == step_order),
                            f"step {step_order}"
                        )

            if old_last_end and new_last_end and new_last_end > old_last_end:
                end_delay = int((new_last_end - old_last_end).total_seconds() / 60)
                if end_delay > max_delay_minutes:
                    max_delay_minutes = end_delay

            if max_delay_minutes > 0:
                conflict = ConflictRecord(
                    order_id=order.id,
                    conflict_type="delayed",
                    description=f"Order was delayed by {max_delay_minutes} minutes due to rescheduling. "
                                f"Affected step: {delayed_step}. "
                                f"Original finish: {old_last_end}, new finish: {new_last_end}",
                    scenario_id=scenario_id
                )
                db.add(conflict)
                db.commit()
