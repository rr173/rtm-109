from datetime import datetime, timedelta, time, date
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from app.models import Device, ProcessRoute, ProcessStep, WorkOrder, ScheduleEntry, ConflictRecord, MaintenancePlan, Material, StepMaterialRequirement, MaterialLock, SubBatch, SubBatchStepProgress, DeviceFault, FixtureType, Fixture, ProductFamily, ChangeoverRule, Skill
from app.outsourcing_service import (
    schedule_outsourcing_step, create_outsourcing_schedule_entries,
    delete_outsourcing_entries_for_order
)
from app.staffing_service import (
    select_best_employee, assign_employee_to_entry,
    release_employees_for_order, get_available_employees_for_step
)
from sqlalchemy import func, or_
import math
import random


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


def get_product_family_id(db: Session, product_name: str) -> Optional[int]:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if route and route.product_family_id:
        return route.product_family_id
    return None


def get_previous_product_on_device(
    db: Session,
    device_id: int,
    before_time: datetime,
    scenario_id: Optional[int] = None
) -> Optional[str]:
    query = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.end_time <= before_time,
    )
    if scenario_id is not None:
        query = query.filter(ScheduleEntry.scenario_id == scenario_id)
    else:
        query = query.filter(ScheduleEntry.scenario_id.is_(None))
    entry = query.order_by(ScheduleEntry.end_time.desc()).first()
    if entry and entry.order:
        return entry.order.product_name
    return None


def calculate_changeover_minutes(
    db: Session,
    device_id: int,
    from_product_name: Optional[str],
    to_product_name: str,
    scenario_id: Optional[int] = None
) -> Tuple[int, str]:
    if from_product_name is None or from_product_name == to_product_name:
        return 0, "none"

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return 0, "none"

    device_type = device.device_type

    specific_rule = db.query(ChangeoverRule).filter(
        ChangeoverRule.from_product_name == from_product_name,
        ChangeoverRule.to_product_name == to_product_name,
        ChangeoverRule.device_type == device_type
    ).first()
    if specific_rule and specific_rule.device_id is None:
        return specific_rule.changeover_minutes, specific_rule.changeover_type

    specific_device_rule = db.query(ChangeoverRule).filter(
        ChangeoverRule.from_product_name == from_product_name,
        ChangeoverRule.to_product_name == to_product_name,
        ChangeoverRule.device_id == device_id
    ).first()
    if specific_device_rule:
        return specific_device_rule.changeover_minutes, specific_device_rule.changeover_type

    from_family_id = get_product_family_id(db, from_product_name)
    to_family_id = get_product_family_id(db, to_product_name)

    if from_family_id is not None and to_family_id is not None:
        if from_family_id == to_family_id:
            family_rule = db.query(ChangeoverRule).filter(
                ChangeoverRule.from_product_family_id == from_family_id,
                ChangeoverRule.to_product_family_id == to_family_id,
                ChangeoverRule.from_product_name.is_(None),
                ChangeoverRule.to_product_name.is_(None),
                ChangeoverRule.device_type == device_type,
                ChangeoverRule.device_id.is_(None)
            ).first()
            if family_rule:
                return family_rule.changeover_minutes, family_rule.changeover_type
            return 15, "same_family"

        family_rule = db.query(ChangeoverRule).filter(
            ChangeoverRule.from_product_family_id == from_family_id,
            ChangeoverRule.to_product_family_id == to_family_id,
            ChangeoverRule.from_product_name.is_(None),
            ChangeoverRule.to_product_name.is_(None),
            ChangeoverRule.device_type == device_type,
            ChangeoverRule.device_id.is_(None)
        ).first()
        if family_rule:
            return family_rule.changeover_minutes, family_rule.changeover_type

        device_family_rule = db.query(ChangeoverRule).filter(
            ChangeoverRule.from_product_family_id == from_family_id,
            ChangeoverRule.to_product_family_id == to_family_id,
            ChangeoverRule.from_product_name.is_(None),
            ChangeoverRule.to_product_name.is_(None),
            ChangeoverRule.device_id == device_id
        ).first()
        if device_family_rule:
            return device_family_rule.changeover_minutes, device_family_rule.changeover_type

        return 60, "cross_family"

    return 30, "cross_family"


def get_device_occupied_slots(db: Session, device_id: int, exclude_order_id: Optional[int] = None) -> List[Tuple[datetime, datetime, bool]]:
    from sqlalchemy.orm import joinedload
    query = db.query(ScheduleEntry).options(joinedload(ScheduleEntry.order)).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.scenario_id.is_(None)
    )
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    entries = query.order_by(ScheduleEntry.start_time).all()
    slots = []
    for e in entries:
        is_locked = (e.order.is_locked if e.order else False) or e.is_delivered_locked
        slot_start = e.changeover_start_time if e.changeover_start_time else e.start_time
        slots.append((slot_start, e.end_time, is_locked))

    from app.capacity_reservation_service import get_reservation_occupied_device_slots
    reservation_slots = get_reservation_occupied_device_slots(db, device_id)
    for (rs, re, _, _, _) in reservation_slots:
        slots.append((rs, re, True))

    return slots


def get_fixture_occupied_slots(
    db: Session,
    fixture_id: int,
    exclude_order_id: Optional[int] = None,
    include_turn_over: bool = True
) -> List[Tuple[datetime, datetime, bool]]:
    from sqlalchemy.orm import joinedload
    query = db.query(ScheduleEntry).options(joinedload(ScheduleEntry.order)).filter(
        ScheduleEntry.fixture_id == fixture_id,
        ScheduleEntry.is_completed == False,
        ScheduleEntry.scenario_id.is_(None)
    )
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    entries = query.order_by(ScheduleEntry.start_time).all()
    
    slots = []
    for e in entries:
        is_locked = (e.order.is_locked if e.order else False) or e.is_delivered_locked
        end_time = e.fixture_turn_over_end_time if (include_turn_over and e.fixture_turn_over_end_time) else e.end_time
        slots.append((e.start_time, end_time, is_locked))

    from app.capacity_reservation_service import get_reservation_occupied_fixture_slots
    reservation_slots = get_reservation_occupied_fixture_slots(db, fixture_id)
    for (rs, re, _, _, _) in reservation_slots:
        slots.append((rs, re, True))

    return slots


def get_available_fixtures_for_step(
    db: Session,
    step: ProcessStep,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    exclude_fixture_ids: Optional[List[int]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    respect_locked: bool = True
) -> List[Tuple[Fixture, datetime]]:
    if step.fixture_type_id is None:
        return []
    
    fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
    if not fixture_type:
        return []
    
    fixtures = db.query(Fixture).filter(
        Fixture.fixture_type_id == step.fixture_type_id,
        Fixture.status == "available"
    ).all()
    
    if exclude_fixture_ids:
        fixtures = [f for f in fixtures if f.id not in exclude_fixture_ids]
    
    available_fixtures = []
    for fixture in fixtures:
        compatible_types = [t.strip() for t in fixture.compatible_device_types.split(",")]
        if device_type not in compatible_types:
            continue
        
        slot_start = find_earliest_fixture_slot(
            db, fixture, earliest_start, duration_minutes,
            turn_over_minutes=fixture_type.turn_over_minutes,
            exclude_order_id=exclude_order_id,
            respect_locked=respect_locked,
            sibling_entries=sibling_fixture_entries
        )
        
        if slot_start is not None:
            available_fixtures.append((fixture, slot_start))
    
    available_fixtures.sort(key=lambda x: (x[1], x[0].id))
    return available_fixtures


def find_earliest_fixture_slot(
    db: Session,
    fixture: Fixture,
    earliest_start: datetime,
    duration_minutes: int,
    turn_over_minutes: int = 0,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Optional[datetime]:
    total_duration = timedelta(minutes=duration_minutes + turn_over_minutes)
    current_start = earliest_start
    
    occupied = get_fixture_occupied_slots(db, fixture.id, exclude_order_id, include_turn_over=True)
    
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


def select_best_device_and_fixture(
    db: Session,
    step: ProcessStep,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_device_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_employee_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    exclude_device_ids: Optional[List[int]] = None,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None,
    order_priority: int = 5
) -> Tuple[Optional[Device], Optional[Fixture], Optional[datetime], Optional[str], Optional[str], Optional[object], Optional[str], Optional[int]]:
    devices = db.query(Device).filter(Device.device_type == step.device_type)
    
    if exclude_device_ids:
        devices = devices.filter(~Device.id.in_(exclude_device_ids))
    
    devices = devices.all()
    
    if not devices:
        return None, None, None, "device", None, None, None, None
    
    available_devices = []
    for device in devices:
        if get_active_device_fault(db, device.id):
            continue
        if exclude_device_ids and device.id in exclude_device_ids:
            continue
        available_devices.append(device)
    
    if not available_devices:
        return None, None, None, "device", None, None, None, None
    
    needs_fixture = step.fixture_type_id is not None
    
    best_device = None
    best_fixture = None
    best_start = None
    best_employee = None
    bottleneck_type = None
    bottleneck_reason = None
    bottleneck_skill = None
    bottleneck_skill_level = None
    
    device_earliest_starts = []
    for device in available_devices:
        device_slot = find_earliest_slot_with_siblings(
            db, device, earliest_start, duration_minutes,
            order_id=exclude_order_id, respect_locked=respect_locked,
            sibling_entries=sibling_device_entries,
            product_name=product_name,
            deadline=deadline
        )
        if device_slot is not None:
            device_earliest_starts.append((device, device_slot))
    
    if not device_earliest_starts:
        bottleneck_type = "device"
        return None, None, None, bottleneck_type, None, None, None, None
    
    if not needs_fixture:
        device_earliest_starts.sort(key=lambda x: (x[1], _calculate_device_load(db, x[0].id)))
        
        all_candidates = []
        for device, device_slot in device_earliest_starts:
            employee, emp_start, staffing_result = select_best_employee(
                db, step, device, device_slot, duration_minutes,
                exclude_order_id=exclude_order_id,
                respect_locked=respect_locked,
                sibling_entries=sibling_employee_entries,
                order_priority=order_priority
            )
            
            if employee and emp_start:
                combined_start = max(device_slot, emp_start)
                all_candidates.append((combined_start, device, None, employee, device_slot, emp_start))
            elif staffing_result and not staffing_result.has_available_staff:
                bottleneck_type = "staff"
                bottleneck_skill = staffing_result.missing_skill
                bottleneck_skill_level = staffing_result.missing_skill_level
                bottleneck_reason = staffing_result.detail
        
        if all_candidates:
            all_candidates.sort(key=lambda x: (x[0], _calculate_device_load(db, x[1].id)))
            best_start, best_device, best_fixture, best_employee, _, _ = all_candidates[0]
            return best_device, best_fixture, best_start, None, None, best_employee, None, None
        elif bottleneck_type == "staff":
            return None, None, None, bottleneck_type, bottleneck_reason, None, bottleneck_skill, bottleneck_skill_level
        else:
            bottleneck_type = "device"
            return None, None, None, bottleneck_type, None, None, None, None
    
    fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
    if not fixture_type:
        return None, None, None, "fixture", None, None, None, None
    
    all_candidates = []
    has_device_available = False
    has_fixture_available = False
    has_staff_available = False
    
    for device, device_slot in device_earliest_starts:
        has_device_available = True
        fixtures_for_device = get_available_fixtures_for_step(
            db, step, device.device_type, max(earliest_start, device_slot), duration_minutes,
            exclude_order_id=exclude_order_id,
            sibling_fixture_entries=sibling_fixture_entries,
            respect_locked=respect_locked
        )
        
        if fixtures_for_device:
            has_fixture_available = True
            for fixture, fixture_slot in fixtures_for_device:
                combined_start = max(device_slot, fixture_slot)
                
                employee, emp_start, staffing_result = select_best_employee(
                    db, step, device, combined_start, duration_minutes,
                    exclude_order_id=exclude_order_id,
                    respect_locked=respect_locked,
                    sibling_entries=sibling_employee_entries,
                    order_priority=order_priority
                )
                
                if employee and emp_start:
                    has_staff_available = True
                    final_start = max(combined_start, emp_start)
                    all_candidates.append((final_start, device, fixture, employee, device_slot, fixture_slot, emp_start))
                elif staffing_result and not staffing_result.has_available_staff:
                    if not bottleneck_type or bottleneck_type != "staff":
                        bottleneck_type = "staff"
                        bottleneck_skill = staffing_result.missing_skill
                        bottleneck_skill_level = staffing_result.missing_skill_level
                        bottleneck_reason = staffing_result.detail
    
    if not all_candidates:
        if not has_device_available:
            bottleneck_type = "device"
        elif not has_fixture_available:
            bottleneck_type = "fixture"
            bottleneck_reason = fixture_type.name
        elif not has_staff_available:
            pass
        return None, None, None, bottleneck_type, bottleneck_reason, None, bottleneck_skill, bottleneck_skill_level
    
    all_candidates.sort(key=lambda x: (x[0], _calculate_device_load(db, x[1].id)))
    best_start, best_device, best_fixture, best_employee, _, _, _ = all_candidates[0]
    
    return best_device, best_fixture, best_start, None, None, best_employee, None, None


def release_fixtures_for_order(db: Session, order_id: int, respect_delivery_lock: bool = True) -> int:
    query = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.fixture_id.isnot(None)
    )
    if respect_delivery_lock:
        query = query.filter(ScheduleEntry.is_delivered_locked == False)
    entries = query.all()
    for entry in entries:
        entry.fixture_id = None
        entry.fixture_turn_over_end_time = None
    db.flush()
    return len(entries)


def get_fixture_occupancy(
    db: Session,
    fixture_id: int,
    look_ahead_days: int = 7
) -> List[Dict]:
    from sqlalchemy import or_, and_
    
    now = datetime.now()
    end_time = now + timedelta(days=look_ahead_days)
    
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order),
        joinedload(ScheduleEntry.sub_batch),
        joinedload(ScheduleEntry.device)
    ).filter(
        ScheduleEntry.fixture_id == fixture_id,
        ScheduleEntry.start_time < end_time,
        or_(
            and_(ScheduleEntry.fixture_turn_over_end_time.isnot(None), ScheduleEntry.fixture_turn_over_end_time > now),
            ScheduleEntry.end_time > now
        ),
        ScheduleEntry.is_completed == False
    ).order_by(ScheduleEntry.start_time).all()
    
    occupancy = []
    for entry in entries:
        order = entry.order
        sub_batch = entry.sub_batch
        device = entry.device
        
        fixture_release_time = entry.fixture_turn_over_end_time if entry.fixture_turn_over_end_time else entry.end_time
        
        is_producing = now >= entry.start_time and now < entry.end_time
        is_in_turn_over = False
        if entry.fixture_turn_over_end_time:
            is_in_turn_over = now >= entry.end_time and now < entry.fixture_turn_over_end_time
        
        status = "scheduled"
        if entry.is_completed:
            status = "completed"
        elif is_producing:
            status = "producing"
        elif is_in_turn_over:
            status = "turn_over"
        
        occupancy.append({
            "schedule_entry_id": entry.id,
            "order_id": order.id if order else None,
            "order_no": order.order_no if order else None,
            "sub_batch_id": sub_batch.id if sub_batch else None,
            "sub_batch_no": sub_batch.batch_no if sub_batch else None,
            "step_order": entry.step_order,
            "step_name": entry.step_name,
            "device_id": device.id if device else None,
            "device_name": device.name if device else None,
            "start_time": entry.start_time,
            "end_time": entry.end_time,
            "turn_over_end_time": entry.fixture_turn_over_end_time,
            "fixture_release_time": fixture_release_time,
            "status": status,
            "is_producing": is_producing,
            "is_in_turn_over": is_in_turn_over,
        })
    
    return occupancy


def get_fixture_timeline(
    db: Session,
    fixture_id: int,
    look_ahead_days: int = 7
) -> Dict:
    fixture = db.query(Fixture).options(
        joinedload(Fixture.fixture_type)
    ).filter(Fixture.id == fixture_id).first()
    
    if not fixture:
        return {"success": False, "message": f"工装 ID {fixture_id} 不存在"}
    
    occupancy = get_fixture_occupancy(db, fixture_id, look_ahead_days)
    
    now = datetime.now()
    current_occupancy = None
    for occ in occupancy:
        if occ["is_producing"] or occ["is_in_turn_over"]:
            current_occupancy = occ
            break
    
    days = []
    for day_offset in range(look_ahead_days):
        current_date = now.date() + timedelta(days=day_offset)
        day_start = datetime.combine(current_date, time.min)
        day_end = day_start + timedelta(days=1)
        
        entries = []
        for occ in occupancy:
            occ_start = occ["start_time"]
            occ_end = occ["fixture_turn_over_end_time"] or occ["end_time"]
            
            if occ_end <= day_start or occ_start >= day_end:
                continue
            
            entry_start = max(occ_start, day_start)
            entry_end = min(occ_end, day_end)
            
            entry_type = "production"
            if occ["start_time"] <= day_start and day_start < occ["end_time"]:
                entry_type = "production"
            elif occ["end_time"] <= day_start and day_start < (occ["fixture_turn_over_end_time"] or occ["end_time"]):
                entry_type = "turn_over"
            elif occ["is_in_turn_over"]:
                entry_type = "turn_over"
            
            description = f"{occ['order_no']} - {occ['step_name']}"
            if occ["sub_batch_no"]:
                description += f" ({occ['sub_batch_no']})"
            
            entries.append({
                "type": entry_type,
                "start_time": entry_start,
                "end_time": entry_end,
                "description": description,
                "order_no": occ["order_no"],
                "sub_batch_no": occ["sub_batch_no"],
                "step_name": occ["step_name"],
            })
        
        entries.sort(key=lambda x: x["start_time"])
        days.append({
            "date": current_date.isoformat(),
            "entries": entries,
        })
    
    return {
        "success": True,
        "fixture_id": fixture.id,
        "fixture_code": fixture.code,
        "fixture_type_name": fixture.fixture_type.name if fixture.fixture_type else None,
        "status": fixture.status,
        "current_occupancy": current_occupancy,
        "days": days,
    }


def check_fixture_type_in_use(db: Session, fixture_type_id: int) -> Tuple[bool, List[str]]:
    steps = db.query(ProcessStep).filter(
        ProcessStep.fixture_type_id == fixture_type_id
    ).all()
    
    if not steps:
        return False, []
    
    route_ids = set(step.route_id for step in steps)
    routes = db.query(ProcessRoute).filter(
        ProcessRoute.id.in_(route_ids)
    ).all()
    
    issues = []
    for route in routes:
        issues.append(f"产品 '{route.product_name}' 的工艺路线正在使用该工装类型")
    
    return True, issues


def check_fixture_has_future_occupancy(db: Session, fixture_id: int) -> Tuple[bool, List[str]]:
    from sqlalchemy import or_, and_
    
    now = datetime.now()
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.fixture_id == fixture_id,
        or_(
            and_(ScheduleEntry.fixture_turn_over_end_time.isnot(None), ScheduleEntry.fixture_turn_over_end_time > now),
            ScheduleEntry.end_time > now
        ),
        ScheduleEntry.is_completed == False
    ).all()
    
    if not entries:
        return False, []
    
    order_ids = set()
    for entry in entries:
        if entry.order_id:
            order_ids.add(entry.order_id)
    
    orders = db.query(WorkOrder).filter(
        WorkOrder.id.in_(order_ids)
    ).all()
    
    issues = []
    for entry in entries:
        order = entry.order
        if order:
            fixture_release = entry.fixture_turn_over_end_time or entry.end_time
            state = "周转中" if (entry.fixture_turn_over_end_time and now >= entry.end_time) else "排产中"
            issues.append(f"工单 '{order.order_no}' 工序 '{entry.step_name}' {state}，占用至 {fixture_release.strftime('%Y-%m-%d %H:%M')}")
    
    return True, issues


def get_maintenance_windows_in_range(
    db: Session,
    device_id: int,
    start_dt: datetime,
    end_dt: datetime
) -> List[Tuple[datetime, datetime, str]]:
    plans = db.query(MaintenancePlan).filter(MaintenancePlan.device_id == device_id).all()
    windows = []
    for plan in plans:
        current = start_dt.date()
        while current <= end_dt.date():
            if current.weekday() == plan.day_of_week:
                start_t = parse_time_str(plan.start_time)
                end_t = parse_time_str(plan.end_time)
                win_start = datetime.combine(current, start_t)
                win_end = datetime.combine(current, end_t)
                if win_end >= start_dt and win_start <= end_dt:
                    windows.append((win_start, win_end, plan.description or "设备维护"))
            current += timedelta(days=1)
    windows.sort(key=lambda x: x[0])
    return windows


def find_next_maintenance_window(
    db: Session,
    device_id: int,
    from_dt: datetime,
    max_days: int = 365
) -> Optional[Tuple[datetime, datetime, str]]:
    plans = db.query(MaintenancePlan).filter(MaintenancePlan.device_id == device_id).all()
    if not plans:
        return None

    from_date = from_dt.date()
    for day_offset in range(max_days):
        check_date = from_date + timedelta(days=day_offset)
        weekday = check_date.weekday()
        for plan in plans:
            if plan.day_of_week == weekday:
                start_t = parse_time_str(plan.start_time)
                end_t = parse_time_str(plan.end_time)
                win_start = datetime.combine(check_date, start_t)
                win_end = datetime.combine(check_date, end_t)
                if win_end > from_dt:
                    return (win_start, win_end, plan.description or "设备维护")
    return None


def find_earliest_slot(
    db: Session,
    device: Device,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True
) -> Optional[datetime]:
    return find_earliest_slot_with_faults(
        db, device, earliest_start, duration_minutes,
        exclude_order_id=exclude_order_id, respect_locked=respect_locked
    )


def select_best_device(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None
) -> Tuple[Optional[Device], Optional[datetime]]:
    return select_best_device_with_faults(
        db, device_type, earliest_start, duration_minutes,
        exclude_order_id=exclude_order_id, respect_locked=respect_locked,
        product_name=product_name,
        deadline=deadline
    )


def _calculate_device_load(db: Session, device_id: int) -> int:
    entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.scenario_id.is_(None)
    ).all()
    total_minutes = 0
    for e in entries:
        delta = e.end_time - e.start_time
        total_minutes += int(delta.total_seconds() / 60)
    return total_minutes


def get_material_available_quantity(db: Session, material_id: int) -> int:
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        return 0
    locked = db.query(func.coalesce(func.sum(MaterialLock.quantity), 0)).filter(
        MaterialLock.material_id == material_id,
        MaterialLock.scenario_id.is_(None)
    ).scalar()
    return material.total_quantity - locked


def check_materials_for_steps(db: Session, steps: List[ProcessStep], multiplier: int = 1) -> Tuple[bool, List[Dict]]:
    shortages = []
    material_needs = {}

    for step in steps:
        for req in step.material_requirements:
            mat_id = req.material_id
            if mat_id not in material_needs:
                material_needs[mat_id] = 0
            material_needs[mat_id] += req.quantity * multiplier

    for mat_id, needed in material_needs.items():
        available = get_material_available_quantity(db, mat_id)
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


def lock_materials_for_order(db: Session, order_id: int, steps: List[ProcessStep], multiplier: int = 1) -> bool:
    for step in steps:
        for req in step.material_requirements:
            lock = MaterialLock(
                order_id=order_id,
                step_id=step.id,
                material_id=req.material_id,
                quantity=req.quantity * multiplier
            )
            db.add(lock)
    db.flush()
    return True


def release_material_locks_for_order(db: Session, order_id: int) -> int:
    locks = db.query(MaterialLock).filter(MaterialLock.order_id == order_id).all()
    count = len(locks)
    for lock in locks:
        db.delete(lock)
    db.flush()
    return count


def schedule_order(db: Session, order: WorkOrder, respect_locked: bool = True) -> Dict:
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

    materials_ok, material_shortages = check_materials_for_steps(db, steps)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}{db.query(Material).filter(Material.id == s['material_id']).first().unit if db.query(Material).filter(Material.id == s['material_id']).first() else ''}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="material_shortage",
            description=f"物料不足: {'; '.join(shortage_descs)}"
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

    prev_end_time = order.expected_start_time
    prev_step = None
    schedule_entries = []
    bottleneck_step = None

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        device, start_time = select_best_device(
            db, step.device_type, earliest_start, step.duration_minutes,
            exclude_order_id=order.id, respect_locked=respect_locked,
            product_name=order.product_name
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            break

        end_time = start_time + timedelta(minutes=step.duration_minutes)

        if end_time > order.deadline:
            bottleneck_step = step.step_name
            break

        prev_product = get_previous_product_on_device(db, device.id, start_time)
        changeover_minutes, changeover_type = calculate_changeover_minutes(
            db, device.id, prev_product, order.product_name
        )
        changeover_start_time = None
        changeover_end_time = None
        if changeover_minutes > 0:
            changeover_start_time = start_time
            changeover_end_time = start_time + timedelta(minutes=changeover_minutes)
            start_time = changeover_end_time
            end_time = start_time + timedelta(minutes=step.duration_minutes)
            if end_time > order.deadline:
                bottleneck_step = step.step_name
                break

        schedule_entries.append({
            "step_id": step.id,
            "device_id": device.id,
            "step_order": step.step_order,
            "step_name": step.step_name,
            "start_time": start_time,
            "end_time": end_time,
            "changeover_start_time": changeover_start_time,
            "changeover_end_time": changeover_end_time,
            "changeover_minutes": changeover_minutes,
            "changeover_type": changeover_type,
            "prev_product_name": prev_product,
        })

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        from app.capacity_reservation_service import find_reservation_blockers
        reservation_blocker_desc = ""
        bn_step_obj = next((s for s in steps if s.step_name == bottleneck_step), None)
        if bn_step_obj:
            for dev in db.query(Device).filter(Device.device_type == bn_step_obj.device_type).all():
                blockers = find_reservation_blockers(db, dev.id, order.expected_start_time, order.deadline)
                for b in blockers[:3]:
                    reservation_blocker_desc += f"; 设备{dev.name}被预留[{b['reservation_no']}]占用({b['product_name']}工序{b['step_name']})"

        conflict_msg = f"Bottleneck at step '{bottleneck_step}': cannot schedule before deadline"
        if reservation_blocker_desc:
            conflict_msg += f"，其中包含产能预留占用{reservation_blocker_desc}"

        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="scheduling_failed",
            description=conflict_msg
        )
        db.add(conflict)
        order.status = "failed"
        order.bottleneck_step = bottleneck_step
        db.commit()
        return {
            "success": False,
            "message": conflict_msg,
            "bottleneck_step": bottleneck_step
        }

    for entry in schedule_entries:
        db_entry = ScheduleEntry(
            order_id=order.id,
            step_id=entry["step_id"],
            device_id=entry["device_id"],
            step_order=entry["step_order"],
            step_name=entry["step_name"],
            start_time=entry["start_time"],
            end_time=entry["end_time"],
            changeover_start_time=entry.get("changeover_start_time"),
            changeover_end_time=entry.get("changeover_end_time"),
            changeover_minutes=entry.get("changeover_minutes", 0),
            changeover_type=entry.get("changeover_type"),
            prev_product_name=entry.get("prev_product_name"),
        )
        db.add(db_entry)

    lock_materials_for_order(db, order.id, steps)

    order.status = "scheduled"
    order.bottleneck_step = None
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "message": "Order scheduled successfully",
        "schedule_entries": order.schedule_entries,
        "bottleneck_step": None
    }


def reschedule_unlocked_orders(db: Session, exclude_order_id: Optional[int] = None) -> None:
    from app.models import BatchDeliveryRecord

    query = db.query(WorkOrder).filter(
        WorkOrder.is_locked == False,
        WorkOrder.status == "scheduled",
        WorkOrder.scenario_id.is_(None)
    )
    if exclude_order_id is not None:
        query = query.filter(WorkOrder.id != exclude_order_id)
    unlocked_orders = query.all()

    if not unlocked_orders:
        return

    order_ids_with_delivery = set()
    delivered_orders = db.query(BatchDeliveryRecord).filter(
        BatchDeliveryRecord.scenario_id.is_(None)
    ).all()
    for r in delivered_orders:
        order_ids_with_delivery.add(r.order_id)

    unlocked_orders = [o for o in unlocked_orders if o.id not in order_ids_with_delivery]

    if not unlocked_orders:
        return

    order_info = []
    for order in unlocked_orders:
        first_entry = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order.id
        ).order_by(ScheduleEntry.start_time.asc()).first()
        start_time = first_entry.start_time if first_entry else order.expected_start_time
        old_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
        old_start_times = {e.step_order: e.start_time for e in old_entries}
        old_last_end = max((e.end_time for e in old_entries), default=None) if old_entries else None
        order_info.append({
            "order": order,
            "start_time": start_time,
            "old_start_times": old_start_times,
            "old_last_end": old_last_end
        })

    order_info.sort(key=lambda x: (-x["order"].priority, x["start_time"]))

    order_ids = [info["order"].id for info in order_info]

    for oid in order_ids:
        release_material_locks_for_order(db, oid)
        release_fixtures_for_order(db, oid)
        release_employees_for_order(db, oid)
        delete_outsourcing_entries_for_order(db, oid)
        db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == oid,
            ScheduleEntry.is_delivered_locked == False
        ).delete(synchronize_session=False)
        db.query(SubBatch).filter(
            SubBatch.order_id == oid,
            SubBatch.delivered_quantity == 0
        ).delete(synchronize_session=False)
    db.flush()

    for info in order_info:
        order = db.query(WorkOrder).filter(WorkOrder.id == info["order"].id).first()
        if not order:
            continue

        old_start_times = info["old_start_times"]
        old_last_end = info["old_last_end"]

        result = schedule_order(db, order, respect_locked=False)

        if not result["success"]:
            order.is_blocked = True
            order.blocked_reason = result.get("message", "排产失败")
            order.status = "failed"
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=f"Order cannot be scheduled after rescheduling: {result.get('message', '')}"
            )
            db.add(conflict)
            db.commit()
        else:
            db.refresh(order)
            order.is_blocked = False
            order.blocked_reason = None
            new_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
            new_start_times = {e.step_order: e.start_time for e in new_entries}
            new_last_end = max((e.end_time for e in new_entries), default=None)

            max_delay_minutes = 0
            delayed_step = None
            for step_order in old_start_times:
                if step_order in new_start_times:
                    delay = (new_start_times[step_order] - old_start_times[step_order]).total_seconds() / 60
                    if delay > max_delay_minutes:
                        max_delay_minutes = int(delay)
                        delayed_step = next((e.step_name for e in new_entries if e.step_order == step_order), f"step {step_order}")

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
                                f"Original finish: {old_last_end}, new finish: {new_last_end}"
                )
                db.add(conflict)
                db.commit()


def get_min_batch_size_for_route(db: Session, steps: List[ProcessStep]) -> int:
    device_types = set(step.device_type for step in steps)
    min_batch_size = None
    for dt in device_types:
        devices = db.query(Device).filter(Device.device_type == dt).all()
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


def plan_split_batches(
    db: Session,
    order: WorkOrder,
    steps: List[ProcessStep]
) -> Tuple[bool, List[Dict], Optional[str]]:
    from app.models import DeliveryPlan

    total_quantity = order.total_quantity if order.total_quantity > 0 else 1

    delivery_plans = db.query(DeliveryPlan).filter(
        DeliveryPlan.order_id == order.id,
        DeliveryPlan.scenario_id.is_(None)
    ).order_by(DeliveryPlan.plan_index).all()

    if delivery_plans:
        total_planned = sum(p.planned_quantity for p in delivery_plans)
        if total_planned > total_quantity:
            return False, [], None

        min_batch_size = get_min_batch_size_for_route(db, steps)
        batch_plans = []
        global_index = 0

        for plan in delivery_plans:
            plan_qty = plan.planned_quantity
            if plan_qty <= min_batch_size:
                sub_quantities = [plan_qty]
            else:
                num_sub = math.ceil(plan_qty / min_batch_size)
                sub_quantities = split_quantity_evenly(plan_qty, num_sub)

            for i, sq in enumerate(sub_quantities):
                batch_no = f"{order.order_no}-P{plan.plan_index}-{str(i+1).zfill(3)}"
                batch_plans.append({
                    "batch_no": batch_no,
                    "quantity": sq,
                    "index": global_index,
                    "delivery_plan_id": plan.id,
                    "delivery_plan_index": plan.plan_index,
                    "expected_delivery_date": plan.expected_delivery_date
                })
                global_index += 1

        remaining = total_quantity - total_planned
        if remaining > 0:
            if remaining <= min_batch_size:
                sub_quantities = [remaining]
            else:
                num_sub = math.ceil(remaining / min_batch_size)
                sub_quantities = split_quantity_evenly(remaining, num_sub)

            for i, sq in enumerate(sub_quantities):
                batch_no = f"{order.order_no}-EXT-{str(i+1).zfill(3)}"
                batch_plans.append({
                    "batch_no": batch_no,
                    "quantity": sq,
                    "index": global_index,
                    "delivery_plan_id": None,
                    "delivery_plan_index": None,
                    "expected_delivery_date": order.deadline
                })
                global_index += 1

        return True, batch_plans, f"按交付计划拆分为{len(batch_plans)}个子批次，交付计划{len(delivery_plans)}批"

    if total_quantity == 1:
        return False, [], None

    min_batch_size = get_min_batch_size_for_route(db, steps)
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


def _schedule_single_sub_batch(
    db: Session,
    order: WorkOrder,
    sub_batch: SubBatch,
    steps: List[ProcessStep],
    respect_locked: bool = True,
    sibling_device_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_fixture_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_outsourcing_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    sibling_employee_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Tuple[bool, List[Dict], Optional[str], Optional[str], Optional[str], List[Dict], Optional[str], Optional[int]]:
    prev_end_time = order.expected_start_time
    prev_step = None
    schedule_entries = []
    outsourcing_results = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_fixture_type = None
    bottleneck_skill = None
    bottleneck_skill_level = None

    if sibling_device_entries is None:
        sibling_device_entries = []
    if sibling_fixture_entries is None:
        sibling_fixture_entries = []
    if sibling_outsourcing_entries is None:
        sibling_outsourcing_entries = []
    if sibling_employee_entries is None:
        sibling_employee_entries = []

    employee_assignments = []

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        if step.is_outsource:
            success, nodes, factory, bn_type, bn_msg = schedule_outsourcing_step(
                db, order, sub_batch, step,
                quantity=sub_batch.quantity,
                earliest_start=earliest_start,
                deadline=order.deadline,
                exclude_order_id=order.id,
                sibling_process_entries=sibling_outsourcing_entries
            )

            if not success:
                bottleneck_step = step.step_name
                bottleneck_type = bn_type
                bottleneck_fixture_type = bn_msg
                break

            process_node = next(n for n in nodes if n["node_type"] == "outsourcing_process")
            returned_node = next(n for n in nodes if n["node_type"] == "returned_waiting")

            sibling_outsourcing_entries.append((
                factory.id,
                process_node["start_time"],
                process_node["end_time"]
            ))

            outsourcing_results.append({
                "step": step,
                "factory": factory,
                "nodes": nodes
            })

            prev_end_time = returned_node["end_time"]
            prev_step = step
            continue

        device, fixture, start_time, bn_type, bn_fixture, employee, bn_skill, bn_skill_level = select_best_device_and_fixture(
            db, step, earliest_start, step.duration_minutes,
            exclude_order_id=order.id,
            respect_locked=respect_locked,
            sibling_device_entries=sibling_device_entries,
            sibling_fixture_entries=sibling_fixture_entries,
            sibling_employee_entries=sibling_employee_entries,
            product_name=order.product_name,
            deadline=order.deadline,
            order_priority=order.priority
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            bottleneck_type = bn_type
            bottleneck_fixture_type = bn_fixture
            bottleneck_skill = bn_skill
            bottleneck_skill_level = bn_skill_level
            break

        prev_product = get_previous_product_on_device(db, device.id, start_time)
        changeover_minutes, changeover_type = calculate_changeover_minutes(
            db, device.id, prev_product, order.product_name
        )

        total_end_time = start_time + timedelta(minutes=changeover_minutes + step.duration_minutes)
        if total_end_time > order.deadline:
            bottleneck_step = step.step_name
            if changeover_minutes > 0:
                bottleneck_type = "changeover"
            else:
                bottleneck_type = "deadline"
            break

        changeover_start_time = None
        changeover_end_time = None
        if changeover_minutes > 0:
            changeover_start_time = start_time
            changeover_end_time = start_time + timedelta(minutes=changeover_minutes)
            start_time = changeover_end_time

        end_time = start_time + timedelta(minutes=step.duration_minutes)

        turn_over_end_time = None
        if fixture:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if fixture_type and fixture_type.turn_over_minutes > 0:
                turn_over_end_time = end_time + timedelta(minutes=fixture_type.turn_over_minutes)

        schedule_entries.append({
            "step_id": step.id,
            "device_id": device.id,
            "fixture_id": fixture.id if fixture else None,
            "operator_id": employee.id if employee else None,
            "step_order": step.step_order,
            "step_name": step.step_name,
            "start_time": start_time,
            "end_time": end_time,
            "fixture_turn_over_end_time": turn_over_end_time,
            "changeover_start_time": changeover_start_time,
            "changeover_end_time": changeover_end_time,
            "changeover_minutes": changeover_minutes,
            "changeover_type": changeover_type,
            "prev_product_name": prev_product,
        })

        occupied_start = changeover_start_time if changeover_start_time else start_time
        sibling_device_entries.append((device.id, occupied_start, end_time))
        if fixture and turn_over_end_time:
            sibling_fixture_entries.append((fixture.id, start_time, turn_over_end_time))
        elif fixture:
            sibling_fixture_entries.append((fixture.id, start_time, end_time))
        if employee:
            sibling_employee_entries.append((employee.id, start_time, end_time))
            employee_assignments.append(employee)

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        for e in schedule_entries:
            sibling_device_entries.pop()
            if e["fixture_id"]:
                sibling_fixture_entries.pop()
            if e.get("operator_id"):
                sibling_employee_entries.pop()
        for _ in outsourcing_results:
            sibling_outsourcing_entries.pop()
        return False, [], bottleneck_step, bottleneck_type, bottleneck_fixture_type, [], bottleneck_skill, bottleneck_skill_level

    return True, schedule_entries, None, None, None, outsourcing_results, None, None


def find_earliest_slot_with_siblings(
    db: Session,
    device: Device,
    earliest_start: datetime,
    duration_minutes: int,
    order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)

    occupied = get_device_occupied_slots(db, device.id, exclude_order_id=order_id)

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

        changeover_minutes = 0
        if product_name:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)

        total_duration = timedelta(minutes=changeover_minutes + duration_minutes)

        if deadline and current_start + total_duration > deadline:
            return None

        day_end = calculate_available_end(current_start, device)
        if current_start + total_duration > day_end:
            next_day = current_start.date() + timedelta(days=1)
            current_start = datetime.combine(next_day, parse_time_str(device.daily_start))
            continue

        for (occ_start, occ_end, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if current_start < occ_end and current_start + total_duration > occ_start:
                current_start = occ_end
                moved = True
                break

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        if changeover_minutes > 0:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            new_changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)
            if new_changeover_minutes != changeover_minutes:
                changeover_minutes = new_changeover_minutes
                total_duration = timedelta(minutes=changeover_minutes + duration_minutes)
                if deadline and current_start + total_duration > deadline:
                    return None
                for (occ_start, occ_end, is_locked) in occupied:
                    if respect_locked and not is_locked:
                        continue
                    if current_start < occ_end and current_start + total_duration > occ_start:
                        current_start = occ_end
                        moved = True
                        break
                if moved:
                    current_start = get_next_working_start(current_start, device)
                    continue

        next_maint = find_next_maintenance_window(db, device.id, current_start)
        if next_maint:
            maint_start, maint_end, _ = next_maint
            if current_start >= maint_start and current_start < maint_end:
                current_start = maint_end
                moved = True
            elif current_start + total_duration > maint_start and current_start < maint_start:
                gap = maint_start - current_start
                if gap < total_duration:
                    current_start = maint_end
                    moved = True

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        next_fault = find_next_fault_window(db, device.id, current_start)
        if next_fault:
            fault_start, fault_end, _ = next_fault
            if current_start >= fault_start and current_start < fault_end:
                current_start = fault_end
                moved = True
            elif current_start + total_duration > fault_start and current_start < fault_start:
                gap = fault_start - current_start
                if gap < total_duration:
                    current_start = fault_end
                    moved = True

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        return current_start

    return None


def select_best_device_with_siblings(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    exclude_device_ids: Optional[List[int]] = None,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None
) -> Tuple[Optional[Device], Optional[datetime]]:
    devices = db.query(Device).filter(Device.device_type == device_type)
    
    if exclude_device_ids:
        devices = devices.filter(~Device.id.in_(exclude_device_ids))
    
    devices = devices.all()
    
    if not devices:
        return None, None

    available_devices = []
    for device in devices:
        if get_active_device_fault(db, device.id):
            continue
        if exclude_device_ids and device.id in exclude_device_ids:
            continue
        available_devices.append(device)

    if not available_devices:
        return None, None

    best_device = None
    best_start = None

    for device in available_devices:
        slot_start = find_earliest_slot_with_siblings(
            db, device, earliest_start, duration_minutes,
            order_id=order_id, respect_locked=respect_locked,
            sibling_entries=sibling_entries,
            product_name=product_name,
            deadline=deadline
        )
        if slot_start is not None:
            if best_start is None or slot_start < best_start:
                best_start = slot_start
                best_device = device
            elif slot_start == best_start and best_device is not None:
                device_load = _calculate_device_load(db, device.id)
                best_load = _calculate_device_load(db, best_device.id)
                if device_load < best_load:
                    best_device = device

    return best_device, best_start


def _check_delivery_plan_conflicts(
    db: Session,
    order: WorkOrder,
    batch_plans: List[Dict],
    created_sub_batches: List[SubBatch]
) -> None:
    from app.models import DeliveryPlan

    db.query(ConflictRecord).filter(
        ConflictRecord.order_id == order.id,
        ConflictRecord.conflict_type == "delivery_plan_delay",
        ConflictRecord.scenario_id.is_(None)
    ).delete(synchronize_session=False)

    delivery_plans = db.query(DeliveryPlan).filter(
        DeliveryPlan.order_id == order.id,
        DeliveryPlan.scenario_id.is_(None)
    ).order_by(DeliveryPlan.plan_index).all()

    if not delivery_plans:
        return

    plan_completion = {}
    for sb in created_sub_batches:
        if sb.delivery_plan_id and sb.actual_end_time:
            if sb.delivery_plan_id not in plan_completion:
                plan_completion[sb.delivery_plan_id] = sb.actual_end_time
            elif sb.actual_end_time > plan_completion[sb.delivery_plan_id]:
                plan_completion[sb.delivery_plan_id] = sb.actual_end_time

    for plan in delivery_plans:
        estimated = plan_completion.get(plan.id)
        if estimated and estimated > plan.expected_delivery_date:
            delay_seconds = (estimated - plan.expected_delivery_date).total_seconds()
            delay_minutes = int(delay_seconds / 60)
            delay_hours = delay_minutes // 60
            delay_days = delay_hours // 24
            if delay_days > 0:
                delay_human = f"{delay_days}天{delay_hours % 24}小时"
            elif delay_hours > 0:
                delay_human = f"{delay_hours}小时{delay_minutes % 60}分钟"
            else:
                delay_human = f"{delay_minutes}分钟"

            conflict_desc = (
                f"交付计划第{plan.plan_index}批延期: 计划交付{plan.expected_delivery_date.strftime('%Y-%m-%d %H:%M')}, "
                f"预计完工{estimated.strftime('%Y-%m-%d %H:%M')}, 延期约{delay_human} "
                f"(数量{plan.planned_quantity})"
            )

            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="delivery_plan_delay",
                description=conflict_desc
            )
            db.add(conflict)


def schedule_order_with_split(
    db: Session,
    order: WorkOrder,
    respect_locked: bool = True
) -> Dict:
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

    need_split, batch_plans, split_info = plan_split_batches(db, order, steps)
    num_batches = len(batch_plans) if need_split else 1

    materials_ok, material_shortages = check_materials_for_steps(db, steps, multiplier=num_batches)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}{db.query(Material).filter(Material.id == s['material_id']).first().unit if db.query(Material).filter(Material.id == s['material_id']).first() else ''}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="material_shortage",
            description=f"物料不足(共{num_batches}个子批次): {'; '.join(shortage_descs)}"
        )
        db.add(conflict)
        order.status = "failed"
        order.bottleneck_step = material_shortages[0]["material_name"]
        db.commit()
        return {
            "success": False,
            "message": f"物料库存不足(共{num_batches}个子批次): {'; '.join(shortage_descs)}",
            "bottleneck_step": material_shortages[0]["material_name"],
            "material_shortages": material_shortages
        }

    if not need_split:
        order.is_split = False
        order.total_sub_batches = 0
        db.flush()
        return schedule_order_original(db, order, respect_locked, steps, materials_already_checked=True, material_multiplier=1)

    order.is_split = True
    order.total_sub_batches = len(batch_plans)
    db.flush()

    created_sub_batches = []
    created_entries = []
    created_outsourcing_entries = []
    all_scheduled_entries_by_batch = []
    sibling_device_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_fixture_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_outsourcing_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_employee_entries: List[Tuple[int, datetime, datetime]] = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_fixture_type = None
    bottleneck_skill = None
    bottleneck_skill_level = None
    failed_batch_no = None
    failed_message = None

    try:
        for plan in batch_plans:
            sub_batch = SubBatch(
                order_id=order.id,
                batch_no=plan["batch_no"],
                quantity=plan["quantity"],
                status="pending",
                delivery_plan_id=plan.get("delivery_plan_id")
            )
            db.add(sub_batch)
            db.flush()
            created_sub_batches.append(sub_batch)

            success, entries, bn_step, bn_type, bn_fixture, outsourcing_results, bn_skill, bn_skill_level = _schedule_single_sub_batch(
                db, order, sub_batch, steps,
                respect_locked=respect_locked,
                sibling_device_entries=sibling_device_entries,
                sibling_fixture_entries=sibling_fixture_entries,
                sibling_outsourcing_entries=sibling_outsourcing_entries,
                sibling_employee_entries=sibling_employee_entries
            )

            if not success:
                bottleneck_step = bn_step
                bottleneck_type = bn_type
                bottleneck_fixture_type = bn_fixture
                bottleneck_skill = bn_skill
                bottleneck_skill_level = bn_skill_level
                failed_batch_no = plan["batch_no"]
                if bn_type == "fixture":
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败: 工装不足(类型: {bn_fixture})"
                elif bn_type == "device":
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败: 设备产能不足"
                elif bn_type == "staff":
                    skill_info = ""
                    if bn_skill:
                        skill_info = f"技能: {bn_skill}"
                        if bn_skill_level:
                            skill_info += f", 等级要求: L{bn_skill_level}"
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败: 人员不足({skill_info})"
                elif bn_type == "changeover":
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败: 换型时间导致超出截止时间"
                elif bn_type and "outsourcing" in bn_type:
                    detail = f"[{bn_type}] {bn_fixture}" if bn_fixture else f"[{bn_type}]"
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败: 外协瓶颈 {detail}"
                else:
                    failed_message = f"子批次 {plan['batch_no']} 在工序 '{bn_step}' 排产失败"
                break

            first_start = min(e["start_time"] for e in entries) if entries else None
            last_end = max(e["end_time"] for e in entries) if entries else None

            if outsourcing_results:
                for or_result in outsourcing_results:
                    last_node = max(or_result["nodes"], key=lambda n: n["end_time"])
                    if last_end is None or last_node["end_time"] > last_end:
                        last_end = last_node["end_time"]
                    first_node = min(or_result["nodes"], key=lambda n: n["start_time"])
                    if first_start is None or first_node["start_time"] < first_start:
                        first_start = first_node["start_time"]

            sub_batch.status = "scheduled"
            sub_batch.actual_start_time = first_start
            sub_batch.actual_end_time = last_end

            batch_entries_with_ids = []
            for entry in entries:
                db_entry = ScheduleEntry(
                    order_id=order.id,
                    sub_batch_id=sub_batch.id,
                    step_id=entry["step_id"],
                    device_id=entry["device_id"],
                    fixture_id=entry["fixture_id"],
                    operator_id=entry.get("operator_id"),
                    step_order=entry["step_order"],
                    step_name=entry["step_name"],
                    start_time=entry["start_time"],
                    end_time=entry["end_time"],
                    fixture_turn_over_end_time=entry["fixture_turn_over_end_time"],
                    changeover_start_time=entry.get("changeover_start_time"),
                    changeover_end_time=entry.get("changeover_end_time"),
                    changeover_minutes=entry.get("changeover_minutes", 0),
                    changeover_type=entry.get("changeover_type"),
                    prev_product_name=entry.get("prev_product_name"),
                )
                db.add(db_entry)
                db.flush()
                
                if entry.get("operator_id"):
                    assign_employee_to_entry(
                        db, entry["operator_id"], db_entry.id,
                        entry["start_time"], entry["end_time"]
                    )
                
                created_entries.append(db_entry)
                batch_entries_with_ids.append({
                    **entry,
                    "id": db_entry.id,
                    "sub_batch_id": sub_batch.id
                })

            for or_result in outsourcing_results:
                os_entries = create_outsourcing_schedule_entries(
                    db, order, sub_batch,
                    or_result["step"], or_result["factory"],
                    or_result["nodes"], plan["quantity"]
                )
                created_outsourcing_entries.extend(os_entries)

            all_scheduled_entries_by_batch.append({
                "sub_batch_id": sub_batch.id,
                "batch_no": plan["batch_no"],
                "quantity": plan["quantity"],
                "status": "scheduled",
                "schedule_entries": batch_entries_with_ids,
                "outsourcing_nodes": or_result["nodes"] if outsourcing_results else []
            })

        if bottleneck_step is not None:
            for entry in created_entries:
                db.delete(entry)
            for os_entry in created_outsourcing_entries:
                db.delete(os_entry)
            for sb in created_sub_batches:
                db.delete(sb)
            db.flush()

            conflict_desc = f"{failed_message}: 无法在截止时间前安排所有子批次，整体排产取消"
            if bottleneck_type == "fixture":
                conflict_desc += f" [工装瓶颈: {bottleneck_fixture_type}]"
            elif bottleneck_type == "device":
                conflict_desc += " [设备瓶颈]"
                from app.capacity_reservation_service import find_reservation_blockers
                route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
                if route:
                    bn_step_obj = next((s for s in sorted(route.steps, key=lambda s: s.step_order) if s.step_name == bottleneck_step), None)
                    if bn_step_obj:
                        for dev in db.query(Device).filter(Device.device_type == bn_step_obj.device_type).all():
                            blockers = find_reservation_blockers(db, dev.id, order.expected_start_time, order.deadline)
                            for b in blockers[:3]:
                                conflict_desc += f"; 设备{dev.name}被预留[{b['reservation_no']}]占用({b['product_name']}工序{b['step_name']})"
            elif bottleneck_type == "staff":
                if bottleneck_skill:
                    conflict_desc += f" [人员瓶颈: 缺少技能{bottleneck_skill}"
                    if bottleneck_skill_level:
                        conflict_desc += f"(等级要求L{bottleneck_skill_level})"
                    conflict_desc += "]"
                else:
                    conflict_desc += " [人员瓶颈]"
            elif bottleneck_type and "outsourcing" in bottleneck_type:
                conflict_desc += f" [外协瓶颈: {bottleneck_fixture_type}]"
            
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=conflict_desc
            )
            db.add(conflict)
            order.status = "failed"
            order.bottleneck_step = bottleneck_step
            order.is_split = False
            order.total_sub_batches = 0
            order.is_blocked = True
            order.blocked_reason = conflict_desc
            db.commit()
            return {
                "success": False,
                "message": conflict_desc,
                "bottleneck_step": bottleneck_step,
                "bottleneck_type": bottleneck_type,
                "bottleneck_fixture_type": bottleneck_fixture_type,
                "failed_batch_no": failed_batch_no
            }

        lock_materials_for_order(db, order.id, steps, multiplier=num_batches)

        _check_delivery_plan_conflicts(db, order, batch_plans, created_sub_batches)

        order.status = "scheduled"
        order.bottleneck_step = None
        db.commit()
        db.refresh(order)

        for sb in order.sub_batches:
            db.refresh(sb)

        return {
            "success": True,
            "message": f"工单排产成功，{split_info}",
            "is_split": True,
            "total_sub_batches": len(batch_plans),
            "split_info": split_info,
            "sub_batches": all_scheduled_entries_by_batch,
            "schedule_entries": order.schedule_entries,
            "bottleneck_step": None,
            "bottleneck_type": None,
            "bottleneck_fixture_type": None
        }

    except Exception as e:
        for entry in created_entries:
            db.delete(entry)
        for sb in created_sub_batches:
            db.delete(sb)
        db.flush()
        order.is_split = False
        order.total_sub_batches = 0
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="scheduling_failed",
            description=f"排产过程发生异常: {str(e)}，已回滚"
        )
        db.add(conflict)
        order.status = "failed"
        db.commit()
        raise


def schedule_order_original(
    db: Session,
    order: WorkOrder,
    respect_locked: bool = True,
    steps: Optional[List[ProcessStep]] = None,
    materials_already_checked: bool = False,
    material_multiplier: int = 1
) -> Dict:
    if steps is None:
        route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
        if not route:
            return {
                "success": False,
                "message": f"Product '{order.product_name}' has no process route defined",
                "bottleneck_step": None,
                "bottleneck_type": None
            }
        steps = sorted(route.steps, key=lambda s: s.step_order)
        if not steps:
            return {
                "success": False,
                "message": "Process route has no steps",
                "bottleneck_step": None,
                "bottleneck_type": None
            }

    if not materials_already_checked:
        materials_ok, material_shortages = check_materials_for_steps(db, steps, multiplier=material_multiplier)
        if not materials_ok:
            shortage_descs = [
                f"{s['material_name']}: 需要{s['needed']}{db.query(Material).filter(Material.id == s['material_id']).first().unit if db.query(Material).filter(Material.id == s['material_id']).first() else ''}, 可用{s['available']}, 缺{s['shortage']}"
                for s in material_shortages
            ]
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="material_shortage",
                description=f"物料不足: {'; '.join(shortage_descs)}"
            )
            db.add(conflict)
            order.status = "failed"
            order.bottleneck_step = material_shortages[0]["material_name"]
            db.commit()
            return {
                "success": False,
                "message": f"物料库存不足: {'; '.join(shortage_descs)}",
                "bottleneck_step": material_shortages[0]["material_name"],
                "bottleneck_type": "material",
                "material_shortages": material_shortages
            }

    prev_end_time = order.expected_start_time
    prev_step = None
    schedule_entries = []
    outsourcing_results = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_fixture_type = None
    bottleneck_skill = None
    bottleneck_skill_level = None
    sibling_employee_entries: List[Tuple[int, datetime, datetime]] = []

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        if step.is_outsource:
            success, nodes, factory, bn_type, bn_msg = schedule_outsourcing_step(
                db, order, None, step,
                quantity=order.total_quantity,
                earliest_start=earliest_start,
                deadline=order.deadline,
                exclude_order_id=order.id
            )

            if not success:
                bottleneck_step = step.step_name
                bottleneck_type = bn_type
                bottleneck_fixture_type = bn_msg
                break

            returned_node = next(n for n in nodes if n["node_type"] == "returned_waiting")

            outsourcing_results.append({
                "step": step,
                "factory": factory,
                "nodes": nodes
            })

            prev_end_time = returned_node["end_time"]
            prev_step = step
            continue

        device, fixture, start_time, bn_type, bn_fixture, employee, bn_skill, bn_skill_level = select_best_device_and_fixture(
            db, step, earliest_start, step.duration_minutes,
            exclude_order_id=order.id, respect_locked=respect_locked,
            sibling_employee_entries=sibling_employee_entries,
            product_name=order.product_name,
            deadline=order.deadline,
            order_priority=order.priority
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            bottleneck_type = bn_type
            bottleneck_fixture_type = bn_fixture
            bottleneck_skill = bn_skill
            bottleneck_skill_level = bn_skill_level
            break

        prev_product = get_previous_product_on_device(db, device.id, start_time)
        changeover_minutes, changeover_type = calculate_changeover_minutes(
            db, device.id, prev_product, order.product_name
        )

        total_end_time = start_time + timedelta(minutes=changeover_minutes + step.duration_minutes)
        if total_end_time > order.deadline:
            bottleneck_step = step.step_name
            if changeover_minutes > 0:
                bottleneck_type = "changeover"
            else:
                bottleneck_type = "deadline"
            break

        changeover_start_time = None
        changeover_end_time = None
        if changeover_minutes > 0:
            changeover_start_time = start_time
            changeover_end_time = start_time + timedelta(minutes=changeover_minutes)
            start_time = changeover_end_time

        end_time = start_time + timedelta(minutes=step.duration_minutes)

        turn_over_end_time = None
        if fixture:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if fixture_type and fixture_type.turn_over_minutes > 0:
                turn_over_end_time = end_time + timedelta(minutes=fixture_type.turn_over_minutes)

        schedule_entries.append({
            "step_id": step.id,
            "device_id": device.id,
            "fixture_id": fixture.id if fixture else None,
            "operator_id": employee.id if employee else None,
            "step_order": step.step_order,
            "step_name": step.step_name,
            "start_time": start_time,
            "end_time": end_time,
            "fixture_turn_over_end_time": turn_over_end_time,
            "changeover_start_time": changeover_start_time,
            "changeover_end_time": changeover_end_time,
            "changeover_minutes": changeover_minutes,
            "changeover_type": changeover_type,
            "prev_product_name": prev_product,
        })

        if employee:
            sibling_employee_entries.append((employee.id, start_time, end_time))

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        from app.capacity_reservation_service import find_reservation_blockers, find_fixture_reservation_blockers
        reservation_blocker_desc = ""

        if bottleneck_type == "device":
            route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
            if route:
                bn_step_obj = next((s for s in sorted(route.steps, key=lambda s: s.step_order) if s.step_name == bottleneck_step), None)
                if bn_step_obj:
                    for dev in db.query(Device).filter(Device.device_type == bn_step_obj.device_type).all():
                        blockers = find_reservation_blockers(
                            db, dev.id,
                            order.expected_start_time,
                            order.deadline
                        )
                        if blockers:
                            for b in blockers[:3]:
                                reservation_blocker_desc += f" 设备{dev.name}被预留[{b['reservation_no']}]占用({b['start_time'].strftime('%m-%d %H:%M')}-{b['end_time'].strftime('%m-%d %H:%M')},{b['product_name']}工序{b['step_name']});"

        conflict_desc = f"Bottleneck at step '{bottleneck_step}'"
        if bottleneck_type == "fixture":
            conflict_desc += f": 工装不足(类型: {bottleneck_fixture_type})"
        elif bottleneck_type == "device":
            conflict_desc += ": 设备产能不足"
            if reservation_blocker_desc:
                conflict_desc += f"，其中包含产能预留占用:{reservation_blocker_desc}"
        elif bottleneck_type == "staff":
            if bottleneck_skill:
                conflict_desc += f": 人员不足，缺少技能{bottleneck_skill}"
                if bottleneck_skill_level:
                    conflict_desc += f"(等级要求L{bottleneck_skill_level})"
            else:
                conflict_desc += ": 人员不足"
        elif bottleneck_type == "deadline":
            conflict_desc += ": 无法在截止时间前完成"
        elif bottleneck_type == "changeover":
            conflict_desc += ": 换型时间导致无法在截止时间前完成"
        elif bottleneck_type and "outsourcing" in bottleneck_type:
            conflict_desc += f": 外协瓶颈({bottleneck_type}): {bottleneck_fixture_type}"
        conflict_desc += ": cannot schedule before deadline"
        
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="scheduling_failed",
            description=conflict_desc
        )
        db.add(conflict)
        order.status = "failed"
        order.bottleneck_step = bottleneck_step
        order.is_blocked = True
        order.blocked_reason = conflict_desc
        db.commit()
        return {
            "success": False,
            "message": f"Cannot schedule order: bottleneck at step '{bottleneck_step}'",
            "bottleneck_step": bottleneck_step,
            "bottleneck_type": bottleneck_type,
            "bottleneck_fixture_type": bottleneck_fixture_type
        }

    for entry in schedule_entries:
        db_entry = ScheduleEntry(
            order_id=order.id,
            sub_batch_id=None,
            step_id=entry["step_id"],
            device_id=entry["device_id"],
            fixture_id=entry["fixture_id"],
            operator_id=entry.get("operator_id"),
            step_order=entry["step_order"],
            step_name=entry["step_name"],
            start_time=entry["start_time"],
            end_time=entry["end_time"],
            fixture_turn_over_end_time=entry["fixture_turn_over_end_time"],
            changeover_start_time=entry.get("changeover_start_time"),
            changeover_end_time=entry.get("changeover_end_time"),
            changeover_minutes=entry.get("changeover_minutes", 0),
            changeover_type=entry.get("changeover_type"),
            prev_product_name=entry.get("prev_product_name"),
        )
        db.add(db_entry)
        db.flush()
        
        if entry.get("operator_id"):
            assign_employee_to_entry(
                db, entry["operator_id"], db_entry.id,
                entry["start_time"], entry["end_time"]
            )

    for or_result in outsourcing_results:
        create_outsourcing_schedule_entries(
            db, order, None,
            or_result["step"], or_result["factory"],
            or_result["nodes"], order.total_quantity
        )

    lock_materials_for_order(db, order.id, steps, multiplier=material_multiplier)

    order.status = "scheduled"
    order.bottleneck_step = None
    db.commit()
    db.refresh(order)

    return {
        "success": True,
        "message": "Order scheduled successfully",
        "is_split": False,
        "total_sub_batches": 0,
        "schedule_entries": order.schedule_entries,
        "bottleneck_step": None,
        "bottleneck_type": None,
        "bottleneck_fixture_type": None
    }


def schedule_order(db: Session, order: WorkOrder, respect_locked: bool = True) -> Dict:
    return schedule_order_with_split(db, order, respect_locked)


def get_order_summary(db: Session, order_id: int) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    total_sub_batches = order.total_sub_batches if order.is_split else 0
    completed_sub_batches = 0
    estimated_completion = None

    if order.is_split and order.sub_batches:
        completed_sub_batches = sum(1 for sb in order.sub_batches if sb.status == "completed")
        end_times = [sb.actual_end_time for sb in order.sub_batches if sb.actual_end_time]
        if end_times:
            estimated_completion = max(end_times)
    elif order.schedule_entries:
        end_times = [e.end_time for e in order.schedule_entries]
        estimated_completion = max(end_times) if end_times else None

    if total_sub_batches > 0:
        progress = (completed_sub_batches / total_sub_batches) * 100
    else:
        if order.status == "scheduled":
            progress = 50.0
        elif order.status == "completed":
            progress = 100.0
        elif order.status == "failed":
            progress = 0.0
        else:
            progress = 0.0

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "product_name": order.product_name,
        "total_quantity": order.total_quantity,
        "status": order.status,
        "is_split": order.is_split,
        "total_sub_batches": total_sub_batches,
        "completed_sub_batches": completed_sub_batches,
        "progress_percent": round(progress, 2),
        "expected_start_time": order.expected_start_time,
        "deadline": order.deadline,
        "estimated_completion_time": estimated_completion,
        "bottleneck_step": order.bottleneck_step
    }


def release_sub_batches_for_order(db: Session, order_id: int, respect_delivery_lock: bool = True) -> int:
    query = db.query(SubBatch).filter(SubBatch.order_id == order_id)
    if respect_delivery_lock:
        query = query.filter(SubBatch.delivered_quantity == 0)
    sub_batches = query.all()
    count = len(sub_batches)
    for sb in sub_batches:
        sb.status = "cancelled"
    db.flush()
    return count


def get_route_steps_for_order(db: Session, order: WorkOrder) -> List[ProcessStep]:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
    if not route:
        return []
    return sorted(route.steps, key=lambda s: s.step_order)


def get_or_create_sub_batch_for_progress(db: Session, order_id: int, sub_batch_id: Optional[int]) -> Tuple[Optional[SubBatch], Optional[str]]:
    if sub_batch_id:
        sub_batch = db.query(SubBatch).filter(SubBatch.id == sub_batch_id, SubBatch.order_id == order_id).first()
        if not sub_batch:
            return None, f"子批次 {sub_batch_id} 不存在或不属于工单 {order_id}"
        return sub_batch, None
    
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None, f"工单 {order_id} 不存在"
    
    if order.is_split and order.sub_batches:
        return None, "工单已拆分，请指定 sub_batch_id"
    
    if not order.is_split and not order.sub_batches:
        sub_batch = SubBatch(
            order_id=order_id,
            batch_no=f"{order.order_no}-001",
            quantity=order.total_quantity,
            status="scheduled"
        )
        db.add(sub_batch)
        db.flush()
        
        for entry in order.schedule_entries:
            entry.sub_batch_id = sub_batch.id
        
        order.is_split = True
        order.total_sub_batches = 1
        db.flush()
        return sub_batch, None
    
    if order.sub_batches:
        return order.sub_batches[0], None
    
    return None, "无法确定子批次，请指定 sub_batch_id"


def validate_step_report(
    db: Session,
    sub_batch: SubBatch,
    step_order: int,
    steps: List[ProcessStep]
) -> Tuple[bool, Optional[str]]:
    if step_order < 1 or step_order > len(steps):
        return False, f"工序序号必须在 1 到 {len(steps)} 之间"
    
    replenish_from = sub_batch.replenish_from_step or 1
    if step_order < replenish_from:
        return False, f"该补产子批次从工序 {replenish_from} 开始，不能上报工序 {step_order}"
    
    existing_progress = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sub_batch.id,
        SubBatchStepProgress.step_order == step_order
    ).first()
    
    if existing_progress and existing_progress.is_completed:
        return False, f"工序 {step_order} 已完成，不能重复上报"
    
    effective_prev = max(replenish_from, step_order - 1)
    if step_order > replenish_from:
        prev_progress = db.query(SubBatchStepProgress).filter(
            SubBatchStepProgress.sub_batch_id == sub_batch.id,
            SubBatchStepProgress.step_order == step_order - 1
        ).first()
        
        if not prev_progress or not prev_progress.is_completed:
            return False, f"必须先完成工序 {step_order - 1} 才能上报工序 {step_order}"
    
    return True, None


def report_step_progress(
    db: Session,
    sub_batch_id: Optional[int],
    order_id: Optional[int],
    step_order: int,
    actual_completion_time: datetime,
    good_quantity: int
) -> Tuple[bool, Dict]:
    if not sub_batch_id and not order_id:
        return False, {"message": "必须指定 sub_batch_id 或 order_id"}
    
    if order_id and not sub_batch_id:
        sub_batch, error = get_or_create_sub_batch_for_progress(db, order_id, None)
        if error:
            return False, {"message": error}
        sub_batch_id = sub_batch.id
    elif sub_batch_id:
        sub_batch = db.query(SubBatch).filter(SubBatch.id == sub_batch_id).first()
        if not sub_batch:
            return False, {"message": f"子批次 {sub_batch_id} 不存在"}
        order_id = sub_batch.order_id
    else:
        return False, {"message": "参数错误"}
    
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return False, {"message": f"工单 {order_id} 不存在"}
    
    if order.status not in ["scheduled", "in_progress"]:
        return False, {"message": f"工单状态为 {order.status}，不能上报进度"}
    
    steps = get_route_steps_for_order(db, order)
    if not steps:
        return False, {"message": "产品没有工艺路线"}
    
    valid, error = validate_step_report(db, sub_batch, step_order, steps)
    if not valid:
        return False, {"message": error}
    
    step = next((s for s in steps if s.step_order == step_order), None)
    if not step:
        return False, {"message": f"工序 {step_order} 不存在"}
    
    scrap_quantity = max(0, sub_batch.quantity - good_quantity)
    
    progress = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sub_batch_id,
        SubBatchStepProgress.step_order == step_order
    ).first()
    
    if not progress:
        progress = SubBatchStepProgress(
            sub_batch_id=sub_batch_id,
            step_order=step_order,
            step_name=step.step_name,
            step_id=step.id,
            good_quantity=good_quantity,
            scrap_quantity=scrap_quantity,
            is_completed=True,
            actual_completion_time=actual_completion_time
        )
        db.add(progress)
    else:
        progress.good_quantity = good_quantity
        progress.scrap_quantity = scrap_quantity
        progress.is_completed = True
        progress.actual_completion_time = actual_completion_time
    
    schedule_entry = db.query(ScheduleEntry).filter(
        ScheduleEntry.sub_batch_id == sub_batch_id,
        ScheduleEntry.step_order == step_order
    ).first()
    
    if schedule_entry:
        schedule_entry.is_completed = True
        schedule_entry.actual_completion_time = actual_completion_time
    
    if order.status == "scheduled":
        order.status = "in_progress"
    
    db.flush()
    
    result = {
        "sub_batch_id": sub_batch_id,
        "step_order": step_order,
        "good_quantity": good_quantity,
        "scrap_quantity": scrap_quantity,
        "is_completed": True,
        "replenishment_created": False,
        "replenishment_sub_batch_id": None,
        "replenishment_batch_no": None
    }
    
    if scrap_quantity > 0:
        replenish_success, replenish_result = create_replenishment_sub_batch(
            db, sub_batch, step_order, scrap_quantity, steps
        )
        if replenish_success:
            result["replenishment_created"] = True
            result["replenishment_sub_batch_id"] = replenish_result.get("sub_batch_id")
            result["replenishment_batch_no"] = replenish_result.get("batch_no")
        else:
            db.rollback()
            return False, {"message": replenish_result.get("message", "补产失败")}
    
    if step_order == len(steps):
        sub_batch.status = "completed"
        sub_batch.actual_end_time = actual_completion_time
    
    db.flush()
    
    update_order_progress(db, order_id)
    
    order_summary = get_order_summary(db, order_id)
    result["order_progress"] = order_summary
    
    db.commit()
    
    return True, result


def create_replenishment_sub_batch(
    db: Session,
    original_sub_batch: SubBatch,
    from_step_order: int,
    quantity: int,
    all_steps: List[ProcessStep]
) -> Tuple[bool, Dict]:
    if original_sub_batch.replenish_level >= 3:
        return False, {"message": f"补产层数已达上限(3层)，请人工介入处理"}
    
    order = db.query(WorkOrder).filter(WorkOrder.id == original_sub_batch.order_id).first()
    if not order:
        return False, {"message": "工单不存在"}
    
    existing_replenishments = db.query(SubBatch).filter(
        SubBatch.parent_sub_batch_id == original_sub_batch.id,
        SubBatch.replenish_from_step == from_step_order
    ).count()
    
    batch_no = f"{original_sub_batch.batch_no}-R{original_sub_batch.replenish_level + 1}-{str(existing_replenishments + 1).zfill(2)}"
    
    remaining_steps = [s for s in all_steps if s.step_order >= from_step_order]
    
    materials_ok, material_shortages = check_materials_for_steps(db, remaining_steps, multiplier=1)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="replenishment_material_shortage",
            description=f"补产子批次 {batch_no} 物料不足: {'; '.join(shortage_descs)}"
        )
        db.add(conflict)
        return False, {"message": f"补产物料不足: {'; '.join(shortage_descs)}"}
    
    replenish_sub_batch = SubBatch(
        order_id=order.id,
        batch_no=batch_no,
        quantity=quantity,
        status="pending",
        parent_sub_batch_id=original_sub_batch.id,
        is_replenishment=True,
        replenish_level=original_sub_batch.replenish_level + 1,
        replenish_from_step=from_step_order
    )
    db.add(replenish_sub_batch)
    db.flush()
    
    for step in all_steps:
        if step.step_order < from_step_order:
            pseudo_progress = SubBatchStepProgress(
                sub_batch_id=replenish_sub_batch.id,
                step_order=step.step_order,
                step_name=step.step_name,
                step_id=step.id,
                good_quantity=quantity,
                scrap_quantity=0,
                is_completed=True,
                actual_completion_time=original_sub_batch.actual_end_time or datetime.utcnow()
            )
            db.add(pseudo_progress)
    db.flush()
    
    all_scheduled_entries = []
    sibling_device_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_fixture_entries: List[Tuple[int, datetime, datetime]] = []
    
    prev_end_time = max(
        order.expected_start_time,
        original_sub_batch.actual_end_time or datetime.utcnow()
    )
    prev_step = None
    
    scheduled_entries = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_fixture_type = None
    
    for step in remaining_steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)
        
        device, fixture, start_time, bn_type, bn_fixture = select_best_device_and_fixture(
            db, step, earliest_start, step.duration_minutes,
            exclude_order_id=order.id,
            respect_locked=True,
            sibling_device_entries=sibling_device_entries,
            sibling_fixture_entries=sibling_fixture_entries
        )
        
        if device is None or start_time is None:
            bottleneck_step = step.step_name
            bottleneck_type = bn_type
            bottleneck_fixture_type = bn_fixture
            break
        
        end_time = start_time + timedelta(minutes=step.duration_minutes)
        
        turn_over_end_time = None
        if fixture:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if fixture_type and fixture_type.turn_over_minutes > 0:
                turn_over_end_time = end_time + timedelta(minutes=fixture_type.turn_over_minutes)
        
        scheduled_entries.append({
            "step_id": step.id,
            "device_id": device.id,
            "fixture_id": fixture.id if fixture else None,
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
        db.delete(replenish_sub_batch)
        db.flush()
        error_msg = f"补产子批次排产失败，工序 '{bottleneck_step}' 无法安排"
        if bottleneck_type == "fixture":
            error_msg += f": 工装不足(类型: {bottleneck_fixture_type})"
        elif bottleneck_type == "device":
            error_msg += ": 设备产能不足"
        return False, {"message": error_msg}
    
    for entry in scheduled_entries:
        db_entry = ScheduleEntry(
            order_id=order.id,
            sub_batch_id=replenish_sub_batch.id,
            step_id=entry["step_id"],
            device_id=entry["device_id"],
            fixture_id=entry["fixture_id"],
            step_order=entry["step_order"],
            step_name=entry["step_name"],
            start_time=entry["start_time"],
            end_time=entry["end_time"],
            fixture_turn_over_end_time=entry["fixture_turn_over_end_time"],
        )
        db.add(db_entry)
        all_scheduled_entries.append(db_entry)
    
    lock_materials_for_order(db, order.id, remaining_steps, multiplier=1)
    
    replenish_sub_batch.status = "scheduled"
    replenish_sub_batch.actual_start_time = min(e["start_time"] for e in scheduled_entries) if scheduled_entries else None
    replenish_sub_batch.actual_end_time = max(e["end_time"] for e in scheduled_entries) if scheduled_entries else None
    
    order.total_sub_batches += 1
    
    db.flush()
    
    return True, {
        "sub_batch_id": replenish_sub_batch.id,
        "batch_no": batch_no,
        "quantity": quantity,
        "from_step_order": from_step_order,
        "schedule_entries": all_scheduled_entries
    }


def update_order_progress(db: Session, order_id: int) -> None:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return
    
    steps = get_route_steps_for_order(db, order)
    total_steps = len(steps)
    
    if total_steps == 0:
        return
    
    sub_batches = db.query(SubBatch).filter(
        SubBatch.order_id == order_id,
        SubBatch.status != "cancelled"
    ).all()
    
    if not sub_batches and order.schedule_entries:
        get_or_create_sub_batch_for_progress(db, order_id, None)
        db.flush()
        sub_batches = db.query(SubBatch).filter(
            SubBatch.order_id == order_id,
            SubBatch.status != "cancelled"
        ).all()
    
    total_sub_batches = len(sub_batches)
    completed_sub_batches = 0
    
    all_completed = True
    
    for sb in sub_batches:
        sb_progresses = db.query(SubBatchStepProgress).filter(
            SubBatchStepProgress.sub_batch_id == sb.id,
            SubBatchStepProgress.is_completed == True
        ).all()
        
        completed_steps_count = len(sb_progresses)
        
        if completed_steps_count >= total_steps:
            completed_sub_batches += 1
            if sb.status != "completed":
                sb.status = "completed"
        else:
            all_completed = False
    
    if all_completed and total_sub_batches > 0:
        order.status = "completed"
        max_end_time = None
        for sb in sub_batches:
            if sb.actual_end_time:
                if max_end_time is None or sb.actual_end_time > max_end_time:
                    max_end_time = sb.actual_end_time
        if max_end_time:
            for sb in sub_batches:
                if not sb.actual_end_time:
                    sb.actual_end_time = max_end_time
    
    db.flush()


def get_order_summary(db: Session, order_id: int) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None
    
    steps = get_route_steps_for_order(db, order)
    total_steps = len(steps)
    
    sub_batches = db.query(SubBatch).filter(
        SubBatch.order_id == order_id,
        SubBatch.status != "cancelled"
    ).all()
    
    if not sub_batches and order.schedule_entries:
        get_or_create_sub_batch_for_progress(db, order_id, None)
        db.flush()
        sub_batches = db.query(SubBatch).filter(
            SubBatch.order_id == order_id,
            SubBatch.status != "cancelled"
        ).all()
    
    total_sub_batches = len(sub_batches)
    completed_sub_batches = 0
    total_completed_steps = 0
    estimated_completion = None
    
    for sb in sub_batches:
        sb_progresses = db.query(SubBatchStepProgress).filter(
            SubBatchStepProgress.sub_batch_id == sb.id,
            SubBatchStepProgress.is_completed == True
        ).all()
        
        completed_steps_count = len(sb_progresses)
        total_completed_steps += completed_steps_count
        
        if completed_steps_count >= total_steps and total_steps > 0:
            completed_sub_batches += 1
        
        if sb.actual_end_time:
            if estimated_completion is None or sb.actual_end_time > estimated_completion:
                estimated_completion = sb.actual_end_time
    
    if not estimated_completion and order.schedule_entries:
        end_times = [e.end_time for e in order.schedule_entries]
        estimated_completion = max(end_times) if end_times else None
    
    total_expected_steps = total_sub_batches * total_steps if total_steps > 0 else 0
    
    if total_expected_steps > 0:
        progress = (total_completed_steps / total_expected_steps) * 100
    elif total_sub_batches > 0:
        progress = (completed_sub_batches / total_sub_batches) * 100
    else:
        if order.status == "scheduled" or order.status == "in_progress":
            progress = 50.0
        elif order.status == "completed":
            progress = 100.0
        elif order.status == "failed":
            progress = 0.0
        else:
            progress = 0.0
    
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "product_name": order.product_name,
        "total_quantity": order.total_quantity,
        "status": order.status,
        "is_blocked": order.is_blocked,
        "blocked_reason": order.blocked_reason,
        "is_split": order.is_split or total_sub_batches > 0,
        "total_sub_batches": total_sub_batches,
        "completed_sub_batches": completed_sub_batches,
        "total_steps": total_steps,
        "completed_steps": total_completed_steps,
        "progress_percent": round(progress, 2),
        "expected_start_time": order.expected_start_time,
        "deadline": order.deadline,
        "estimated_completion_time": estimated_completion,
        "bottleneck_step": order.bottleneck_step
    }


def get_sub_batch_progress(db: Session, sub_batch_id: int) -> Optional[Dict]:
    sub_batch = db.query(SubBatch).filter(SubBatch.id == sub_batch_id).first()
    if not sub_batch:
        return None
    
    order = db.query(WorkOrder).filter(WorkOrder.id == sub_batch.order_id).first()
    steps = get_route_steps_for_order(db, order) if order else []
    
    progresses = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sub_batch_id
    ).order_by(SubBatchStepProgress.step_order).all()
    
    progress_map = {p.step_order: p for p in progresses}
    
    step_details = []
    for step in steps:
        p = progress_map.get(step.step_order)
        step_details.append({
            "step_order": step.step_order,
            "step_name": step.step_name,
            "step_id": step.id,
            "is_completed": p.is_completed if p else False,
            "actual_completion_time": p.actual_completion_time if p else None,
            "good_quantity": p.good_quantity if p else 0,
            "scrap_quantity": p.scrap_quantity if p else 0,
            "reported_at": p.reported_at if p else None
        })
    
    return {
        "sub_batch_id": sub_batch.id,
        "batch_no": sub_batch.batch_no,
        "quantity": sub_batch.quantity,
        "status": sub_batch.status,
        "is_replenishment": sub_batch.is_replenishment,
        "replenish_level": sub_batch.replenish_level,
        "parent_sub_batch_id": sub_batch.parent_sub_batch_id,
        "replenish_from_step": sub_batch.replenish_from_step,
        "total_steps": len(steps),
        "completed_steps": sum(1 for p in progresses if p.is_completed),
        "step_details": step_details
    }


def calculate_device_available_minutes(
    db: Session,
    device: Device,
    start_dt: datetime,
    end_dt: datetime
) -> int:
    start_t = parse_time_str(device.daily_start)
    end_t = parse_time_str(device.daily_end)
    daily_minutes = int((datetime.combine(date.today(), end_t) - datetime.combine(date.today(), start_t)).total_seconds() / 60)

    total_available = 0
    current_date = start_dt.date()
    end_date = end_dt.date()

    while current_date <= end_date:
        day_start = datetime.combine(current_date, start_t)
        day_end = datetime.combine(current_date, end_t)

        effective_start = max(day_start, start_dt)
        effective_end = min(day_end, end_dt)

        if effective_end > effective_start:
            day_minutes = int((effective_end - effective_start).total_seconds() / 60)
            total_available += day_minutes

            maint_windows = get_maintenance_windows_in_range(
                db, device.id, effective_start, effective_end
            )
            for (mw_start, mw_end, _) in maint_windows:
                mw_eff_start = max(mw_start, effective_start)
                mw_eff_end = min(mw_end, effective_end)
                if mw_eff_end > mw_eff_start:
                    total_available -= int((mw_eff_end - mw_eff_start).total_seconds() / 60)

        current_date += timedelta(days=1)

    return max(0, total_available)


def calculate_device_scheduled_minutes(
    db: Session,
    device_id: int,
    start_dt: datetime,
    end_dt: datetime
) -> Tuple[int, List[Tuple[datetime, datetime]]]:
    entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time < end_dt,
        ScheduleEntry.end_time > start_dt
    ).order_by(ScheduleEntry.start_time).all()

    total_scheduled = 0
    scheduled_intervals = []

    for entry in entries:
        eff_start = max(entry.start_time, start_dt)
        eff_end = min(entry.end_time, end_dt)
        if eff_end > eff_start:
            minutes = int((eff_end - eff_start).total_seconds() / 60)
            total_scheduled += minutes
            scheduled_intervals.append((eff_start, eff_end))

    return total_scheduled, scheduled_intervals


def find_idle_periods(
    db: Session,
    device: Device,
    start_dt: datetime,
    end_dt: datetime,
    scheduled_intervals: List[Tuple[datetime, datetime]],
    min_idle_minutes: int = 30
) -> List[Dict]:
    start_t = parse_time_str(device.daily_start)
    end_t = parse_time_str(device.daily_end)

    busy_intervals = list(scheduled_intervals)

    maint_windows = get_maintenance_windows_in_range(db, device.id, start_dt, end_dt)
    for (mw_start, mw_end, _) in maint_windows:
        busy_intervals.append((mw_start, mw_end))

    busy_intervals.sort(key=lambda x: x[0])

    merged = []
    for interval in busy_intervals:
        if not merged:
            merged.append(list(interval))
        else:
            last = merged[-1]
            if interval[0] <= last[1]:
                last[1] = max(last[1], interval[1])
            else:
                merged.append(list(interval))

    idle_periods = []
    current_date = start_dt.date()
    end_date = end_dt.date()

    while current_date <= end_date:
        day_start = datetime.combine(current_date, start_t)
        day_end = datetime.combine(current_date, end_t)

        eff_day_start = max(day_start, start_dt)
        eff_day_end = min(day_end, end_dt)

        if eff_day_end > eff_day_start:
            cursor = eff_day_start

            for (busy_start, busy_end) in merged:
                if busy_end <= eff_day_start:
                    continue
                if busy_start >= eff_day_end:
                    break

                if busy_start > cursor:
                    idle_duration = int((busy_start - cursor).total_seconds() / 60)
                    if idle_duration >= min_idle_minutes:
                        idle_periods.append({
                            "start_time": cursor,
                            "end_time": busy_start,
                            "duration_minutes": idle_duration
                        })

                cursor = max(cursor, busy_end)

            if cursor < eff_day_end:
                idle_duration = int((eff_day_end - cursor).total_seconds() / 60)
                if idle_duration >= min_idle_minutes:
                    idle_periods.append({
                        "start_time": cursor,
                        "end_time": eff_day_end,
                        "duration_minutes": idle_duration
                    })

        current_date += timedelta(days=1)

    return idle_periods


def calculate_avg_waiting_time(
    db: Session,
    device_id: int,
    start_dt: datetime,
    end_dt: datetime
) -> float:
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order),
        joinedload(ScheduleEntry.sub_batch)
    ).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time >= start_dt,
        ScheduleEntry.start_time <= end_dt,
        ScheduleEntry.step_order > 1
    ).all()

    if not entries:
        return 0.0

    total_waiting = 0
    count = 0

    for entry in entries:
        order = entry.order
        if not order:
            continue

        prev_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order.id,
            ScheduleEntry.step_order == entry.step_order - 1
        ).all()

        for prev_entry in prev_entries:
            waiting = int((entry.start_time - prev_entry.end_time).total_seconds() / 60)
            if waiting > 0:
                total_waiting += waiting
                count += 1

    return round(total_waiting / count, 2) if count > 0 else 0.0


def calculate_efficiency_stats(
    db: Session,
    start_dt: datetime,
    end_dt: datetime
) -> Dict:
    if (end_dt - start_dt).days > 90:
        return {
            "success": False,
            "message": "时间范围不能超过90天"
        }

    devices = db.query(Device).order_by(Device.id).all()

    device_efficiencies = []
    type_efficiencies_map = {}

    for device in devices:
        available_minutes = calculate_device_available_minutes(db, device, start_dt, end_dt)
        scheduled_minutes, scheduled_intervals = calculate_device_scheduled_minutes(
            db, device.id, start_dt, end_dt
        )
        utilization_rate = round(scheduled_minutes / available_minutes, 4) if available_minutes > 0 else 0.0
        idle_periods = find_idle_periods(db, device, start_dt, end_dt, scheduled_intervals)
        avg_waiting = calculate_avg_waiting_time(db, device.id, start_dt, end_dt)

        dev_eff = {
            "device_id": device.id,
            "device_name": device.name,
            "device_type": device.device_type,
            "utilization_rate": utilization_rate,
            "scheduled_minutes": scheduled_minutes,
            "available_minutes": available_minutes,
            "idle_periods": idle_periods,
            "avg_waiting_time_minutes": avg_waiting
        }
        device_efficiencies.append(dev_eff)

        if device.device_type not in type_efficiencies_map:
            type_efficiencies_map[device.device_type] = []
        type_efficiencies_map[device.device_type].append(dev_eff)

    device_type_efficiencies = []
    for dtype, devs in type_efficiencies_map.items():
        rates = [d["utilization_rate"] for d in devs]
        avg_rate = round(sum(rates) / len(rates), 4) if rates else 0.0
        max_diff = round(max(rates) - min(rates), 4) if len(rates) >= 2 else 0.0
        device_type_efficiencies.append({
            "device_type": dtype,
            "device_count": len(devs),
            "avg_utilization_rate": avg_rate,
            "max_utilization_diff": max_diff,
            "devices": devs
        })

    return {
        "success": True,
        "start_time": start_dt,
        "end_time": end_dt,
        "total_devices": len(devices),
        "device_efficiencies": device_efficiencies,
        "device_type_efficiencies": device_type_efficiencies
    }


def _simulate_find_earliest_slot(
    device: Device,
    earliest_start: datetime,
    duration_minutes: int,
    occupied_slots: List[Tuple[datetime, datetime]],
    db: Session,
    product_name: Optional[str] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)

    all_occupied = list(occupied_slots)
    all_occupied.sort(key=lambda x: x[0])

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

        changeover_minutes = 0
        if product_name:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)

        total_duration = timedelta(minutes=changeover_minutes + duration_minutes)

        day_end = calculate_available_end(current_start, device)
        if current_start + total_duration > day_end:
            next_day = current_start.date() + timedelta(days=1)
            current_start = datetime.combine(next_day, parse_time_str(device.daily_start))
            continue

        for (occ_start, occ_end) in all_occupied:
            if current_start < occ_end and current_start + total_duration > occ_start:
                current_start = occ_end
                moved = True
                break

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        if changeover_minutes > 0:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            new_changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)
            if new_changeover_minutes != changeover_minutes:
                changeover_minutes = new_changeover_minutes
                total_duration = timedelta(minutes=changeover_minutes + duration_minutes)
                for (occ_start, occ_end) in all_occupied:
                    if current_start < occ_end and current_start + total_duration > occ_start:
                        current_start = occ_end
                        moved = True
                        break
                if moved:
                    current_start = get_next_working_start(current_start, device)
                    continue

        next_maint = find_next_maintenance_window(db, device.id, current_start)
        if next_maint:
            maint_start, maint_end, _ = next_maint
            if current_start >= maint_start and current_start < maint_end:
                current_start = maint_end
                moved = True
            elif current_start + total_duration > maint_start and current_start < maint_start:
                gap = maint_start - current_start
                if gap < total_duration:
                    current_start = maint_end
                    moved = True

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        next_fault = find_next_fault_window(db, device.id, current_start)
        if next_fault:
            fault_start, fault_end, _ = next_fault
            if current_start >= fault_start and current_start < fault_end:
                current_start = fault_end
                moved = True
            elif current_start + total_duration > fault_start and current_start < fault_start:
                gap = fault_start - current_start
                if gap < total_duration:
                    current_start = fault_end
                    moved = True

        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        return current_start

    return None


def _simulate_select_best_device(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    all_occupied: Dict[int, List[Tuple[datetime, datetime]]],
    product_name: Optional[str] = None
) -> Tuple[Optional[Device], Optional[datetime]]:
    devices = db.query(Device).filter(Device.device_type == device_type).all()
    if not devices:
        return None, None

    best_device = None
    best_start = None

    for device in devices:
        dev_occupied = all_occupied.get(device.id, [])
        slot_start = _simulate_find_earliest_slot(
            device, earliest_start, duration_minutes, dev_occupied, db,
            product_name=product_name
        )
        if slot_start is not None:
            if best_start is None or slot_start < best_start:
                best_start = slot_start
                best_device = device

    return best_device, best_start


def simulate_schedule_order(
    db: Session,
    product_name: str,
    quantity: int,
    expected_start_time: datetime,
    all_occupied: Dict[int, List[Tuple[datetime, datetime]]],
    deadline_days: int = 30
) -> Dict:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not route:
        return {
            "success": False,
            "reason": f"产品 '{product_name}' 没有定义工艺路线",
            "bottleneck_step": None,
            "schedule_entries": []
        }

    steps = sorted(route.steps, key=lambda s: s.step_order)
    if not steps:
        return {
            "success": False,
            "reason": "工艺路线没有工序",
            "bottleneck_step": None,
            "schedule_entries": []
        }

    deadline = expected_start_time + timedelta(days=deadline_days)
    prev_end_time = expected_start_time
    prev_step = None
    schedule_entries = []
    bottleneck_step = None

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        device, start_time = _simulate_select_best_device(
            db, step.device_type, earliest_start, step.duration_minutes, all_occupied,
            product_name=product_name
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            break

        end_time = start_time + timedelta(minutes=step.duration_minutes)

        if end_time > deadline:
            bottleneck_step = step.step_name
            break

        prev_product = get_previous_product_on_device(db, device.id, start_time)
        changeover_minutes, changeover_type = calculate_changeover_minutes(
            db, device.id, prev_product, product_name
        )
        changeover_start_time = None
        changeover_end_time = None
        if changeover_minutes > 0:
            changeover_start_time = start_time
            changeover_end_time = start_time + timedelta(minutes=changeover_minutes)
            start_time = changeover_end_time
            end_time = start_time + timedelta(minutes=step.duration_minutes)
            if end_time > deadline:
                bottleneck_step = step.step_name
                break

        schedule_entries.append({
            "step_order": step.step_order,
            "step_name": step.step_name,
            "device_id": device.id,
            "device_name": device.name,
            "device_type": device.device_type,
            "start_time": start_time,
            "end_time": end_time,
            "changeover_start_time": changeover_start_time,
            "changeover_end_time": changeover_end_time,
            "changeover_minutes": changeover_minutes,
            "changeover_type": changeover_type,
            "prev_product_name": prev_product,
        })

        occupied_start = changeover_start_time if changeover_start_time else start_time
        if device.id not in all_occupied:
            all_occupied[device.id] = []
        all_occupied[device.id].append((occupied_start, end_time))

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        for entry in schedule_entries:
            dev_id = entry["device_id"]
            if dev_id in all_occupied:
                try:
                    all_occupied[dev_id].remove((entry["start_time"], entry["end_time"]))
                except ValueError:
                    pass
        return {
            "success": False,
            "reason": f"工序 '{bottleneck_step}' 无法在截止时间前安排",
            "bottleneck_step": bottleneck_step,
            "schedule_entries": []
        }

    return {
        "success": True,
        "reason": None,
        "bottleneck_step": None,
        "schedule_entries": schedule_entries
    }


def _get_real_occupied_slots(
    db: Session,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[int, List[Tuple[datetime, datetime]]]:
    occupied = {}
    devices = db.query(Device).all()

    for device in devices:
        entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.device_id == device.id,
            ScheduleEntry.start_time < end_dt,
            ScheduleEntry.end_time > start_dt
        ).all()
        occupied[device.id] = [
            (e.changeover_start_time if e.changeover_start_time else e.start_time, e.end_time)
            for e in entries
        ]

    return occupied


def _calculate_daily_utilization(
    db: Session,
    device_id: int,
    device: Device,
    date_val: date,
    extra_occupied: List[Tuple[datetime, datetime]]
) -> Tuple[int, int]:
    start_t = parse_time_str(device.daily_start)
    end_t = parse_time_str(device.daily_end)
    day_start = datetime.combine(date_val, start_t)
    day_end = datetime.combine(date_val, end_t)

    available_minutes = int((day_end - day_start).total_seconds() / 60)

    maint_windows = get_maintenance_windows_in_range(db, device_id, day_start, day_end)
    for (mw_start, mw_end, _) in maint_windows:
        available_minutes -= int((mw_end - mw_start).total_seconds() / 60)
    available_minutes = max(0, available_minutes)

    real_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time < day_end,
        ScheduleEntry.end_time > day_start
    ).all()

    all_intervals = [(e.start_time, e.end_time) for e in real_entries]
    all_intervals.extend(extra_occupied)
    all_intervals.sort(key=lambda x: x[0])

    merged = []
    for interval in all_intervals:
        int_start, int_end = interval
        eff_s = max(int_start, day_start)
        eff_e = min(int_end, day_end)
        if eff_e > eff_s:
            if not merged:
                merged.append([eff_s, eff_e])
            else:
                last = merged[-1]
                if eff_s <= last[1]:
                    last[1] = max(last[1], eff_e)
                else:
                    merged.append([eff_s, eff_e])

    scheduled_minutes = 0
    for (s, e) in merged:
        scheduled_minutes += int((e - s).total_seconds() / 60)

    return scheduled_minutes, available_minutes


def predict_bottlenecks(
    db: Session,
    future_days: int,
    simulated_orders: List[Dict]
) -> Dict:
    if len(simulated_orders) > 50:
        return {
            "success": False,
            "message": "模拟工单不能超过50条"
        }

    today = date.today()
    start_dt = datetime.combine(today, time.min)
    end_dt = datetime.combine(today + timedelta(days=future_days), time.max)

    all_occupied = _get_real_occupied_slots(db, start_dt, end_dt)

    simulated_results = []
    failed_orders = []
    all_new_entries_by_device: Dict[int, List[Tuple[datetime, datetime]]] = {}

    for order_data in simulated_orders:
        result = simulate_schedule_order(
            db,
            order_data["product_name"],
            order_data["quantity"],
            order_data["expected_start_time"],
            all_occupied
        )

        sim_result = {
            "product_name": order_data["product_name"],
            "quantity": order_data["quantity"],
            "expected_start_time": order_data["expected_start_time"],
            "scheduled": result["success"],
            "schedule_entries": result["schedule_entries"],
            "failure_reason": result.get("reason"),
            "bottleneck_step": result.get("bottleneck_step")
        }
        simulated_results.append(sim_result)

        if not result["success"]:
            failed_orders.append({
                "product_name": order_data["product_name"],
                "quantity": order_data["quantity"],
                "expected_start_time": order_data["expected_start_time"],
                "reason": result.get("reason", "未知原因"),
                "bottleneck_step": result.get("bottleneck_step")
            })
        else:
            for entry in result["schedule_entries"]:
                dev_id = entry["device_id"]
                if dev_id not in all_new_entries_by_device:
                    all_new_entries_by_device[dev_id] = []
                all_new_entries_by_device[dev_id].append((entry["start_time"], entry["end_time"]))

    high_risk_device_types = []
    devices = db.query(Device).all()
    device_map = {d.id: d for d in devices}

    type_day_utilization: Dict[str, Dict[str, List[float]]] = {}

    for day_offset in range(future_days):
        current_date = today + timedelta(days=day_offset)
        date_str = current_date.isoformat()

        for device in devices:
            extra_occ = all_new_entries_by_device.get(device.id, [])
            scheduled_minutes, available_minutes = _calculate_daily_utilization(
                db, device.id, device, current_date, extra_occ
            )
            util_rate = round(scheduled_minutes / available_minutes, 4) if available_minutes > 0 else 0.0

            if util_rate > 0.9:
                high_risk_device_types.append({
                    "device_type": device.device_type,
                    "date": date_str,
                    "utilization_rate": util_rate,
                    "scheduled_minutes": scheduled_minutes,
                    "available_minutes": available_minutes
                })

            if device.device_type not in type_day_utilization:
                type_day_utilization[device.device_type] = {}
            if date_str not in type_day_utilization[device.device_type]:
                type_day_utilization[device.device_type][date_str] = []
            type_day_utilization[device.device_type][date_str].append(util_rate)

    device_recommendations = []
    for dtype, day_utils in type_day_utilization.items():
        max_avg_util = 0.0
        max_util_date = None
        for date_str, utils in day_utils.items():
            avg_util = sum(utils) / len(utils) if utils else 0.0
            if avg_util > max_avg_util:
                max_avg_util = avg_util
                max_util_date = date_str

        if max_avg_util > 0.85:
            target_util = 0.8
            current_count = len([d for d in devices if d.device_type == dtype])
            if current_count > 0 and max_avg_util > 0:
                recommended = math.ceil(current_count * max_avg_util / target_util) - current_count
                recommended = max(1, recommended)
            else:
                recommended = 1

            device_recommendations.append({
                "device_type": dtype,
                "recommended_count": recommended,
                "reason": f"该类型设备在 {max_util_date} 平均利用率达 {max_avg_util*100:.1f}%，超过85%警戒线，建议增加设备以降低负载"
            })

    failed_device_types = set()
    for failed in failed_orders:
        if failed.get("bottleneck_step"):
            route = db.query(ProcessRoute).filter(ProcessRoute.product_name == failed["product_name"]).first()
            if route:
                for step in route.steps:
                    if step.step_name == failed["bottleneck_step"]:
                        failed_device_types.add(step.device_type)
                        break

    for dtype in failed_device_types:
        existing = next((r for r in device_recommendations if r["device_type"] == dtype), None)
        if not existing:
            current_count = len([d for d in devices if d.device_type == dtype])
            device_recommendations.append({
                "device_type": dtype,
                "recommended_count": max(1, current_count),
                "reason": f"该类型设备为瓶颈工序设备，已有工单因该设备产能不足排产失败"
            })
        else:
            existing["reason"] += "，且已有工单因该设备产能不足排产失败"

    return {
        "success": True,
        "future_days": future_days,
        "total_simulated_orders": len(simulated_orders),
        "high_risk_device_types": high_risk_device_types,
        "failed_orders": failed_orders,
        "device_recommendations": device_recommendations,
        "simulated_results": simulated_results
    }


def get_active_device_fault(db: Session, device_id: int) -> Optional[DeviceFault]:
    return db.query(DeviceFault).filter(
        DeviceFault.device_id == device_id,
        DeviceFault.status == "active",
        DeviceFault.scenario_id.is_(None)
    ).first()


def get_fault_windows_in_range(
    db: Session,
    device_id: int,
    start_dt: datetime,
    end_dt: datetime
) -> List[Tuple[datetime, datetime, str]]:
    faults = db.query(DeviceFault).filter(
        DeviceFault.device_id == device_id,
        DeviceFault.fault_time <= end_dt,
        DeviceFault.expected_recovery_time >= start_dt,
        DeviceFault.scenario_id.is_(None)
    ).all()
    
    windows = []
    for fault in faults:
        fault_start = max(fault.fault_time, start_dt)
        fault_end = min(fault.expected_recovery_time, end_dt)
        if fault_end > fault_start:
            desc = fault.description or "设备故障"
            if fault.status == "resolved" and fault.actual_recovery_time:
                fault_end = min(fault_end, fault.actual_recovery_time)
                if fault_end > fault_start:
                    windows.append((fault_start, fault_end, desc))
            else:
                windows.append((fault_start, fault_end, desc))
    
    windows.sort(key=lambda x: x[0])
    return windows


def find_next_fault_window(
    db: Session,
    device_id: int,
    from_dt: datetime,
    max_days: int = 365
) -> Optional[Tuple[datetime, datetime, str]]:
    faults = db.query(DeviceFault).filter(
        DeviceFault.device_id == device_id,
        DeviceFault.expected_recovery_time > from_dt
    ).order_by(DeviceFault.fault_time).all()
    
    for fault in faults:
        if fault.status == "resolved":
            if fault.actual_recovery_time and fault.actual_recovery_time > from_dt:
                return (max(fault.fault_time, from_dt), fault.actual_recovery_time, fault.description or "设备故障")
            continue
        if fault.fault_time <= from_dt:
            return (from_dt, fault.expected_recovery_time, fault.description or "设备故障")
        if fault.fault_time > from_dt:
            max_date = from_dt.date() + timedelta(days=max_days)
            if fault.fault_time.date() <= max_date:
                return (fault.fault_time, fault.expected_recovery_time, fault.description or "设备故障")
    
    return None


def is_device_faulty_at_time(db: Session, device_id: int, check_time: datetime) -> bool:
    active_fault = get_active_device_fault(db, device_id)
    if active_fault:
        return active_fault.fault_time <= check_time <= active_fault.expected_recovery_time
    
    past_resolved = db.query(DeviceFault).filter(
        DeviceFault.device_id == device_id,
        DeviceFault.status == "resolved",
        DeviceFault.fault_time <= check_time,
        DeviceFault.actual_recovery_time >= check_time
    ).first()
    return past_resolved is not None


def get_available_devices_by_type(db: Session, device_type: str, exclude_device_id: Optional[int] = None) -> List[Device]:
    query = db.query(Device).filter(Device.device_type == device_type)
    if exclude_device_id:
        query = query.filter(Device.id != exclude_device_id)
    
    devices = query.all()
    available_devices = []
    for device in devices:
        if not get_active_device_fault(db, device.id):
            available_devices.append(device)
    return available_devices


def find_affected_schedule_entries(
    db: Session,
    device_id: int,
    fault_time: datetime
) -> List[ScheduleEntry]:
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order),
        joinedload(ScheduleEntry.sub_batch),
        joinedload(ScheduleEntry.device)
    ).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time >= fault_time,
        ScheduleEntry.is_completed == False
    ).order_by(ScheduleEntry.start_time, ScheduleEntry.step_order).all()
    
    return entries


def _reschedule_order_for_fault(
    db: Session,
    order: WorkOrder,
    faulty_device_id: int,
    fault_time: datetime,
    expected_recovery_time: datetime,
    respect_locked: bool = False
) -> Tuple[bool, List[Dict], Optional[str], List[Dict]]:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
    if not route:
        return False, [], f"产品 '{order.product_name}' 没有定义工艺路线", []
    
    steps = sorted(route.steps, key=lambda s: s.step_order)
    if not steps:
        return False, [], "工艺路线没有工序", []
    
    materials_ok, material_shortages = check_materials_for_steps(db, steps)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        return False, [], f"物料不足: {'; '.join(shortage_descs)}", []
    
    existing_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order.id
    ).order_by(ScheduleEntry.sub_batch_id, ScheduleEntry.step_order).all()
    
    entries_by_sub_batch: Dict[Optional[int], List[ScheduleEntry]] = {}
    for entry in existing_entries:
        if entry.sub_batch_id not in entries_by_sub_batch:
            entries_by_sub_batch[entry.sub_batch_id] = []
        entries_by_sub_batch[entry.sub_batch_id].append(entry)
    
    all_scheduled_entries = []
    all_outsourcing_results = []
    sibling_device_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_fixture_entries: List[Tuple[int, datetime, datetime]] = []
    sibling_outsourcing_entries: List[Tuple[int, datetime, datetime]] = []
    migrated_detail_list: List[Dict] = []
    
    entries_to_delete_global: List[ScheduleEntry] = []
    
    for sub_batch_id, old_entries in entries_by_sub_batch.items():
        affected_entries = [e for e in old_entries if not e.is_completed and e.start_time >= fault_time]
        
        if not affected_entries:
            continue
        
        min_affected_step_order = min(e.step_order for e in affected_entries)
        
        prior_entries = [
            e for e in old_entries
            if e.step_order < min_affected_step_order
        ]
        
        if prior_entries:
            last_prior = max(prior_entries, key=lambda e: e.step_order)
            if last_prior.is_completed:
                prev_end_time = last_prior.actual_completion_time or last_prior.end_time
            else:
                prev_end_time = last_prior.end_time
            prev_step_order = last_prior.step_order
            prev_step_def = next((s for s in steps if s.step_order == prev_step_order), None)
        else:
            prev_end_time = order.expected_start_time
            prev_step_order = 0
            prev_step_def = None
        
        reschedule_steps = [s for s in steps if s.step_order > prev_step_order]
        if not reschedule_steps:
            continue
        
        entries_to_delete_for_subbatch = [
            e for e in old_entries
            if e.step_order >= min_affected_step_order and not e.is_completed
        ]
        entries_to_delete_global.extend(entries_to_delete_for_subbatch)
        
        for entry in entries_to_delete_for_subbatch:
            sibling_device_entries.append((
                entry.device_id,
                entry.start_time,
                entry.end_time
            ))
            if entry.fixture_id and entry.fixture_turn_over_end_time:
                sibling_fixture_entries.append((
                    entry.fixture_id,
                    entry.start_time,
                    entry.fixture_turn_over_end_time
                ))
            elif entry.fixture_id:
                sibling_fixture_entries.append((
                    entry.fixture_id,
                    entry.start_time,
                    entry.end_time
                ))
        
        sub_batch = db.query(SubBatch).filter(SubBatch.id == sub_batch_id).first() if sub_batch_id else None
        
        prev_step = prev_step_def
        schedule_entries = []
        outsourcing_results_for_subbatch = []
        bottleneck_step = None
        bottleneck_type = None
        bottleneck_fixture_type = None
        temp_scheduled_device: List[Tuple[int, datetime, datetime]] = []
        temp_scheduled_fixture: List[Tuple[int, datetime, datetime]] = []
        
        for step in reschedule_steps:
            earliest_start = prev_end_time
            if prev_step and prev_step.min_gap_after > 0:
                earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)
            
            combined_device_siblings = sibling_device_entries + temp_scheduled_device
            combined_fixture_siblings = sibling_fixture_entries + temp_scheduled_fixture
            
            if step.is_outsource:
                combined_outsourcing_siblings = sibling_outsourcing_entries + [
                    (n["factory_id"], n["start_time"], n["end_time"])
                    for sb_res in outsourcing_results_for_subbatch
                    for n in sb_res["nodes"]
                    if n["node_type"] == "outsourcing_process"
                ]
                success, nodes, factory, bn_type, bn_msg = schedule_outsourcing_step(
                    db, order, sub_batch, step,
                    quantity=sub_batch.quantity if sub_batch else order.quantity,
                    earliest_start=earliest_start,
                    deadline=order.deadline,
                    exclude_order_id=order.id,
                    sibling_process_entries=combined_outsourcing_siblings
                )
                if not success:
                    bottleneck_step = step.step_name
                    bottleneck_type = bn_type
                    bottleneck_fixture_type = bn_msg
                    break
                
                process_end = max(n["end_time"] for n in nodes)
                prev_end_time = process_end
                prev_step = step
                
                outsourcing_results_for_subbatch.append({
                    "step": step,
                    "nodes": nodes,
                    "factory": factory,
                    "sub_batch_id": sub_batch.id if sub_batch else None
                })
                continue
            
            best_device, best_fixture, best_start, bn_type, bn_fixture = select_best_device_and_fixture(
                db, step, earliest_start, step.duration_minutes,
                exclude_order_id=order.id,
                respect_locked=respect_locked,
                sibling_device_entries=combined_device_siblings,
                sibling_fixture_entries=combined_fixture_siblings,
                exclude_device_ids=[faulty_device_id],
                product_name=order.product_name
            )
            
            if best_device is None or best_start is None:
                bottleneck_step = step.step_name
                bottleneck_type = bn_type
                bottleneck_fixture_type = bn_fixture
                break
            
            end_time = best_start + timedelta(minutes=step.duration_minutes)
            
            if end_time > order.deadline:
                bottleneck_step = step.step_name
                bottleneck_type = "deadline"
                break

            prev_product = get_previous_product_on_device(db, best_device.id, best_start)
            changeover_minutes, changeover_type = calculate_changeover_minutes(
                db, best_device.id, prev_product, order.product_name
            )
            changeover_start_time = None
            changeover_end_time = None
            if changeover_minutes > 0:
                changeover_start_time = best_start
                changeover_end_time = best_start + timedelta(minutes=changeover_minutes)
                best_start = changeover_end_time
                end_time = best_start + timedelta(minutes=step.duration_minutes)
                if end_time > order.deadline:
                    bottleneck_step = step.step_name
                    bottleneck_type = "changeover"
                    break
            
            turn_over_end_time = None
            if best_fixture:
                fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
                if fixture_type and fixture_type.turn_over_minutes > 0:
                    turn_over_end_time = end_time + timedelta(minutes=fixture_type.turn_over_minutes)
            
            orig_entry = next(
                (e for e in entries_to_delete_for_subbatch if e.step_order == step.step_order),
                None
            )
            is_migrated = (orig_entry is not None and orig_entry.device_id == faulty_device_id)
            is_rescheduled = orig_entry is not None
            
            if is_migrated and orig_entry:
                migrated_detail_list.append({
                    "schedule_entry_id": orig_entry.id,
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "sub_batch_id": sub_batch_id,
                    "sub_batch_no": orig_entry.sub_batch.batch_no if orig_entry.sub_batch else None,
                    "step_order": step.step_order,
                    "step_name": step.step_name,
                    "from_device_id": faulty_device_id,
                    "from_device_name": "",
                    "to_device_id": best_device.id,
                    "to_device_name": "",
                    "original_start_time": orig_entry.start_time,
                    "original_end_time": orig_entry.end_time,
                    "new_start_time": best_start,
                    "new_end_time": end_time,
                })
            
            schedule_entries.append({
                "step_id": step.id,
                "device_id": best_device.id,
                "fixture_id": best_fixture.id if best_fixture else None,
                "step_order": step.step_order,
                "step_name": step.step_name,
                "start_time": best_start,
                "end_time": end_time,
                "fixture_turn_over_end_time": turn_over_end_time,
                "sub_batch_id": sub_batch_id,
                "migrated_from_device_id": faulty_device_id if is_migrated else None,
                "is_migrated": is_migrated,
                "changeover_start_time": changeover_start_time,
                "changeover_end_time": changeover_end_time,
                "changeover_minutes": changeover_minutes,
                "changeover_type": changeover_type,
                "prev_product_name": prev_product,
            })
            
            occupied_start = changeover_start_time if changeover_start_time else best_start
            temp_scheduled_device.append((best_device.id, occupied_start, end_time))
            if best_fixture and turn_over_end_time:
                temp_scheduled_fixture.append((best_fixture.id, best_start, turn_over_end_time))
            elif best_fixture:
                temp_scheduled_fixture.append((best_fixture.id, best_start, end_time))
            
            prev_end_time = end_time
            prev_step = step
        
        if bottleneck_step is not None:
            error_msg = f"在工序 '{bottleneck_step}' 无法安排"
            if bottleneck_type == "fixture":
                error_msg += f"，工装不足(类型: {bottleneck_fixture_type})"
            elif bottleneck_type == "device":
                error_msg += "，设备产能不足"
            elif bottleneck_type == "changeover":
                error_msg += "，换型时间导致超出截止时间"
            elif bottleneck_type == "outsourcing_concurrent":
                error_msg += f"，{bottleneck_fixture_type or '外协厂并发上限不足'}"
            elif bottleneck_type == "outsourcing_timewindow":
                error_msg += f"，{bottleneck_fixture_type or '外协厂时间窗不足'}"
            elif bottleneck_type == "outsourcing_capability":
                error_msg += f"，{bottleneck_fixture_type or '外协厂工序能力不匹配'}"
            else:
                error_msg += "到其他可用设备或超出截止时间"
            return False, [], error_msg, []
        
        sibling_device_entries.extend(temp_scheduled_device)
        sibling_fixture_entries.extend(temp_scheduled_fixture)
        all_scheduled_entries.extend(schedule_entries)
        all_outsourcing_results.extend(outsourcing_results_for_subbatch)
    
    from app.staffing_service import release_employees_for_entries
    
    entries_to_delete_ids = [e.id for e in entries_to_delete_global]
    release_employees_for_entries(db, entries_to_delete_ids)
    
    for entry in entries_to_delete_global:
        try:
            db.delete(entry)
        except Exception as e:
            print(f"[Fault] 删除记录失败 order={order.order_no} entry_id={entry.id}: {e}")
    
    if all_outsourcing_results:
        delete_outsourcing_entries_for_order(db, order.id)
        try:
            create_outsourcing_schedule_entries(db, order, all_outsourcing_results)
        except Exception as e:
            print(f"[Fault] 创建外协排产条目失败 order={order.order_no}: {e}")
            return False, [], f"外协排产失败: {e}", []
    
    db.flush()
    
    return True, all_scheduled_entries, None, migrated_detail_list


def check_cascade_impact(
    db: Session,
    migrated_entries: List[Dict],
    new_entries: List[ScheduleEntry],
    faulty_device_id: int
) -> List[Dict]:
    cascade_blocked = []
    
    affected_device_ids = set()
    for entry in new_entries:
        affected_device_ids.add(entry.device_id)
    
    for device_id in affected_device_ids:
        device_entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order),
            joinedload(ScheduleEntry.sub_batch)
        ).filter(
            ScheduleEntry.device_id == device_id,
            ScheduleEntry.is_completed == False,
            ScheduleEntry.order.has(is_locked=False)
        ).all()
        
        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            continue
        
        occupied = []
        for entry in device_entries:
            occupied.append((entry.start_time, entry.end_time, entry.order.is_locked if entry.order else False))
        
        for migrated in new_entries:
            if migrated.device_id != device_id:
                continue
            occupied.append((migrated.start_time, migrated.end_time, True))
        
        occupied.sort(key=lambda x: x[0])
        
        for entry in device_entries:
            if entry.order and entry.order.is_locked:
                continue
            if entry.is_migrated:
                continue
            
            order = entry.order
            if not order:
                continue
            
            step = db.query(ProcessStep).filter(ProcessStep.id == entry.step_id).first()
            if not step:
                continue
            
            earliest_start = entry.start_time
            duration = entry.end_time - entry.start_time
            duration_minutes = int(duration.total_seconds() / 60)
            
            new_slot = find_earliest_slot_with_siblings(
                db, device, earliest_start, duration_minutes,
                order_id=order.id, respect_locked=True,
                sibling_entries=[(e.device_id, e.start_time, e.end_time) for e in new_entries]
            )
            
            if new_slot is None:
                cascade_blocked.append({
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "blocked_reason": f"设备 {device.name} 因接纳故障迁移工单导致产能不足，工序 '{entry.step_name}' 无法安排",
                    "affected_step": entry.step_name,
                    "affected_sub_batch": entry.sub_batch.batch_no if entry.sub_batch else None
                })
                continue
            
            new_end = new_slot + duration
            if new_end > order.deadline:
                cascade_blocked.append({
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "blocked_reason": f"设备 {device.name} 因接纳故障迁移工单导致工序 '{entry.step_name}' 超出截止时间 (原结束: {entry.end_time}, 新结束: {new_end}, 截止: {order.deadline})",
                    "affected_step": entry.step_name,
                    "affected_sub_batch": entry.sub_batch.batch_no if entry.sub_batch else None
                })
    
    return cascade_blocked


def report_device_fault(
    db: Session,
    device_id: int,
    expected_recovery_time: datetime,
    fault_time: Optional[datetime] = None,
    description: Optional[str] = None
) -> Dict:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return {
            "success": False,
            "message": f"设备 ID {device_id} 不存在"
        }
    
    existing_active_fault = get_active_device_fault(db, device_id)
    if existing_active_fault:
        return {
            "success": False,
            "message": f"设备 '{device.name}' 已有活跃故障记录 (ID: {existing_active_fault.id})，不能重复报告"
        }
    
    if fault_time is None:
        fault_time = datetime.now()
    
    if expected_recovery_time <= fault_time:
        return {
            "success": False,
            "message": "预计恢复时间必须晚于故障时间"
        }
    
    fault = DeviceFault(
        device_id=device_id,
        fault_time=fault_time,
        expected_recovery_time=expected_recovery_time,
        description=description,
        status="active"
    )
    db.add(fault)
    db.flush()
    
    affected_entries = find_affected_schedule_entries(db, device_id, fault_time)
    
    affected_order_ids = set()
    for entry in affected_entries:
        if entry.order_id:
            affected_order_ids.add(entry.order_id)
    
    migrated_results = []
    blocked_orders = []
    all_new_entries = []
    
    for order_id in affected_order_ids:
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order:
            continue
        
        if order.is_locked:
            blocked_orders.append({
                "order_id": order.id,
                "order_no": order.order_no,
                "blocked_reason": f"工单已锁定，无法自动迁移",
                "affected_step": None,
                "affected_sub_batch": None
            })
            continue
        
        success, new_entries, error_msg, migrated_info = _reschedule_order_for_fault(
            db, order, device_id, fault_time, expected_recovery_time
        )
        
        if not success:
            order.is_blocked = True
            order.blocked_reason = f"设备故障迁移失败: {error_msg}"
            order.status = "failed"
            db.flush()
            
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="device_fault_blocked",
                description=f"设备 '{device.name}' 故障，{error_msg}"
            )
            db.add(conflict)
            
            sub_batch = None
            step_name = None
            if affected_entries:
                for e in affected_entries:
                    if e.order_id == order_id:
                        if e.sub_batch:
                            sub_batch = e.sub_batch.batch_no
                        step_name = e.step_name
                        break
            
            blocked_orders.append({
                "order_id": order.id,
                "order_no": order.order_no,
                "blocked_reason": order.blocked_reason,
                "affected_step": step_name,
                "affected_sub_batch": sub_batch
            })
            continue
        
        created_entries = []
        device_cache: Dict[int, Device] = {}
        
        for entry_data in new_entries:
            is_migrated = entry_data.get("is_migrated", False)
            db_entry = ScheduleEntry(
                order_id=order.id,
                sub_batch_id=entry_data.get("sub_batch_id"),
                step_id=entry_data["step_id"],
                device_id=entry_data["device_id"],
                step_order=entry_data["step_order"],
                step_name=entry_data["step_name"],
                start_time=entry_data["start_time"],
                end_time=entry_data["end_time"],
                migrated_from_device_id=entry_data.get("migrated_from_device_id"),
                is_migrated=is_migrated,
                changeover_start_time=entry_data.get("changeover_start_time"),
                changeover_end_time=entry_data.get("changeover_end_time"),
                changeover_minutes=entry_data.get("changeover_minutes", 0),
                changeover_type=entry_data.get("changeover_type"),
                prev_product_name=entry_data.get("prev_product_name"),
            )
            db.add(db_entry)
            created_entries.append(db_entry)
            all_new_entries.append(db_entry)
        
        for m_detail in migrated_info:
            to_dev_id = m_detail["to_device_id"]
            if to_dev_id not in device_cache:
                to_dev = db.query(Device).filter(Device.id == to_dev_id).first()
                device_cache[to_dev_id] = to_dev
            else:
                to_dev = device_cache[to_dev_id]
            
            m_detail["from_device_name"] = device.name
            m_detail["to_device_name"] = to_dev.name if to_dev else f"Device-{to_dev_id}"
            migrated_results.append(m_detail)
    
    cascade_blocked = check_cascade_impact(db, migrated_results, all_new_entries, device_id)
    
    for blocked in cascade_blocked:
        order = db.query(WorkOrder).filter(WorkOrder.id == blocked["order_id"]).first()
        if order:
            order.is_blocked = True
            order.blocked_reason = blocked["blocked_reason"]
            order.status = "failed"
            
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="device_fault_cascade_blocked",
                description=blocked["blocked_reason"]
            )
            db.add(conflict)
    
    for affected_order_id in affected_order_ids:
        delete_outsourcing_entries_for_order(db, affected_order_id)
        order_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == affected_order_id
        ).all()
        
        affected_for_order = [e for e in order_entries if e.device_id == device_id and e.start_time >= fault_time and not e.is_completed]
        if not affected_for_order:
            continue
        
        min_step = min(e.step_order for e in affected_for_order)
        
        entries_by_subbatch: Dict[Optional[int], List[ScheduleEntry]] = {}
        for e in order_entries:
            if e.sub_batch_id not in entries_by_subbatch:
                entries_by_subbatch[e.sub_batch_id] = []
            entries_by_subbatch[e.sub_batch_id].append(e)
        
        for sb_id, sb_entries in entries_by_subbatch.items():
            affected_in_sb = [e for e in sb_entries if e in affected_for_order]
            if not affected_in_sb:
                continue
            min_step_sb = min(e.step_order for e in affected_in_sb)
            
            entries_to_delete_final = [e for e in sb_entries if e.step_order >= min_step_sb and not e.is_completed]
            if entries_to_delete_final:
                from app.staffing_service import release_employees_for_entries
                final_ids = [e.id for e in entries_to_delete_final]
                release_employees_for_entries(db, final_ids)
            
            for e in entries_to_delete_final:
                if e in db:
                    try:
                        db.delete(e)
                    except Exception as e_del:
                        print(f"[Fault] 最终清理失败 entry_id={e.id}, error: {e_del}")
    
    db.commit()
    db.refresh(fault)
    
    return {
        "success": True,
        "message": f"设备 '{device.name}' 故障已记录，已处理 {len(affected_order_ids)} 个受影响工单",
        "fault_id": fault.id,
        "device_id": device_id,
        "device_name": device.name,
        "fault_time": fault_time,
        "expected_recovery_time": expected_recovery_time,
        "affected_orders_count": len(affected_order_ids),
        "migrated_entries": migrated_results,
        "blocked_orders": blocked_orders,
        "cascade_blocked_orders": cascade_blocked
    }


def resolve_device_fault(
    db: Session,
    device_id: int,
    actual_recovery_time: Optional[datetime] = None
) -> Dict:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return {
            "success": False,
            "message": f"设备 ID {device_id} 不存在"
        }
    
    active_fault = get_active_device_fault(db, device_id)
    if not active_fault:
        return {
            "success": False,
            "message": f"设备 '{device.name}' 没有活跃的故障记录"
        }
    
    if actual_recovery_time is None:
        actual_recovery_time = datetime.now()
    
    if actual_recovery_time < active_fault.fault_time:
        return {
            "success": False,
            "message": "实际恢复时间不能早于故障时间"
        }
    
    active_fault.status = "resolved"
    active_fault.actual_recovery_time = actual_recovery_time
    active_fault.resolved_at = datetime.now()
    
    db.commit()
    db.refresh(active_fault)
    
    return {
        "success": True,
        "message": f"设备 '{device.name}' 故障已解除，已恢复正常使用",
        "fault_id": active_fault.id,
        "device_id": device_id,
        "device_name": device.name,
        "status": "resolved",
        "resolved_at": active_fault.resolved_at
    }


def get_device_faults(
    db: Session,
    device_id: Optional[int] = None,
    status: Optional[str] = None,
    include_resolved: bool = False
) -> Tuple[List[DeviceFault], int, int]:
    query = db.query(DeviceFault).options(joinedload(DeviceFault.device)).filter(
        DeviceFault.scenario_id.is_(None)
    )
    
    if device_id:
        query = query.filter(DeviceFault.device_id == device_id)
    
    if status:
        query = query.filter(DeviceFault.status == status)
    elif not include_resolved:
        query = query.filter(DeviceFault.status == "active")
    
    faults = query.order_by(DeviceFault.created_at.desc()).all()
    
    active_count = db.query(DeviceFault).filter(
        DeviceFault.status == "active",
        DeviceFault.scenario_id.is_(None)
    ).count()
    
    return faults, len(faults), active_count


def find_earliest_slot_with_faults(
    db: Session,
    device: Device,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)
    
    occupied = get_device_occupied_slots(db, device.id, exclude_order_id)
    
    max_iterations = 365 * 24 * 60
    iterations = 0
    
    while iterations < max_iterations:
        iterations += 1
        moved = False

        changeover_minutes = 0
        if product_name:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)

        total_duration = timedelta(minutes=changeover_minutes + duration_minutes)

        if deadline and current_start + total_duration > deadline:
            return None
        
        day_end = calculate_available_end(current_start, device)
        if current_start + total_duration > day_end:
            next_day = current_start.date() + timedelta(days=1)
            current_start = datetime.combine(next_day, parse_time_str(device.daily_start))
            continue
        
        for (occ_start, occ_end, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if current_start < occ_end and current_start + total_duration > occ_start:
                current_start = occ_end
                moved = True
                break
        
        if moved:
            current_start = get_next_working_start(current_start, device)
            continue

        if changeover_minutes > 0:
            prev_product = get_previous_product_on_device(db, device.id, current_start)
            new_changeover_minutes, _ = calculate_changeover_minutes(db, device.id, prev_product, product_name)
            if new_changeover_minutes != changeover_minutes:
                changeover_minutes = new_changeover_minutes
                total_duration = timedelta(minutes=changeover_minutes + duration_minutes)
                if deadline and current_start + total_duration > deadline:
                    return None
                for (occ_start, occ_end, is_locked) in occupied:
                    if respect_locked and not is_locked:
                        continue
                    if current_start < occ_end and current_start + total_duration > occ_start:
                        current_start = occ_end
                        moved = True
                        break
                if moved:
                    current_start = get_next_working_start(current_start, device)
                    continue
        
        next_maint = find_next_maintenance_window(db, device.id, current_start)
        if next_maint:
            maint_start, maint_end, _ = next_maint
            if current_start >= maint_start and current_start < maint_end:
                current_start = maint_end
                moved = True
            elif current_start + total_duration > maint_start and current_start < maint_start:
                gap = maint_start - current_start
                if gap < total_duration:
                    current_start = maint_end
                    moved = True
        
        if moved:
            current_start = get_next_working_start(current_start, device)
            continue
        
        next_fault = find_next_fault_window(db, device.id, current_start)
        if next_fault:
            fault_start, fault_end, _ = next_fault
            if current_start >= fault_start and current_start < fault_end:
                current_start = fault_end
                moved = True
            elif current_start + total_duration > fault_start and current_start < fault_start:
                gap = fault_start - current_start
                if gap < total_duration:
                    current_start = fault_end
                    moved = True
        
        if moved:
            current_start = get_next_working_start(current_start, device)
            continue
        
        return current_start
    
    return None


def select_best_device_with_faults(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    exclude_device_ids: Optional[List[int]] = None,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None
) -> Tuple[Optional[Device], Optional[datetime]]:
    devices = db.query(Device).filter(Device.device_type == device_type)
    
    if exclude_device_ids:
        devices = devices.filter(~Device.id.in_(exclude_device_ids))
    
    devices = devices.all()
    
    if not devices:
        return None, None
    
    available_devices = []
    for device in devices:
        if get_active_device_fault(db, device.id):
            continue
        if exclude_device_ids and device.id in exclude_device_ids:
            continue
        available_devices.append(device)
    
    if not available_devices:
        return None, None
    
    best_device = None
    best_start = None
    
    for device in available_devices:
        slot_start = find_earliest_slot_with_faults(
            db, device, earliest_start, duration_minutes,
            exclude_order_id, respect_locked=respect_locked,
            product_name=product_name,
            deadline=deadline
        )
        if slot_start is not None:
            if best_start is None or slot_start < best_start:
                best_start = slot_start
                best_device = device
            elif slot_start == best_start and best_device is not None:
                device_load = _calculate_device_load(db, device.id)
                best_load = _calculate_device_load(db, best_device.id)
                if device_load < best_load:
                    best_device = device
    
    return best_device, best_start


def get_order_first_device_id(db: Session, order_id: int) -> Optional[int]:
    first_entry = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.step_order.asc()).first()
    return first_entry.device_id if first_entry else None


def get_order_first_step_start_time(db: Session, order_id: int) -> Optional[datetime]:
    first_entry = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.step_order.asc()).first()
    return first_entry.start_time if first_entry else None


def check_insertion_throttle(db: Session, order_id: int) -> Tuple[bool, Optional[str]]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return False, "工单不存在"
    
    if order.last_insertion_at:
        now = datetime.utcnow()
        time_diff = (now - order.last_insertion_at).total_seconds()
        if time_diff < 600:
            remaining = int(600 - time_diff)
            return False, f"插单操作过于频繁，请在 {remaining} 秒后重试（两次插单间隔需至少10分钟）"
    
    return True, None


def get_overlapping_orders_for_order(
    db: Session,
    order_id: int,
    threshold_priority: Optional[int] = None
) -> List[WorkOrder]:
    """
    获取与指定工单在任意设备上有时间重叠的所有未锁定工单。
    如果指定了 threshold_priority，则只返回优先级低于该值的工单。
    """
    target_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.scenario_id.is_(None)
    ).all()
    
    if not target_entries:
        return []
    
    order_ids = set()
    result_orders = []
    
    for target_entry in target_entries:
        device_id = target_entry.device_id
        target_start = target_entry.changeover_start_time if target_entry.changeover_start_time else target_entry.start_time
        target_end = target_entry.end_time
        
        overlapping_entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(
            ScheduleEntry.device_id == device_id,
            ScheduleEntry.scenario_id.is_(None),
            ScheduleEntry.order_id != order_id
        ).all()
        
        for entry in overlapping_entries:
            if not entry.order or entry.order.id in order_ids:
                continue
            if entry.order.is_locked:
                continue
            if threshold_priority is not None and entry.order.priority >= threshold_priority:
                continue
            
            entry_start = entry.changeover_start_time if entry.changeover_start_time else entry.start_time
            entry_end = entry.end_time
            
            if entry_start < target_end and entry_end > target_start:
                order_ids.add(entry.order.id)
                result_orders.append(entry.order)
    
    return result_orders


def get_lower_priority_orders_on_device(
    db: Session, 
    device_id: int, 
    threshold_priority: int,
    from_time: datetime
) -> List[WorkOrder]:
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.start_time >= from_time,
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.start_time.asc()).all()
    
    order_ids = set()
    result_orders = []
    for entry in entries:
        if entry.order and entry.order.id not in order_ids:
            if not entry.order.is_locked and entry.order.priority < threshold_priority:
                order_ids.add(entry.order.id)
                result_orders.append(entry.order)
    
    return result_orders


def check_conflict_with_locked_orders(
    db: Session,
    order_id: int,
    target_device_id: int,
    earliest_start: datetime,
    duration_minutes: int,
    product_name: str
) -> Tuple[bool, Optional[WorkOrder], Optional[datetime], Optional[datetime]]:
    device = db.query(Device).filter(Device.id == target_device_id).first()
    if not device:
        return False, None, None, None
    
    current_start = get_next_working_start(earliest_start, device)
    
    prev_product = get_previous_product_on_device(db, target_device_id, current_start)
    changeover_minutes, _ = calculate_changeover_minutes(db, target_device_id, prev_product, product_name)
    total_duration = timedelta(minutes=changeover_minutes + duration_minutes)
    
    locked_entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.device_id == target_device_id,
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.start_time.asc()).all()
    
    for entry in locked_entries:
        if entry.order and entry.order.is_locked and entry.order.id != order_id:
            occ_start = entry.changeover_start_time if entry.changeover_start_time else entry.start_time
            occ_end = entry.end_time
            
            if current_start < occ_end and current_start + total_duration > occ_start:
                return True, entry.order, occ_start, occ_end
    
    return False, None, None, None


def reschedule_orders_by_priority(db: Session, orders: List[WorkOrder]) -> Dict:
    if not orders:
        return {"delayed": [], "blocked": []}
    
    from app.models import BatchDeliveryRecord

    order_ids_with_delivery = set()
    order_ids_to_check = [o.id for o in orders]
    delivered_records = db.query(BatchDeliveryRecord).filter(
        BatchDeliveryRecord.order_id.in_(order_ids_to_check),
        BatchDeliveryRecord.scenario_id.is_(None)
    ).all()
    for r in delivered_records:
        order_ids_with_delivery.add(r.order_id)

    orders_sorted = sorted(
        [o for o in orders if o.id not in order_ids_with_delivery],
        key=lambda o: (-o.priority, o.id)
    )
    
    if not orders_sorted:
        return {"delayed": [], "blocked": []}
    
    delayed_orders = []
    blocked_orders = []
    
    original_times = {}
    old_start_times_map = {}
    old_last_end_map = {}
    
    for order in orders_sorted:
        first_start = get_order_first_step_start_time(db, order.id)
        original_times[order.id] = first_start
        
        old_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order.id,
            ScheduleEntry.is_delivered_locked == False
        ).all()
        old_start_times_map[order.id] = {e.step_order: e.start_time for e in old_entries}
        old_last_end_map[order.id] = max((e.end_time for e in old_entries), default=None) if old_entries else None
    
    order_ids = [o.id for o in orders_sorted]
    
    for oid in order_ids:
        release_material_locks_for_order(db, oid)
        release_fixtures_for_order(db, oid)
        from app.staffing_service import release_employees_for_order
        release_employees_for_order(db, oid)
        delete_outsourcing_entries_for_order(db, oid)
        db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == oid,
            ScheduleEntry.is_delivered_locked == False
        ).delete(synchronize_session=False)
        db.query(SubBatch).filter(
            SubBatch.order_id == oid,
            SubBatch.delivered_quantity == 0
        ).delete(synchronize_session=False)
    db.flush()
    
    for order in orders_sorted:
        old_start_times = old_start_times_map.get(order.id, {})
        old_last_end = old_last_end_map.get(order.id)
        
        order = db.query(WorkOrder).filter(WorkOrder.id == order.id).first()
        if not order:
            continue
        
        order.is_split = False
        order.total_sub_batches = 0
        order.is_blocked = False
        order.blocked_reason = None
        
        result = schedule_order(db, order, respect_locked=False)
        
        if not result["success"]:
            order.is_blocked = True
            order.blocked_reason = result.get("message", "排产失败")
            order.status = "failed"
            blocked_orders.append({
                "order_id": order.id,
                "order_no": order.order_no,
                "blocked_reason": result.get("message", "排产失败"),
                "original_start_time": original_times.get(order.id)
            })
            db.commit()
        else:
            db.refresh(order)
            new_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
            new_first_start = min((e.start_time for e in new_entries), default=None)
            new_last_end = max((e.end_time for e in new_entries), default=None)
            
            max_delay_minutes = 0
            delayed_step = None
            for step_order in old_start_times:
                new_entry = next((e for e in new_entries if e.step_order == step_order), None)
                if new_entry:
                    delay = (new_entry.start_time - old_start_times[step_order]).total_seconds() / 60
                    if delay > max_delay_minutes:
                        max_delay_minutes = int(delay)
                        delayed_step = new_entry.step_name
            
            if old_last_end and new_last_end and new_last_end > old_last_end:
                end_delay = int((new_last_end - old_last_end).total_seconds() / 60)
                if end_delay > max_delay_minutes:
                    max_delay_minutes = end_delay
            
            if max_delay_minutes > 0:
                delayed_orders.append({
                    "order_id": order.id,
                    "order_no": order.order_no,
                    "delay_minutes": max_delay_minutes,
                    "delayed_step": delayed_step,
                    "original_start_time": original_times.get(order.id),
                    "new_start_time": new_first_start
                })
    
    return {
        "delayed": delayed_orders,
        "blocked": blocked_orders
    }


def insert_order_with_priority(
    db: Session,
    order_id: int,
    new_priority: int,
    operator: Optional[str] = None,
    reason: Optional[str] = None
) -> Dict:
    from app.models import InsertionHistory, InsertionAffectedOrder
    
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return {
            "success": False,
            "message": "工单不存在"
        }
    
    if order.status not in ["scheduled", "pending", "failed"]:
        return {
            "success": False,
            "message": f"工单状态为 {order.status}，不能插单"
        }
    
    if order.is_locked:
        return {
            "success": False,
            "message": "已锁定的工单不能执行插单操作"
        }
    
    from app.models import BatchDeliveryRecord
    has_delivery_records = db.query(BatchDeliveryRecord).filter(
        BatchDeliveryRecord.order_id == order.id,
        BatchDeliveryRecord.scenario_id.is_(None)
    ).count() > 0
    if has_delivery_records:
        return {
            "success": False,
            "message": "已有批次交付记录的工单不能执行插单操作，已交付的排产计划不可变动"
        }
    
    if new_priority < 1 or new_priority > 10:
        return {
            "success": False,
            "message": "优先级必须在 1-10 之间"
        }
    
    old_priority = order.priority
    
    if new_priority <= old_priority:
        return {
            "success": False,
            "message": f"新优先级({new_priority})不高于当前优先级({old_priority})，无需插单"
        }
    
    throttle_ok, throttle_msg = check_insertion_throttle(db, order_id)
    if not throttle_ok:
        return {
            "success": False,
            "message": throttle_msg
        }
    
    steps = get_route_steps_for_order(db, order)
    if not steps:
        return {
            "success": False,
            "message": "产品没有工艺路线"
        }
    
    first_step = steps[0]
    first_device_id = get_order_first_device_id(db, order_id)
    
    if not first_device_id:
        first_device, _ = select_best_device(
            db, first_step.device_type, order.expected_start_time,
            first_step.duration_minutes, exclude_order_id=order.id,
            respect_locked=True, product_name=order.product_name,
            deadline=order.deadline
        )
        if first_device:
            first_device_id = first_device.id
    
    if first_device_id:
        has_conflict, locked_order, lock_start, lock_end = check_conflict_with_locked_orders(
            db, order.id, first_device_id, order.expected_start_time,
            first_step.duration_minutes, order.product_name
        )
        if has_conflict and locked_order:
            return {
                "success": False,
                "message": f"插单失败：锁定工单 '{locked_order.order_no}' 占用了设备时间窗 "
                          f"({lock_start.strftime('%Y-%m-%d %H:%M')} - {lock_end.strftime('%Y-%m-%d %H:%M')})，无法插入",
                "blocked_by_locked": locked_order.order_no
            }
    
    order.priority = new_priority
    order.last_insertion_at = datetime.utcnow()
    db.flush()
    
    old_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order.id,
        ScheduleEntry.is_delivered_locked == False
    ).all()
    old_first_start = min((e.start_time for e in old_entries), default=None) if old_entries else None
    
    release_material_locks_for_order(db, order.id)
    release_fixtures_for_order(db, order.id)
    from app.staffing_service import release_employees_for_order
    release_employees_for_order(db, order.id)
    delete_outsourcing_entries_for_order(db, order.id)
    
    db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order.id,
        ScheduleEntry.is_delivered_locked == False
    ).delete(synchronize_session=False)
    db.query(SubBatch).filter(
        SubBatch.order_id == order.id,
        SubBatch.delivered_quantity == 0
    ).delete(synchronize_session=False)
    order.is_split = False
    order.total_sub_batches = 0
    order.is_blocked = False
    order.blocked_reason = None
    db.flush()
    
    result = schedule_order(db, order, respect_locked=True)
    
    if not result["success"]:
        order.priority = old_priority
        order.is_blocked = True
        order.blocked_reason = result.get("message", "排产失败")
        order.status = "failed"
        db.commit()
        return {
            "success": False,
            "message": f"插单失败：{result.get('message', '排产失败')}",
            "delayed_count": 0,
            "blocked_count": 0,
            "affected_orders": []
        }
    
    db.refresh(order)
    new_first_start = get_order_first_step_start_time(db, order_id)
    
    original_times = {}
    
    all_lower_priority_orders = db.query(WorkOrder).filter(
        WorkOrder.priority < new_priority,
        WorkOrder.is_locked == False,
        WorkOrder.scenario_id.is_(None)
    ).all()
    
    for o in all_lower_priority_orders:
        if o.id != order_id:
            first_start = get_order_first_step_start_time(db, o.id)
            original_times[o.id] = first_start
    
    if all_lower_priority_orders:
        cascade_result = reschedule_orders_by_priority(db, all_lower_priority_orders)
        
        all_delayed = []
        for d in cascade_result["delayed"]:
            d["original_start_time"] = original_times.get(d["order_id"], d.get("original_start_time"))
            all_delayed.append(d)
        
        all_blocked = []
        for b in cascade_result["blocked"]:
            b["original_start_time"] = original_times.get(b["order_id"], b.get("original_start_time"))
            all_blocked.append(b)
    else:
        all_delayed = []
        all_blocked = []
    
    final_delayed = all_delayed
    final_blocked = all_blocked
    
    insertion_history = InsertionHistory(
        order_id=order.id,
        order_no=order.order_no,
        old_priority=old_priority,
        new_priority=new_priority,
        operator=operator,
        reason=reason,
        affected_orders_count=len(final_delayed) + len(final_blocked),
        delayed_orders_count=len(final_delayed),
        blocked_orders_count=len(final_blocked)
    )
    db.add(insertion_history)
    db.flush()
    
    for delayed in final_delayed:
        affected = InsertionAffectedOrder(
            insertion_history_id=insertion_history.id,
            affected_order_id=delayed["order_id"],
            affected_order_no=delayed["order_no"],
            impact_type="delayed",
            delay_minutes=delayed["delay_minutes"],
            original_start_time=delayed.get("original_start_time"),
            new_start_time=delayed.get("new_start_time")
        )
        db.add(affected)
    
    for blocked in final_blocked:
        affected = InsertionAffectedOrder(
            insertion_history_id=insertion_history.id,
            affected_order_id=blocked["order_id"],
            affected_order_no=blocked["order_no"],
            impact_type="blocked",
            blocked_reason=blocked.get("blocked_reason"),
            original_start_time=blocked.get("original_start_time")
        )
        db.add(affected)
    
    db.commit()
    
    affected_orders_info = []
    for delayed in final_delayed:
        affected_orders_info.append({
            "order_id": delayed["order_id"],
            "order_no": delayed["order_no"],
            "impact_type": "delayed",
            "delay_minutes": delayed["delay_minutes"],
            "original_start_time": delayed.get("original_start_time"),
            "new_start_time": delayed.get("new_start_time")
        })
    
    for blocked in final_blocked:
        affected_orders_info.append({
            "order_id": blocked["order_id"],
            "order_no": blocked["order_no"],
            "impact_type": "blocked",
            "delay_minutes": 0,
            "blocked_reason": blocked.get("blocked_reason"),
            "original_start_time": blocked.get("original_start_time")
        })
    
    return {
        "success": True,
        "message": f"插单成功，工单 '{order.order_no}' 优先级已从 {old_priority} 提升至 {new_priority}",
        "order_id": order.id,
        "order_no": order.order_no,
        "old_priority": old_priority,
        "new_priority": new_priority,
        "original_start_time": old_first_start,
        "new_start_time": new_first_start,
        "affected_orders": affected_orders_info,
        "delayed_count": len(final_delayed),
        "blocked_count": len(final_blocked)
    }


def get_insertion_history(
    db: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    order_id: Optional[int] = None,
    operator: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> Tuple[List, int]:
    from app.models import InsertionHistory
    
    query = db.query(InsertionHistory).filter(InsertionHistory.scenario_id.is_(None))
    
    if start_time:
        query = query.filter(InsertionHistory.created_at >= start_time)
    if end_time:
        query = query.filter(InsertionHistory.created_at <= end_time)
    if order_id:
        query = query.filter(InsertionHistory.order_id == order_id)
    if operator:
        query = query.filter(InsertionHistory.operator == operator)
    
    total = query.count()
    
    histories = query.order_by(InsertionHistory.created_at.desc()).offset(skip).limit(limit).all()
    
    return histories, total


def get_insertion_history_detail(db: Session, history_id: int) -> Optional[Dict]:
    from app.models import InsertionHistory, InsertionAffectedOrder
    
    history = db.query(InsertionHistory).filter(InsertionHistory.id == history_id).first()
    if not history:
        return None
    
    affected_orders = db.query(InsertionAffectedOrder).filter(
        InsertionAffectedOrder.insertion_history_id == history_id
    ).all()
    
    affected_list = []
    for ao in affected_orders:
        affected_list.append({
            "order_id": ao.affected_order_id,
            "order_no": ao.affected_order_no,
            "impact_type": ao.impact_type,
            "delay_minutes": ao.delay_minutes,
            "blocked_reason": ao.blocked_reason,
            "original_start_time": ao.original_start_time,
            "new_start_time": ao.new_start_time
        })
    
    return {
        "id": history.id,
        "order_id": history.order_id,
        "order_no": history.order_no,
        "old_priority": history.old_priority,
        "new_priority": history.new_priority,
        "operator": history.operator,
        "reason": history.reason,
        "affected_orders_count": history.affected_orders_count,
        "delayed_orders_count": history.delayed_orders_count,
        "blocked_orders_count": history.blocked_orders_count,
        "created_at": history.created_at,
        "affected_orders": affected_list
    }
