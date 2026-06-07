from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from app.models import Device, ProcessRoute, ProcessStep, WorkOrder, ScheduleEntry, ConflictRecord, MaintenancePlan, Material, StepMaterialRequirement, MaterialLock
from sqlalchemy import func
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


def get_device_occupied_slots(db: Session, device_id: int, exclude_order_id: Optional[int] = None) -> List[Tuple[datetime, datetime, bool]]:
    from sqlalchemy.orm import joinedload
    query = db.query(ScheduleEntry).options(joinedload(ScheduleEntry.order)).filter(ScheduleEntry.device_id == device_id)
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    entries = query.order_by(ScheduleEntry.start_time).all()
    return [(e.start_time, e.end_time, e.order.is_locked if e.order else False) for e in entries]


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
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)

    occupied = get_device_occupied_slots(db, device.id, exclude_order_id)

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

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

        next_maint = find_next_maintenance_window(db, device.id, current_start)
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


def select_best_device(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True
) -> Tuple[Optional[Device], Optional[datetime]]:
    devices = db.query(Device).filter(Device.device_type == device_type).all()
    if not devices:
        return None, None

    best_device = None
    best_start = None

    for device in devices:
        slot_start = find_earliest_slot(
            db, device, earliest_start, duration_minutes, exclude_order_id, respect_locked=respect_locked
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


def _calculate_device_load(db: Session, device_id: int) -> int:
    entries = db.query(ScheduleEntry).filter(ScheduleEntry.device_id == device_id).all()
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
        MaterialLock.material_id == material_id
    ).scalar()
    return material.total_quantity - locked


def check_materials_for_steps(db: Session, steps: List[ProcessStep]) -> Tuple[bool, List[Dict]]:
    shortages = []
    material_needs = {}

    for step in steps:
        for req in step.material_requirements:
            mat_id = req.material_id
            if mat_id not in material_needs:
                material_needs[mat_id] = 0
            material_needs[mat_id] += req.quantity

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


def lock_materials_for_order(db: Session, order_id: int, steps: List[ProcessStep]) -> bool:
    for step in steps:
        for req in step.material_requirements:
            lock = MaterialLock(
                order_id=order_id,
                step_id=step.id,
                material_id=req.material_id,
                quantity=req.quantity
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
            exclude_order_id=order.id, respect_locked=respect_locked
        )

        if device is None or start_time is None:
            bottleneck_step = step.step_name
            break

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
        })

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        conflict = ConflictRecord(
            order_id=order.id,
            conflict_type="scheduling_failed",
            description=f"Bottleneck at step '{bottleneck_step}': cannot schedule before deadline"
        )
        db.add(conflict)
        order.status = "failed"
        order.bottleneck_step = bottleneck_step
        db.commit()
        return {
            "success": False,
            "message": f"Cannot schedule order: bottleneck at step '{bottleneck_step}'",
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
    query = db.query(WorkOrder).filter(
        WorkOrder.is_locked == False,
        WorkOrder.status == "scheduled"
    )
    if exclude_order_id is not None:
        query = query.filter(WorkOrder.id != exclude_order_id)
    unlocked_orders = query.order_by(WorkOrder.id).all()

    for order in unlocked_orders:
        old_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order.id).all()
        old_start_times = {e.step_order: e.start_time for e in old_entries}
        old_end_times = {e.step_order: e.end_time for e in old_entries}
        old_first_start = min((e.start_time for e in old_entries), default=None)
        old_last_end = max((e.end_time for e in old_entries), default=None)

        release_material_locks_for_order(db, order.id)

        for e in old_entries:
            db.delete(e)
        db.commit()

        result = schedule_order(db, order, respect_locked=False)

        if not result["success"]:
            conflict = ConflictRecord(
                order_id=order.id,
                conflict_type="scheduling_failed",
                description=f"Order cannot be scheduled after rescheduling: {result.get('message', '')}"
            )
            db.add(conflict)
            db.commit()
        else:
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
