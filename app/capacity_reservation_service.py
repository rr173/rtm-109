from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from app.models import (
    Device, ProcessRoute, ProcessStep, ScheduleEntry,
    Material, StepMaterialRequirement, MaterialLock,
    FixtureType, Fixture, CapacityReservation, CapacityReservationSlot
)
from app.scheduler import (
    get_next_working_start, calculate_available_end, parse_time_str,
    get_device_occupied_slots, get_fixture_occupied_slots,
    get_previous_product_on_device, calculate_changeover_minutes,
    get_active_device_fault, find_next_maintenance_window,
    find_next_fault_window, get_material_available_quantity,
    check_materials_for_steps, select_best_device_and_fixture,
    find_earliest_fixture_slot
)
import threading

_trial_cache: Dict[str, List[Dict]] = {}
_trial_cache_lock = threading.Lock()


def _generate_reservation_no() -> str:
    now = datetime.now()
    return f"CR-{now.strftime('%Y%m%d%H%M%S')}-{now.microsecond // 1000:03d}"


def _release_expired_reservations(db: Session) -> int:
    now = datetime.now()
    expired = db.query(CapacityReservation).filter(
        CapacityReservation.status == "active",
        CapacityReservation.expire_at <= now
    ).all()
    count = 0
    for res in expired:
        res.status = "expired"
        res.released_at = now
        res.release_reason = "超时未下单自动释放"
        count += 1
    if count > 0:
        db.commit()
    return count


def get_reservation_occupied_device_slots(
    db: Session,
    device_id: int
) -> List[Tuple[datetime, datetime, str, str, str]]:
    _release_expired_reservations(db)
    slots = db.query(CapacityReservationSlot).options(
        joinedload(CapacityReservationSlot.reservation)
    ).filter(
        CapacityReservationSlot.device_id == device_id,
        CapacityReservationSlot.reservation.has(
            CapacityReservation.status == "active"
        )
    ).all()

    result = []
    for slot in slots:
        res = slot.reservation
        result.append((
            slot.start_time,
            slot.end_time,
            "reservation",
            res.reservation_no,
            f"产能预留[{res.reservation_no}] {res.product_name}x{res.quantity} 工序{slot.step_name}"
        ))
    return result


def get_reservation_occupied_fixture_slots(
    db: Session,
    fixture_id: int
) -> List[Tuple[datetime, datetime, str, str, str]]:
    _release_expired_reservations(db)
    slots = db.query(CapacityReservationSlot).options(
        joinedload(CapacityReservationSlot.reservation)
    ).filter(
        CapacityReservationSlot.fixture_id == fixture_id,
        CapacityReservationSlot.reservation.has(
            CapacityReservation.status == "active"
        )
    ).all()

    result = []
    for slot in slots:
        res = slot.reservation
        end = slot.fixture_turn_over_end_time or slot.end_time
        result.append((
            slot.start_time,
            end,
            "reservation",
            res.reservation_no,
            f"产能预留[{res.reservation_no}] {res.product_name}x{res.quantity} 工序{slot.step_name}"
        ))
    return result


def _trial_find_earliest_device_slot(
    db: Session,
    device: Device,
    earliest_start: datetime,
    duration_minutes: int,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None,
    extra_occupied: Optional[List[Tuple[datetime, datetime]]] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_working_start(earliest_start, device)

    occupied = get_device_occupied_slots(db, device.id)

    reservation_slots = get_reservation_occupied_device_slots(db, device.id)
    for (rs, re, _, _, _) in reservation_slots:
        occupied.append((rs, re, True))

    if extra_occupied:
        for (es, ee) in extra_occupied:
            occupied.append((es, ee, True))

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


def _trial_select_best_device(
    db: Session,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    product_name: Optional[str] = None,
    deadline: Optional[datetime] = None,
    extra_device_occupied: Optional[Dict[int, List[Tuple[datetime, datetime]]]] = None
) -> Tuple[Optional[Device], Optional[datetime]]:
    devices = db.query(Device).filter(Device.device_type == device_type).all()
    if not devices:
        return None, None

    available_devices = []
    for device in devices:
        if get_active_device_fault(db, device.id):
            continue
        available_devices.append(device)

    if not available_devices:
        return None, None

    best_device = None
    best_start = None

    for device in available_devices:
        extra = extra_device_occupied.get(device.id, []) if extra_device_occupied else []
        slot_start = _trial_find_earliest_device_slot(
            db, device, earliest_start, duration_minutes,
            product_name=product_name, deadline=deadline,
            extra_occupied=extra
        )
        if slot_start is not None:
            if best_start is None or slot_start < best_start:
                best_start = slot_start
                best_device = device

    return best_device, best_start


def _trial_find_earliest_fixture_slot(
    db: Session,
    fixture: Fixture,
    earliest_start: datetime,
    duration_minutes: int,
    turn_over_minutes: int = 0,
    extra_occupied: Optional[List[Tuple[datetime, datetime]]] = None
) -> Optional[datetime]:
    total_duration = timedelta(minutes=duration_minutes + turn_over_minutes)
    current_start = earliest_start

    occupied = get_fixture_occupied_slots(db, fixture.id)

    reservation_slots = get_reservation_occupied_fixture_slots(db, fixture.id)
    for (rs, re, _, _, _) in reservation_slots:
        occupied.append((rs, re, True))

    if extra_occupied:
        for (es, ee) in extra_occupied:
            occupied.append((es, ee, True))

    occupied.sort(key=lambda x: x[0])

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

        for (occ_start, occ_end, is_locked) in occupied:
            if current_start < occ_end and current_start + total_duration > occ_start:
                current_start = occ_end
                moved = True
                break

        if moved:
            continue

        return current_start

    return None


def _trial_get_available_fixtures(
    db: Session,
    step: ProcessStep,
    device_type: str,
    earliest_start: datetime,
    duration_minutes: int,
    extra_fixture_occupied: Optional[Dict[int, List[Tuple[datetime, datetime]]]] = None
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

    available_fixtures = []
    for fixture in fixtures:
        compatible_types = [t.strip() for t in fixture.compatible_device_types.split(",")]
        if device_type not in compatible_types:
            continue

        extra = extra_fixture_occupied.get(fixture.id, []) if extra_fixture_occupied else []
        slot_start = _trial_find_earliest_fixture_slot(
            db, fixture, earliest_start, duration_minutes,
            turn_over_minutes=fixture_type.turn_over_minutes,
            extra_occupied=extra
        )

        if slot_start is not None:
            available_fixtures.append((fixture, slot_start))

    available_fixtures.sort(key=lambda x: (x[1], x[0].id))
    return available_fixtures


def _trial_schedule_single_product(
    db: Session,
    product_name: str,
    quantity: int,
    expected_delivery_date: datetime,
    device_occupied_accumulator: Dict[int, List[Tuple[datetime, datetime]]],
    fixture_occupied_accumulator: Dict[int, List[Tuple[datetime, datetime]]]
) -> Dict:
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not route:
        return {
            "product_name": product_name,
            "quantity": quantity,
            "expected_delivery_date": expected_delivery_date,
            "can_meet_deadline": False,
            "earliest_delivery_time": None,
            "bottleneck_type": "route",
            "bottleneck_step": None,
            "bottleneck_detail": f"产品 '{product_name}' 没有定义工艺路线",
            "schedule_entries": []
        }

    steps = sorted(route.steps, key=lambda s: s.step_order)
    if not steps:
        return {
            "product_name": product_name,
            "quantity": quantity,
            "expected_delivery_date": expected_delivery_date,
            "can_meet_deadline": False,
            "earliest_delivery_time": None,
            "bottleneck_type": "route",
            "bottleneck_step": None,
            "bottleneck_detail": "工艺路线没有工序",
            "schedule_entries": []
        }

    materials_ok, material_shortages = check_materials_for_steps(db, steps, multiplier=quantity)
    if not materials_ok:
        shortage_descs = []
        for s in material_shortages:
            shortage_descs.append(
                f"{s['material_name']}: 需要{s['needed']}, 可用{s['available']}, 缺{s['shortage']}"
            )
        return {
            "product_name": product_name,
            "quantity": quantity,
            "expected_delivery_date": expected_delivery_date,
            "can_meet_deadline": False,
            "earliest_delivery_time": None,
            "bottleneck_type": "material",
            "bottleneck_step": material_shortages[0]["material_name"],
            "bottleneck_detail": f"物料不足: {'; '.join(shortage_descs)}",
            "schedule_entries": []
        }

    now = datetime.now()
    prev_end_time = max(now, expected_delivery_date - timedelta(days=30))
    prev_step = None
    schedule_entries = []
    bottleneck_step = None
    bottleneck_type = None
    bottleneck_detail = None

    for step in steps:
        earliest_start = prev_end_time
        if prev_step and prev_step.min_gap_after > 0:
            earliest_start = prev_end_time + timedelta(minutes=prev_step.min_gap_after)

        if step.fixture_type_id is not None:
            best_device = None
            best_fixture = None
            best_start = None
            best_step_bn_type = None
            best_step_bn_detail = None

            devices = db.query(Device).filter(Device.device_type == step.device_type).all()
            available_devices = [d for d in devices if not get_active_device_fault(db, d.id)]

            if not available_devices:
                bottleneck_step = step.step_name
                bottleneck_type = "device"
                bottleneck_detail = f"工序 '{step.step_name}' 所需设备类型 '{step.device_type}' 无可用设备(故障或不存在)"
                break

            has_device = False
            has_fixture = False
            all_candidates = []

            for device in available_devices:
                dev_slot = _trial_find_earliest_device_slot(
                    db, device, earliest_start, step.duration_minutes,
                    product_name=product_name,
                    extra_occupied=device_occupied_accumulator.get(device.id, [])
                )
                if dev_slot is None:
                    continue
                has_device = True

                fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
                fixtures = db.query(Fixture).filter(
                    Fixture.fixture_type_id == step.fixture_type_id,
                    Fixture.status == "available"
                ).all()

                for fixture in fixtures:
                    compatible_types = [t.strip() for t in fixture.compatible_device_types.split(",")]
                    if device.device_type not in compatible_types:
                        continue

                    turn_over = fixture_type.turn_over_minutes if fixture_type else 0
                    extra_fix = fixture_occupied_accumulator.get(fixture.id, []) if fixture_occupied_accumulator else []
                    fix_slot = _trial_find_earliest_fixture_slot(
                        db, fixture, max(earliest_start, dev_slot), step.duration_minutes,
                        turn_over_minutes=turn_over,
                        extra_occupied=extra_fix
                    )
                    if fix_slot is None:
                        continue
                    has_fixture = True
                    combined_start = max(dev_slot, fix_slot)
                    all_candidates.append((combined_start, device, fixture))

            if not all_candidates:
                if has_device and not has_fixture:
                    fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
                    bottleneck_step = step.step_name
                    bottleneck_type = "fixture"
                    bottleneck_detail = f"工序 '{step.step_name}' 工装不足(类型: {fixture_type.name if fixture_type else step.fixture_type_id})"
                else:
                    bottleneck_step = step.step_name
                    bottleneck_type = "device"
                    bottleneck_detail = f"工序 '{step.step_name}' 设备产能不足，无法在截止时间前安排"
                break

            all_candidates.sort(key=lambda x: x[0])
            best_start, best_device, best_fixture = all_candidates[0]

        else:
            best_device, best_start = _trial_select_best_device(
                db, step.device_type, earliest_start, step.duration_minutes,
                product_name=product_name,
                deadline=expected_delivery_date,
                extra_device_occupied=device_occupied_accumulator
            )

            if best_device is None or best_start is None:
                bottleneck_step = step.step_name
                bottleneck_type = "device"
                bottleneck_detail = f"工序 '{step.step_name}' 设备类型 '{step.device_type}' 产能不足，无法在截止时间前安排"
                break

            best_fixture = None

        prev_product = get_previous_product_on_device(db, best_device.id, best_start)
        changeover_minutes, changeover_type = calculate_changeover_minutes(
            db, best_device.id, prev_product, product_name
        )

        changeover_start_time = None
        changeover_end_time = None
        if changeover_minutes > 0:
            changeover_start_time = best_start
            changeover_end_time = best_start + timedelta(minutes=changeover_minutes)
            best_start = changeover_end_time

        end_time = best_start + timedelta(minutes=step.duration_minutes)

        fixture_code = None
        if best_fixture:
            fixture_code = best_fixture.code

        schedule_entries.append({
            "step_order": step.step_order,
            "step_name": step.step_name,
            "device_id": best_device.id,
            "device_name": best_device.name,
            "device_type": best_device.device_type,
            "start_time": best_start,
            "end_time": end_time,
            "changeover_minutes": changeover_minutes,
            "fixture_id": best_fixture.id if best_fixture else None,
            "fixture_code": fixture_code,
        })

        occupied_start = changeover_start_time if changeover_start_time else best_start
        if best_device.id not in device_occupied_accumulator:
            device_occupied_accumulator[best_device.id] = []
        device_occupied_accumulator[best_device.id].append((occupied_start, end_time))

        if best_fixture:
            if best_fixture.id not in fixture_occupied_accumulator:
                fixture_occupied_accumulator[best_fixture.id] = []
            fixture_occupied_accumulator[best_fixture.id].append((best_start, end_time))

        prev_end_time = end_time
        prev_step = step

    if bottleneck_step is not None:
        for entry in schedule_entries:
            dev_id = entry["device_id"]
            if dev_id in device_occupied_accumulator:
                try:
                    device_occupied_accumulator[dev_id].remove((entry["start_time"], entry["end_time"]))
                except ValueError:
                    pass
            if entry["fixture_id"] and entry["fixture_id"] in fixture_occupied_accumulator:
                try:
                    fixture_occupied_accumulator[entry["fixture_id"]].remove((entry["start_time"], entry["end_time"]))
                except ValueError:
                    pass

        return {
            "product_name": product_name,
            "quantity": quantity,
            "expected_delivery_date": expected_delivery_date,
            "can_meet_deadline": False,
            "earliest_delivery_time": None,
            "bottleneck_type": bottleneck_type,
            "bottleneck_step": bottleneck_step,
            "bottleneck_detail": bottleneck_detail,
            "schedule_entries": []
        }

    earliest_delivery = schedule_entries[-1]["end_time"] if schedule_entries else None
    can_meet = earliest_delivery is not None and earliest_delivery <= expected_delivery_date

    return {
        "product_name": product_name,
        "quantity": quantity,
        "expected_delivery_date": expected_delivery_date,
        "can_meet_deadline": can_meet,
        "earliest_delivery_time": earliest_delivery,
        "bottleneck_type": None if can_meet else "deadline",
        "bottleneck_step": None if can_meet else schedule_entries[-1]["step_name"] if schedule_entries else None,
        "bottleneck_detail": None if can_meet else f"最早可交付时间 {earliest_delivery} 晚于期望交付日期 {expected_delivery_date}",
        "schedule_entries": schedule_entries
    }


def trial_schedule(
    db: Session,
    items: List[Dict]
) -> Dict:
    _release_expired_reservations(db)

    device_occupied: Dict[int, List[Tuple[datetime, datetime]]] = {}
    fixture_occupied: Dict[int, List[Tuple[datetime, datetime]]] = {}

    results = []
    for item in items:
        result = _trial_schedule_single_product(
            db,
            item["product_name"],
            item["quantity"],
            item["expected_delivery_date"],
            device_occupied,
            fixture_occupied
        )
        results.append(result)

    cache_key = _generate_reservation_no()
    with _trial_cache_lock:
        _trial_cache[cache_key] = results

    return {
        "success": True,
        "message": f"试算完成，共{len(items)}条，成功{sum(1 for r in results if r['can_meet_deadline'])}条",
        "results": results,
        "cache_key": cache_key
    }


def lock_reservation(
    db: Session,
    trial_results: List[Dict],
    trial_result_index: int,
    customer_name: Optional[str] = None,
    sales_person: Optional[str] = None,
    lock_duration_hours: int = 24
) -> Dict:
    _release_expired_reservations(db)

    if trial_result_index < 0 or trial_result_index >= len(trial_results):
        return {
            "success": False,
            "message": f"试算结果索引 {trial_result_index} 超出范围(0-{len(trial_results) - 1})"
        }

    result = trial_results[trial_result_index]
    if not result.get("schedule_entries"):
        return {
            "success": False,
            "message": "该试算结果没有排产条目，无法锁定产能预留"
        }

    now = datetime.now()
    reservation_no = _generate_reservation_no()

    reservation = CapacityReservation(
        reservation_no=reservation_no,
        product_name=result["product_name"],
        quantity=result["quantity"],
        customer_name=customer_name,
        sales_person=sales_person,
        status="active",
        expire_at=now + timedelta(hours=lock_duration_hours),
        trial_earliest_delivery=result.get("earliest_delivery_time"),
        trial_expected_delivery=result.get("expected_delivery_date"),
        trial_can_meet_deadline=result.get("can_meet_deadline", True),
        trial_bottleneck_type=result.get("bottleneck_type"),
        trial_bottleneck_step=result.get("bottleneck_step"),
        trial_bottleneck_detail=result.get("bottleneck_detail"),
    )
    db.add(reservation)
    db.flush()

    for entry in result["schedule_entries"]:
        slot = CapacityReservationSlot(
            reservation_id=reservation.id,
            device_id=entry["device_id"],
            fixture_id=entry.get("fixture_id"),
            step_order=entry["step_order"],
            step_name=entry["step_name"],
            start_time=entry["start_time"],
            end_time=entry["end_time"],
        )
        db.add(slot)

    db.commit()
    db.refresh(reservation)

    return {
        "success": True,
        "message": f"产能预留已锁定，预留号: {reservation_no}，将在 {lock_duration_hours} 小时后自动释放",
        "reservation_id": reservation.id,
        "reservation_no": reservation_no,
        "expire_at": reservation.expire_at
    }


def list_reservations(
    db: Session,
    status: Optional[str] = None
) -> Dict:
    _release_expired_reservations(db)
    db.flush()

    query = db.query(CapacityReservation).options(
        joinedload(CapacityReservation.slots)
    )

    if status:
        query = query.filter(CapacityReservation.status == status)

    reservations = query.order_by(CapacityReservation.created_at.desc()).all()

    now = datetime.now()
    active_count = sum(1 for r in reservations if r.status == "active")

    result_list = []
    for res in reservations:
        remaining = None
        if res.status == "active" and res.expire_at:
            remaining = max(0, int((res.expire_at - now).total_seconds()))

        slots_info = []
        for slot in res.slots:
            device = db.query(Device).filter(Device.id == slot.device_id).first()
            fixture_code = None
            if slot.fixture_id:
                fixture = db.query(Fixture).filter(Fixture.id == slot.fixture_id).first()
                fixture_code = fixture.code if fixture else None
            slots_info.append({
                "id": slot.id,
                "device_id": slot.device_id,
                "device_name": device.name if device else None,
                "fixture_id": slot.fixture_id,
                "fixture_code": fixture_code,
                "step_order": slot.step_order,
                "step_name": slot.step_name,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "fixture_turn_over_end_time": slot.fixture_turn_over_end_time,
            })

        result_list.append({
            "id": res.id,
            "reservation_no": res.reservation_no,
            "product_name": res.product_name,
            "quantity": res.quantity,
            "customer_name": res.customer_name,
            "sales_person": res.sales_person,
            "status": res.status,
            "expire_at": res.expire_at,
            "created_at": res.created_at,
            "released_at": res.released_at,
            "release_reason": res.release_reason,
            "trial_earliest_delivery": res.trial_earliest_delivery,
            "trial_expected_delivery": res.trial_expected_delivery,
            "trial_can_meet_deadline": res.trial_can_meet_deadline,
            "trial_bottleneck_type": res.trial_bottleneck_type,
            "trial_bottleneck_step": res.trial_bottleneck_step,
            "trial_bottleneck_detail": res.trial_bottleneck_detail,
            "slots": slots_info,
            "remaining_seconds": remaining,
        })

    return {
        "reservations": result_list,
        "total": len(result_list),
        "active_count": active_count
    }


def release_reservation(
    db: Session,
    reservation_id: int,
    reason: Optional[str] = None
) -> Dict:
    _release_expired_reservations(db)

    reservation = db.query(CapacityReservation).filter(
        CapacityReservation.id == reservation_id
    ).first()
    if not reservation:
        return {
            "success": False,
            "message": f"产能预留 ID {reservation_id} 不存在"
        }

    if reservation.status != "active":
        return {
            "success": False,
            "message": f"产能预留 '{reservation.reservation_no}' 状态为 {reservation.status}，只有活跃状态才能手动释放"
        }

    reservation.status = "released"
    reservation.released_at = datetime.now()
    reservation.release_reason = reason or "手动释放"
    db.commit()

    return {
        "success": True,
        "message": f"产能预留 '{reservation.reservation_no}' 已释放",
        "reservation_id": reservation.id,
        "reservation_no": reservation.reservation_no
    }


def find_reservation_blockers(
    db: Session,
    device_id: int,
    start_time: datetime,
    end_time: datetime
) -> List[Dict]:
    _release_expired_reservations(db)
    db.flush()

    conflicting_slots = db.query(CapacityReservationSlot).options(
        joinedload(CapacityReservationSlot.reservation)
    ).filter(
        CapacityReservationSlot.device_id == device_id,
        CapacityReservationSlot.start_time < end_time,
        CapacityReservationSlot.end_time > start_time,
        CapacityReservationSlot.reservation.has(
            CapacityReservation.status == "active"
        )
    ).all()

    blockers = []
    for slot in conflicting_slots:
        res = slot.reservation
        blockers.append({
            "reservation_id": res.id,
            "reservation_no": res.reservation_no,
            "product_name": res.product_name,
            "step_name": slot.step_name,
            "step_order": slot.step_order,
            "start_time": slot.start_time,
            "end_time": slot.end_time,
        })

    return blockers


def find_fixture_reservation_blockers(
    db: Session,
    fixture_id: int,
    start_time: datetime,
    end_time: datetime
) -> List[Dict]:
    _release_expired_reservations(db)
    db.flush()

    conflicting_slots = db.query(CapacityReservationSlot).options(
        joinedload(CapacityReservationSlot.reservation)
    ).filter(
        CapacityReservationSlot.fixture_id == fixture_id,
        CapacityReservationSlot.start_time < end_time,
        CapacityReservationSlot.end_time > start_time,
        CapacityReservationSlot.reservation.has(
            CapacityReservation.status == "active"
        )
    ).all()

    blockers = []
    for slot in conflicting_slots:
        res = slot.reservation
        slot_end = slot.fixture_turn_over_end_time or slot.end_time
        if slot_end > start_time and slot.start_time < end_time:
            blockers.append({
                "reservation_id": res.id,
                "reservation_no": res.reservation_no,
                "product_name": res.product_name,
                "step_name": slot.step_name,
                "step_order": slot.step_order,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
            })

    return blockers
