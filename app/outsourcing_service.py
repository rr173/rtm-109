from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from app.models import (
    OutsourcingFactory, OutsourcingCapability, StepOutsourcingConfig,
    OutsourcingScheduleEntry, ProcessStep, WorkOrder, SubBatch, ScheduleEntry
)
from sqlalchemy import and_, or_


OUTSOURCE_NODE_TYPES = [
    "waiting_to_ship",
    "in_transit_to",
    "outsourcing_process",
    "in_transit_back",
    "returned_waiting"
]

NODE_DESCRIPTIONS = {
    "waiting_to_ship": "出厂等待发运",
    "in_transit_to": "去程运输中",
    "outsourcing_process": "外协加工中",
    "in_transit_back": "回程运输中",
    "returned_waiting": "回厂待下道工序"
}


def parse_time_str(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def is_within_factory_hours(dt: datetime, factory: OutsourcingFactory) -> bool:
    start = parse_time_str(factory.daily_start)
    end = parse_time_str(factory.daily_end)
    t = dt.time()
    return start <= t <= end


def get_next_factory_working_start(dt: datetime, factory: OutsourcingFactory) -> datetime:
    start_time = parse_time_str(factory.daily_start)
    end_time = parse_time_str(factory.daily_end)

    if dt.time() > end_time:
        next_day = dt.date() + timedelta(days=1)
        return datetime.combine(next_day, start_time)
    elif dt.time() < start_time:
        return datetime.combine(dt.date(), start_time)
    else:
        return dt


def calculate_factory_available_end(dt: datetime, factory: OutsourcingFactory) -> datetime:
    end_time = parse_time_str(factory.daily_end)
    return datetime.combine(dt.date(), end_time)


def get_factory_concurrent_slots(
    db: Session,
    factory_id: int,
    exclude_order_id: Optional[int] = None,
    scenario_id: Optional[int] = None
) -> List[Tuple[datetime, datetime]]:
    query = db.query(OutsourcingScheduleEntry).filter(
        OutsourcingScheduleEntry.factory_id == factory_id,
        OutsourcingScheduleEntry.node_type == "outsourcing_process",
        OutsourcingScheduleEntry.is_completed == False
    )
    if exclude_order_id is not None:
        query = query.filter(OutsourcingScheduleEntry.order_id != exclude_order_id)
    if scenario_id is not None:
        query = query.filter(OutsourcingScheduleEntry.scenario_id == scenario_id)
    else:
        query = query.filter(OutsourcingScheduleEntry.scenario_id.is_(None))

    entries = query.order_by(OutsourcingScheduleEntry.start_time).all()
    slots = []
    for e in entries:
        slots.append((e.start_time, e.end_time))
    return slots


def count_concurrent_at_time(
    slots: List[Tuple[datetime, datetime]],
    check_time: datetime
) -> int:
    count = 0
    for (start, end) in slots:
        if start <= check_time < end:
            count += 1
    return count


def find_earliest_outsourcing_process_slot(
    db: Session,
    factory: OutsourcingFactory,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    scenario_id: Optional[int] = None,
    sibling_process_entries: Optional[List[Tuple[datetime, datetime]]] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = get_next_factory_working_start(earliest_start, factory)

    occupied = get_factory_concurrent_slots(db, factory.id, exclude_order_id, scenario_id)

    if sibling_process_entries:
        occupied.extend(sibling_process_entries)
        occupied.sort(key=lambda x: x[0])

    max_iterations = 365 * 24 * 60
    iterations = 0

    while iterations < max_iterations:
        iterations += 1
        moved = False

        day_end = calculate_factory_available_end(current_start, factory)
        if current_start + duration > day_end:
            next_day = current_start.date() + timedelta(days=1)
            current_start = datetime.combine(next_day, parse_time_str(factory.daily_start))
            continue

        check_points = [current_start + timedelta(minutes=m)
                       for m in range(0, duration_minutes, 30)]
        check_points.append(current_start + duration - timedelta(minutes=1))

        max_concurrent = 0
        for cp in check_points:
            concurrent = count_concurrent_at_time(occupied, cp)
            if concurrent > max_concurrent:
                max_concurrent = concurrent

        if max_concurrent >= factory.max_concurrent_jobs:
            conflict_start = None
            for (occ_start, occ_end) in occupied:
                if occ_start <= current_start < occ_end:
                    conflict_start = occ_end
                    break
            if conflict_start is None:
                overlapping = [(s, e) for s, e in occupied
                              if not (e <= current_start or s >= current_start + duration)]
                if overlapping:
                    conflict_start = max(e for _, e in overlapping)

            if conflict_start:
                current_start = get_next_factory_working_start(conflict_start, factory)
                moved = True

        if moved:
            continue

        for (occ_start, occ_end) in occupied:
            if current_start < occ_end and current_start + duration > occ_start:
                if count_concurrent_at_time(occupied, current_start) >= factory.max_concurrent_jobs:
                    current_start = get_next_factory_working_start(occ_end, factory)
                    moved = True
                    break

        if moved:
            continue

        return current_start

    return None


def calculate_outsourcing_process_duration(
    factory: OutsourcingFactory,
    capability: OutsourcingCapability,
    quantity: int
) -> int:
    effective_quantity = max(quantity, capability.min_batch_quantity)
    if capability.max_batch_quantity:
        effective_quantity = min(effective_quantity, capability.max_batch_quantity)

    base_minutes = capability.base_duration_minutes
    per_unit_minutes = capability.duration_per_unit_minutes * effective_quantity
    total_minutes = base_minutes + per_unit_minutes

    if capability.efficiency_factor != 100:
        total_minutes = int(total_minutes * 100 / capability.efficiency_factor)

    return max(total_minutes, 1)


def get_available_factories_for_step(
    db: Session,
    step: ProcessStep,
    scenario_id: Optional[int] = None
) -> List[Tuple[OutsourcingFactory, OutsourcingCapability, int]]:
    if not step.is_outsource or not step.outsource_process_type:
        return []

    configs = db.query(StepOutsourcingConfig).filter(
        StepOutsourcingConfig.step_id == step.id
    ).order_by(
        StepOutsourcingConfig.is_preferred.desc(),
        StepOutsourcingConfig.priority.desc()
    ).all()

    if not configs:
        return []

    factory_ids = [c.factory_id for c in configs]
    config_order = {c.factory_id: (c.is_preferred, c.priority) for c in configs}

    query = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities)
    ).filter(
        OutsourcingFactory.id.in_(factory_ids),
        OutsourcingFactory.is_active == True
    )
    if scenario_id is not None:
        query = query.filter(
            or_(
                OutsourcingFactory.scenario_id.is_(None),
                OutsourcingFactory.scenario_id == scenario_id
            )
        )
    else:
        query = query.filter(OutsourcingFactory.scenario_id.is_(None))

    factories = query.all()

    result = []
    for factory in factories:
        capability = None
        for cap in factory.capabilities:
            if cap.process_type == step.outsource_process_type:
                capability = cap
                break
        if capability:
            result.append((factory, capability, config_order.get(factory.id, (False, 0))))

    result.sort(key=lambda x: (
        not x[2][0],
        -x[2][1],
        x[0].transport_to_minutes + x[0].transport_back_minutes
    ))
    return result


def schedule_outsourcing_step(
    db: Session,
    order: WorkOrder,
    sub_batch: Optional[SubBatch],
    step: ProcessStep,
    quantity: int,
    earliest_start: datetime,
    deadline: Optional[datetime] = None,
    exclude_order_id: Optional[int] = None,
    scenario_id: Optional[int] = None,
    sibling_process_entries: Optional[List[Tuple[int, datetime, datetime]]] = None
) -> Tuple[bool, List[Dict], Optional[OutsourcingFactory], Optional[str], Optional[str]]:
    factories = get_available_factories_for_step(db, step, scenario_id)

    if not factories:
        return False, [], None, "outsourcing", f"工序 '{step.step_name}' 没有可用的外协厂配置"

    sub_batch_id = sub_batch.id if sub_batch else None
    quantity_for_calc = sub_batch.quantity if sub_batch else (quantity or order.total_quantity)

    best_result = None
    best_factory = None
    best_end_time = None
    bottleneck_msg = None
    bottleneck_type = None

    sibling_entries_local = []
    if sibling_process_entries:
        for (fid, s, e) in sibling_process_entries:
            sibling_entries_local.append((s, e))

    for factory, capability, _ in factories:
        process_duration = calculate_outsourcing_process_duration(factory, capability, quantity_for_calc)

        waiting_ship_start = earliest_start
        waiting_ship_end = waiting_ship_start

        transit_to_start = waiting_ship_end
        transit_to_end = transit_to_start + timedelta(minutes=factory.transport_to_minutes)

        process_earliest_start = transit_to_end + timedelta(minutes=factory.waiting_before_process_minutes)

        process_entries_for_factory = []
        if sibling_process_entries:
            process_entries_for_factory = [
                (s, e) for (fid, s, e) in sibling_process_entries if fid == factory.id
            ]

        process_start = find_earliest_outsourcing_process_slot(
            db, factory, process_earliest_start, process_duration,
            exclude_order_id=exclude_order_id or order.id,
            scenario_id=scenario_id,
            sibling_process_entries=process_entries_for_factory
        )

        if process_start is None:
            if bottleneck_msg is None:
                bottleneck_msg = f"外协厂 '{factory.name}' 并发上限已满，无法安排加工"
                bottleneck_type = "outsourcing_concurrent"
            continue

        process_end = process_start + timedelta(minutes=process_duration)

        if deadline and process_end > deadline:
            if bottleneck_msg is None:
                bottleneck_msg = f"外协厂 '{factory.name}' 加工完成时间超出工单截止时间"
                bottleneck_type = "outsourcing_deadline"
            continue

        transit_back_start = process_end
        transit_back_end = transit_back_start + timedelta(minutes=factory.transport_back_minutes)

        returned_wait_start = transit_back_end
        returned_wait_end = returned_wait_start

        total_end = returned_wait_end

        nodes = [
            {
                "node_type": "waiting_to_ship",
                "node_sequence": 1,
                "start_time": waiting_ship_start,
                "end_time": waiting_ship_end,
                "description": "出厂等待发运",
                "duration_minutes": 0
            },
            {
                "node_type": "in_transit_to",
                "node_sequence": 2,
                "start_time": transit_to_start,
                "end_time": transit_to_end,
                "description": f"去程运输至 {factory.name}",
                "duration_minutes": factory.transport_to_minutes
            },
            {
                "node_type": "outsourcing_process",
                "node_sequence": 3,
                "start_time": process_start,
                "end_time": process_end,
                "description": f"{factory.name} 外协加工",
                "duration_minutes": process_duration
            },
            {
                "node_type": "in_transit_back",
                "node_sequence": 4,
                "start_time": transit_back_start,
                "end_time": transit_back_end,
                "description": f"从 {factory.name} 回程运输",
                "duration_minutes": factory.transport_back_minutes
            },
            {
                "node_type": "returned_waiting",
                "node_sequence": 5,
                "start_time": returned_wait_start,
                "end_time": returned_wait_end,
                "description": "回厂待下道工序",
                "duration_minutes": 0
            }
        ]

        if best_end_time is None or total_end < best_end_time:
            best_result = nodes
            best_factory = factory
            best_end_time = total_end

    if best_result is None:
        if bottleneck_msg is None:
            bottleneck_msg = f"工序 '{step.step_name}' 所有外协厂均无法安排"
            bottleneck_type = "outsourcing"
        return False, [], None, bottleneck_type or "outsourcing", bottleneck_msg

    return True, best_result, best_factory, None, None


def create_outsourcing_schedule_entries(
    db: Session,
    order: WorkOrder,
    sub_batch: Optional[SubBatch],
    step: ProcessStep,
    factory: OutsourcingFactory,
    nodes: List[Dict],
    quantity: int,
    scenario_id: Optional[int] = None
) -> List[OutsourcingScheduleEntry]:
    entries = []
    sub_batch_id = sub_batch.id if sub_batch else None
    actual_quantity = sub_batch.quantity if sub_batch else quantity

    for node in nodes:
        entry = OutsourcingScheduleEntry(
            order_id=order.id,
            sub_batch_id=sub_batch_id,
            step_id=step.id,
            factory_id=factory.id,
            step_order=step.step_order,
            step_name=step.step_name,
            node_type=node["node_type"],
            node_sequence=node["node_sequence"],
            start_time=node["start_time"],
            end_time=node["end_time"],
            quantity=actual_quantity,
            is_completed=False,
            scenario_id=scenario_id
        )
        db.add(entry)
        entries.append(entry)

    db.flush()
    return entries


def delete_outsourcing_entries_for_order(
    db: Session,
    order_id: int,
    scenario_id: Optional[int] = None
) -> int:
    query = db.query(OutsourcingScheduleEntry).filter(
        OutsourcingScheduleEntry.order_id == order_id
    )
    if scenario_id is not None:
        query = query.filter(OutsourcingScheduleEntry.scenario_id == scenario_id)
    else:
        query = query.filter(OutsourcingScheduleEntry.scenario_id.is_(None))

    count = query.count()
    query.delete(synchronize_session=False)
    db.flush()
    return count


def delete_outsourcing_entries_for_sub_batch(
    db: Session,
    sub_batch_id: int
) -> int:
    query = db.query(OutsourcingScheduleEntry).filter(
        OutsourcingScheduleEntry.sub_batch_id == sub_batch_id
    )
    count = query.count()
    query.delete(synchronize_session=False)
    db.flush()
    return count


def get_order_outsourcing_status(
    db: Session,
    order_id: int,
    scenario_id: Optional[int] = None
) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    query = db.query(OutsourcingScheduleEntry).options(
        joinedload(OutsourcingScheduleEntry.factory)
    ).filter(
        OutsourcingScheduleEntry.order_id == order_id
    )
    if scenario_id is not None:
        query = query.filter(OutsourcingScheduleEntry.scenario_id == scenario_id)
    else:
        query = query.filter(OutsourcingScheduleEntry.scenario_id.is_(None))

    entries = query.order_by(
        OutsourcingScheduleEntry.step_order,
        OutsourcingScheduleEntry.node_sequence
    ).all()

    route = None
    steps = []
    from app.models import ProcessRoute
    route = db.query(ProcessRoute).filter(
        ProcessRoute.product_name == order.product_name
    ).first()
    if route:
        steps = sorted(route.steps, key=lambda s: s.step_order)

    outsource_steps = [s for s in steps if s.is_outsource]
    total_outsource_steps = len(outsource_steps)

    completed_outsource_steps = 0
    for s in outsource_steps:
        step_entries = [e for e in entries
                       if e.step_order == s.step_order and e.node_type == "outsourcing_process"]
        if step_entries and all(e.is_completed for e in step_entries):
            completed_outsource_steps += 1

    now = datetime.utcnow()
    overall_status = "in_factory"
    current_step_order = None
    current_step_name = None
    current_node_type = None
    current_factory_id = None
    current_factory_name = None
    current_node_start = None
    current_node_end = None

    in_factory_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.is_completed == False
    ).order_by(ScheduleEntry.start_time).all()

    if entries:
        active_nodes = []
        for e in entries:
            if not e.is_completed and e.start_time <= now < e.end_time:
                active_nodes.append(e)

        if active_nodes:
            active_nodes.sort(key=lambda n: (n.step_order, n.node_sequence))
            current = active_nodes[0]
            current_step_order = current.step_order
            current_step_name = current.step_name
            current_node_type = current.node_type
            current_factory_id = current.factory_id
            current_factory_name = current.factory.name if current.factory else None
            current_node_start = current.start_time
            current_node_end = current.end_time

            if current.node_type in ["in_transit_to", "in_transit_back"]:
                overall_status = "in_transit"
            elif current.node_type == "outsourcing_process":
                overall_status = "outsourcing_process"
            elif current.node_type == "waiting_to_ship":
                overall_status = "waiting_to_ship"
            elif current.node_type == "returned_waiting":
                overall_status = "returned_waiting"
        else:
            next_nodes = [e for e in entries if not e.is_completed and e.start_time > now]
            if next_nodes:
                overall_status = "in_factory"
            else:
                if order.status == "completed":
                    overall_status = "completed"
                else:
                    overall_status = "in_factory"
    elif in_factory_entries:
        overall_status = "in_factory"

    outsourcing_nodes = []
    for e in entries:
        desc = NODE_DESCRIPTIONS.get(e.node_type, e.node_type)
        if e.node_type in ["in_transit_to", "outsourcing_process", "in_transit_back"]:
            if e.factory:
                if e.node_type == "in_transit_to":
                    desc = f"去程运输至 {e.factory.name}"
                elif e.node_type == "outsourcing_process":
                    desc = f"{e.factory.name} 外协加工中"
                elif e.node_type == "in_transit_back":
                    desc = f"从 {e.factory.name} 回程运输"
        outsourcing_nodes.append({
            "node_type": e.node_type,
            "node_sequence": e.node_sequence,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "description": desc
        })

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "overall_status": overall_status,
        "current_step_order": current_step_order,
        "current_step_name": current_step_name,
        "current_node_type": current_node_type,
        "current_factory_id": current_factory_id,
        "current_factory_name": current_factory_name,
        "current_node_start": current_node_start,
        "current_node_end": current_node_end,
        "outsourcing_nodes": outsourcing_nodes,
        "total_outsource_steps": total_outsource_steps,
        "completed_outsource_steps": completed_outsource_steps
    }


def get_factory_load(
    db: Session,
    factory_id: int,
    look_ahead_days: int = 7,
    scenario_id: Optional[int] = None
) -> Optional[Dict]:
    factory = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities)
    ).filter(OutsourcingFactory.id == factory_id).first()

    if not factory:
        return None

    now = datetime.utcnow()
    end_time = now + timedelta(days=look_ahead_days)

    query = db.query(OutsourcingScheduleEntry).options(
        joinedload(OutsourcingScheduleEntry.order),
        joinedload(OutsourcingScheduleEntry.sub_batch)
    ).filter(
        OutsourcingScheduleEntry.factory_id == factory_id,
        OutsourcingScheduleEntry.start_time < end_time,
        OutsourcingScheduleEntry.end_time > now,
        OutsourcingScheduleEntry.is_completed == False
    )
    if scenario_id is not None:
        query = query.filter(OutsourcingScheduleEntry.scenario_id == scenario_id)
    else:
        query = query.filter(OutsourcingScheduleEntry.scenario_id.is_(None))

    all_entries = query.order_by(OutsourcingScheduleEntry.start_time).all()

    daily_start_min = parse_time_str(factory.daily_start)
    daily_end_min = parse_time_str(factory.daily_end)
    daily_available_minutes = (
        (daily_end_min.hour * 60 + daily_end_min.minute) -
        (daily_start_min.hour * 60 + daily_start_min.minute)
    )

    days = []
    for day_offset in range(look_ahead_days):
        current_date = now.date() + timedelta(days=day_offset)
        day_start = datetime.combine(current_date, time.min)
        day_end = day_start + timedelta(days=1)

        day_entries = []
        total_process_minutes = 0
        concurrent_checks = []

        for e in all_entries:
            entry_start = e.start_time
            entry_end = e.end_time

            if entry_end <= day_start or entry_start >= day_end:
                continue

            overlap_start = max(entry_start, day_start)
            overlap_end = min(entry_end, day_end)
            overlap_minutes = int((overlap_end - overlap_start).total_seconds() / 60)

            is_processing = (e.node_type == "outsourcing_process")
            if is_processing:
                total_process_minutes += overlap_minutes

            concurrent_checks.append((overlap_start, overlap_end, e))

            order = e.order
            sub_batch = e.sub_batch

            day_entries.append({
                "order_id": e.order_id,
                "order_no": order.order_no if order else None,
                "sub_batch_id": e.sub_batch_id,
                "batch_no": sub_batch.batch_no if sub_batch else None,
                "step_order": e.step_order,
                "step_name": e.step_name,
                "node_type": e.node_type,
                "node_sequence": e.node_sequence,
                "start_time": overlap_start,
                "end_time": overlap_end,
                "quantity": e.quantity,
                "is_processing_node": is_processing
            })

        concurrent_peak = 0
        if concurrent_checks:
            check_times = set()
            for (s, e, _) in concurrent_checks:
                check_times.add(s)
                check_times.add(e)
            for ct in sorted(check_times):
                count = 0
                for (s, e, entry) in concurrent_checks:
                    if entry.node_type == "outsourcing_process" and s <= ct < e:
                        count += 1
                concurrent_peak = max(concurrent_peak, count)

        utilization_rate = 0.0
        if daily_available_minutes > 0:
            utilization_rate = round((total_process_minutes / daily_available_minutes) * 100, 2)

        days.append({
            "date": current_date.isoformat(),
            "total_scheduled_minutes": total_process_minutes,
            "available_minutes": daily_available_minutes,
            "utilization_rate": utilization_rate,
            "concurrent_peak": concurrent_peak,
            "max_concurrent": factory.max_concurrent_jobs,
            "entries": day_entries
        })

    in_process_count = 0
    queued_count = 0
    in_transit_to_count = 0
    in_transit_back_count = 0
    returned_waiting_count = 0

    for e in all_entries:
        if e.is_completed:
            continue
        if e.start_time <= now < e.end_time:
            if e.node_type == "outsourcing_process":
                in_process_count += 1
            elif e.node_type == "waiting_to_ship":
                queued_count += 1
            elif e.node_type == "in_transit_to":
                in_transit_to_count += 1
            elif e.node_type == "in_transit_back":
                in_transit_back_count += 1
            elif e.node_type == "returned_waiting":
                returned_waiting_count += 1
        elif e.start_time > now and e.node_type == "outsourcing_process":
            queued_count += 1

    return {
        "factory_id": factory.id,
        "factory_name": factory.name,
        "factory_code": factory.code,
        "look_ahead_days": look_ahead_days,
        "days": days,
        "in_process_count": in_process_count,
        "queued_count": queued_count,
        "in_transit_to_count": in_transit_to_count,
        "in_transit_back_count": in_transit_back_count,
        "returned_waiting_count": returned_waiting_count
    }


def detect_outsourcing_bottlenecks(
    db: Session,
    scenario_id: Optional[int] = None,
    look_ahead_days: int = 7
) -> List[Dict]:
    bottlenecks = []
    now = datetime.utcnow()
    end_time = now + timedelta(days=look_ahead_days)

    query = db.query(OutsourcingFactory).options(
        joinedload(OutsourcingFactory.capabilities),
        joinedload(OutsourcingFactory.schedule_entries)
    ).filter(OutsourcingFactory.is_active == True)
    if scenario_id is not None:
        query = query.filter(
            or_(
                OutsourcingFactory.scenario_id.is_(None),
                OutsourcingFactory.scenario_id == scenario_id
            )
        )
    else:
        query = query.filter(OutsourcingFactory.scenario_id.is_(None))

    factories = query.all()

    for factory in factories:
        entries_query = db.query(OutsourcingScheduleEntry).options(
            joinedload(OutsourcingScheduleEntry.order)
        ).filter(
            OutsourcingScheduleEntry.factory_id == factory.id,
            OutsourcingScheduleEntry.node_type == "outsourcing_process",
            OutsourcingScheduleEntry.start_time < end_time,
            OutsourcingScheduleEntry.end_time > now,
            OutsourcingScheduleEntry.is_completed == False
        )
        if scenario_id is not None:
            entries_query = entries_query.filter(
                OutsourcingScheduleEntry.scenario_id == scenario_id
            )
        else:
            entries_query = entries_query.filter(
                OutsourcingScheduleEntry.scenario_id.is_(None)
            )

        process_entries = entries_query.order_by(
            OutsourcingScheduleEntry.start_time
        ).all()

        slots = [(e.start_time, e.end_time) for e in process_entries]

        for entry in process_entries:
            concurrent = count_concurrent_at_time(slots, entry.start_time)
            if concurrent >= factory.max_concurrent_jobs:
                bottlenecks.append({
                    "factory_id": factory.id,
                    "factory_name": factory.name,
                    "bottleneck_type": "concurrent_limit",
                    "step_name": entry.step_name,
                    "order_no": entry.order.order_no if entry.order else None,
                    "description": (
                        f"外协厂 '{factory.name}' 在 "
                        f"{entry.start_time.strftime('%Y-%m-%d %H:%M')} "
                        f"达到并发上限({factory.max_concurrent_jobs})，"
                        f"影响工单: {entry.order.order_no if entry.order else 'N/A'} "
                        f"工序: {entry.step_name}"
                    ),
                    "detected_at": now
                })

        daily_start = parse_time_str(factory.daily_start)
        daily_end = parse_time_str(factory.daily_end)

        for entry in process_entries:
            entry_end_t = entry.end_time.time()
            if entry.end_time.date() > entry.start_time.date():
                pass
            else:
                if entry_end_t > daily_end:
                    bottlenecks.append({
                        "factory_id": factory.id,
                        "factory_name": factory.name,
                        "bottleneck_type": "time_window",
                        "step_name": entry.step_name,
                        "order_no": entry.order.order_no if entry.order else None,
                        "description": (
                            f"外协厂 '{factory.name}' 接单时间窗为 "
                            f"{factory.daily_start}-{factory.daily_end}，"
                            f"工序 '{entry.step_name}' 结束时间 "
                            f"{entry.end_time.strftime('%H:%M')} 超出时间窗"
                        ),
                        "detected_at": now
                    })

    seen = set()
    unique_bottlenecks = []
    for b in bottlenecks:
        key = (b["factory_id"], b["bottleneck_type"], b["step_name"], b["order_no"])
        if key not in seen:
            seen.add(key)
            unique_bottlenecks.append(b)

    return unique_bottlenecks
