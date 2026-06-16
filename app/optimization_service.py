from datetime import datetime, timedelta, time, date
from typing import List, Dict, Optional, Tuple, Callable
from sqlalchemy.orm import Session, joinedload
from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, ScheduleEntry,
    MaintenancePlan, DeviceFault, FixtureType, Fixture, ProductFamily,
    ChangeoverRule, SubBatch
)
from app.schemas import (
    OptimizationObjective, OptimizationTaskStatus,
    OptimizationScheduleEntry, OptimizationMetrics
)
import random
import math
import json
import threading
import copy
from dataclasses import dataclass, field


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


def calculate_changeover_minutes_in_memory(
    db: Session,
    device_id: int,
    from_product_name: Optional[str],
    to_product_name: str
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


def get_active_device_fault(
    db: Session,
    device_id: int,
    at_time: Optional[datetime] = None
) -> Optional[DeviceFault]:
    if at_time is None:
        at_time = datetime.now()
    faults = db.query(DeviceFault).filter(
        DeviceFault.device_id == device_id,
        DeviceFault.status == "active",
        DeviceFault.expected_recovery_time > at_time
    ).all()
    if faults:
        return faults[0]
    return None


def find_next_fault_window(
    db: Session,
    device_id: int,
    from_dt: datetime
) -> Optional[Tuple[datetime, datetime, str]]:
    fault = get_active_device_fault(db, device_id, from_dt)
    if fault:
        if fault.expected_recovery_time > from_dt:
            return (fault.fault_time, fault.expected_recovery_time, fault.description or "设备故障")
    return None


@dataclass
class SimSlot:
    start: datetime
    end: datetime
    order_id: int
    product_name: str
    changeover_minutes: int = 0


@dataclass
class SimDeviceState:
    device_id: int
    device: Device
    slots: List[SimSlot] = field(default_factory=list)


@dataclass
class SimScheduleEntry:
    order_id: int
    order_no: str
    step_id: int
    step_order: int
    step_name: str
    device_id: int
    device_name: Optional[str]
    start_time: datetime
    end_time: datetime
    changeover_minutes: int = 0

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "order_no": self.order_no,
            "step_id": self.step_id,
            "step_order": self.step_order,
            "step_name": self.step_name,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "changeover_minutes": self.changeover_minutes
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            order_id=d["order_id"],
            order_no=d["order_no"],
            step_id=d["step_id"],
            step_order=d["step_order"],
            step_name=d["step_name"],
            device_id=d["device_id"],
            device_name=d.get("device_name"),
            start_time=datetime.fromisoformat(d["start_time"]),
            end_time=datetime.fromisoformat(d["end_time"]),
            changeover_minutes=d.get("changeover_minutes", 0)
        )


class InMemoryScheduler:
    def __init__(self, db: Session, order_ids: List[int]):
        self.db = db
        self.order_ids = order_ids
        self._load_data()

    def _load_data(self):
        self.orders: Dict[int, WorkOrder] = {}
        self.routes: Dict[str, ProcessRoute] = {}
        self.steps_by_route: Dict[str, List[ProcessStep]] = {}
        self.devices: Dict[int, Device] = {}
        self.devices_by_type: Dict[str, List[Device]] = {}

        orders = self.db.query(WorkOrder).filter(
            WorkOrder.id.in_(self.order_ids)
        ).options(
            joinedload(WorkOrder.sub_batches)
        ).all()
        for o in orders:
            self.orders[o.id] = o
            if o.product_name not in self.routes:
                route = self.db.query(ProcessRoute).filter(
                    ProcessRoute.product_name == o.product_name
                ).first()
                if route:
                    self.routes[o.product_name] = route
                    steps = sorted(route.steps, key=lambda s: s.step_order)
                    self.steps_by_route[o.product_name] = steps

        all_devices = self.db.query(Device).all()
        for d in all_devices:
            self.devices[d.id] = d
            if d.device_type not in self.devices_by_type:
                self.devices_by_type[d.device_type] = []
            self.devices_by_type[d.device_type].append(d)

        self._load_existing_schedule()

    def _load_existing_schedule(self):
        existing_entries = self.db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(
            ScheduleEntry.scenario_id.is_(None),
            ~ScheduleEntry.order_id.in_(self.order_ids)
        ).all()

        self.device_slots: Dict[int, List[SimSlot]] = {}
        for dev_id in self.devices:
            self.device_slots[dev_id] = []

        for entry in existing_entries:
            slot_start = entry.changeover_start_time if entry.changeover_start_time else entry.start_time
            product_name = entry.order.product_name if entry.order else ""
            if entry.device_id not in self.device_slots:
                self.device_slots[entry.device_id] = []
            self.device_slots[entry.device_id].append(SimSlot(
                start=slot_start,
                end=entry.end_time,
                order_id=entry.order_id,
                product_name=product_name,
                changeover_minutes=entry.changeover_minutes or 0
            ))

        for dev_id in self.device_slots:
            self.device_slots[dev_id].sort(key=lambda s: s.start)

    def _find_earliest_slot_for_device(
        self,
        device_id: int,
        earliest_start: datetime,
        duration_minutes: int,
        product_name: str,
        temp_slots: Dict[int, List[SimSlot]]
    ) -> Optional[Tuple[datetime, int]]:
        device = self.devices[device_id]
        duration = timedelta(minutes=duration_minutes)
        current_start = get_next_working_start(earliest_start, device)

        all_slots = list(self.device_slots.get(device_id, []))
        if device_id in temp_slots:
            all_slots.extend(temp_slots[device_id])
        all_slots.sort(key=lambda s: s.start)

        max_iterations = 365 * 24 * 60
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            moved = False

            changeover_minutes = 0
            prev_product = None
            for slot in reversed(all_slots):
                if slot.end <= current_start:
                    prev_product = slot.product_name
                    break
            if prev_product:
                changeover_minutes, _ = calculate_changeover_minutes_in_memory(
                    self.db, device_id, prev_product, product_name
                )

            total_duration = timedelta(minutes=changeover_minutes + duration_minutes)

            day_end = calculate_available_end(current_start, device)
            if current_start + total_duration > day_end:
                next_day = current_start.date() + timedelta(days=1)
                current_start = datetime.combine(next_day, parse_time_str(device.daily_start))
                continue

            for slot in all_slots:
                if current_start < slot.end and current_start + total_duration > slot.start:
                    current_start = slot.end
                    moved = True
                    break

            if moved:
                current_start = get_next_working_start(current_start, device)
                continue

            next_maint = find_next_maintenance_window(self.db, device_id, current_start)
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

            next_fault = find_next_fault_window(self.db, device_id, current_start)
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

            return current_start, changeover_minutes

        return None

    def schedule_order_sequence(
        self,
        order_sequence: List[int]
    ) -> Tuple[bool, List[SimScheduleEntry]]:
        entries: List[SimScheduleEntry] = []
        temp_slots: Dict[int, List[SimSlot]] = {}
        prev_step_end_by_order: Dict[int, datetime] = {}

        for order_id in order_sequence:
            if order_id not in self.orders:
                continue
            order = self.orders[order_id]
            if order.product_name not in self.steps_by_route:
                continue
            steps = self.steps_by_route[order.product_name]
            prev_end_time = order.expected_start_time

            for step in steps:
                earliest_start = prev_end_time
                if step.step_order > 1 and steps[step.step_order - 2].min_gap_after > 0:
                    earliest_start = prev_end_time + timedelta(
                        minutes=steps[step.step_order - 2].min_gap_after
                    )

                if step.device_type not in self.devices_by_type:
                    return False, []

                best_start = None
                best_device_id = None
                best_changeover = 0

                for device in self.devices_by_type[step.device_type]:
                    if get_active_device_fault(self.db, device.id, earliest_start):
                        continue

                    result = self._find_earliest_slot_for_device(
                        device.id, earliest_start, step.duration_minutes,
                        order.product_name, temp_slots
                    )
                    if result is not None:
                        slot_start, ch_minutes = result
                        if best_start is None or slot_start < best_start:
                            best_start = slot_start
                            best_device_id = device.id
                            best_changeover = ch_minutes

                if best_start is None or best_device_id is None:
                    return False, []

                actual_start = best_start + timedelta(minutes=best_changeover)
                end_time = actual_start + timedelta(minutes=step.duration_minutes)

                if end_time > order.deadline:
                    return False, []

                device = self.devices[best_device_id]
                entries.append(SimScheduleEntry(
                    order_id=order.id,
                    order_no=order.order_no,
                    step_id=step.id,
                    step_order=step.step_order,
                    step_name=step.step_name,
                    device_id=device.id,
                    device_name=device.name,
                    start_time=actual_start,
                    end_time=end_time,
                    changeover_minutes=best_changeover
                ))

                slot_start = best_start
                if best_device_id not in temp_slots:
                    temp_slots[best_device_id] = []
                temp_slots[best_device_id].append(SimSlot(
                    start=slot_start,
                    end=end_time,
                    order_id=order.id,
                    product_name=order.product_name,
                    changeover_minutes=best_changeover
                ))

                prev_end_time = end_time

        return True, entries


def compute_metrics(entries: List[SimScheduleEntry]) -> OptimizationMetrics:
    if not entries:
        return OptimizationMetrics(
            makespan_minutes=0,
            total_changeover_minutes=0,
            total_idle_minutes=0,
            avg_device_utilization=0.0
        )

    all_starts = [e.start_time for e in entries]
    all_ends = [e.end_time for e in entries]
    makespan = int((max(all_ends) - min(all_starts)).total_seconds() / 60)

    total_changeover = sum(e.changeover_minutes for e in entries)

    device_entries: Dict[int, List[SimScheduleEntry]] = {}
    for e in entries:
        if e.device_id not in device_entries:
            device_entries[e.device_id] = []
        device_entries[e.device_id].append(e)

    total_idle = 0
    total_available = 0
    total_utilization = 0.0
    device_count = 0

    for dev_id, dev_entries in device_entries.items():
        dev_entries.sort(key=lambda e: e.start_time)
        dev_start = min(e.start_time for e in dev_entries)
        dev_end = max(e.end_time for e in dev_entries)
        dev_total_minutes = int((dev_end - dev_start).total_seconds() / 60)
        dev_work_minutes = sum(
            int((e.end_time - e.start_time).total_seconds() / 60)
            for e in dev_entries
        )
        dev_idle = dev_total_minutes - dev_work_minutes
        if dev_idle < 0:
            dev_idle = 0
        total_idle += dev_idle
        total_available += dev_total_minutes
        if dev_total_minutes > 0:
            total_utilization += dev_work_minutes / dev_total_minutes
        device_count += 1

    avg_utilization = total_utilization / device_count if device_count > 0 else 0.0

    return OptimizationMetrics(
        makespan_minutes=makespan,
        total_changeover_minutes=total_changeover,
        total_idle_minutes=total_idle,
        avg_device_utilization=avg_utilization
    )


def compute_objective_value(
    metrics: OptimizationMetrics,
    objective: str
) -> int:
    if objective == OptimizationObjective.MIN_MAKESPAN:
        return metrics.makespan_minutes
    elif objective == OptimizationObjective.MIN_CHANGEOVER:
        return metrics.total_changeover_minutes
    elif objective == OptimizationObjective.MIN_IDLE:
        return metrics.total_idle_minutes
    else:
        return metrics.makespan_minutes


class OptimizationSearch:
    def __init__(
        self,
        db: Session,
        order_ids: List[int],
        objective: str,
        max_duration_seconds: int,
        progress_callback: Optional[Callable[[int, int, List[SimScheduleEntry], bool], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ):
        self.db = db
        self.order_ids = order_ids
        self.objective = objective
        self.max_duration_seconds = max_duration_seconds
        self.progress_callback = progress_callback
        self.cancel_check = cancel_check

        self.scheduler = InMemoryScheduler(db, order_ids)
        self.explored_count = 0
        self.best_value: Optional[int] = None
        self.best_entries: Optional[List[SimScheduleEntry]] = None
        self.baseline_entries: Optional[List[SimScheduleEntry]] = None
        self.baseline_value: Optional[int] = None

    def _get_greedy_sequence(self) -> List[int]:
        orders = [self.scheduler.orders[oid] for oid in self.order_ids if oid in self.scheduler.orders]
        orders.sort(key=lambda o: (-o.priority, o.expected_start_time, o.id))
        return [o.id for o in orders]

    def _get_random_sequence(self) -> List[int]:
        seq = list(self.order_ids)
        random.shuffle(seq)
        return seq

    def _mutate_sequence(self, seq: List[int]) -> List[int]:
        new_seq = seq.copy()
        if len(new_seq) < 2:
            return new_seq

        strategy = random.choice(["swap", "insert", "invert"])

        if strategy == "swap":
            i, j = random.sample(range(len(new_seq)), 2)
            new_seq[i], new_seq[j] = new_seq[j], new_seq[i]
        elif strategy == "insert":
            i, j = random.sample(range(len(new_seq)), 2)
            if i < j:
                elem = new_seq.pop(i)
                new_seq.insert(j - 1, elem)
            else:
                elem = new_seq.pop(i)
                new_seq.insert(j, elem)
        elif strategy == "invert":
            i, j = sorted(random.sample(range(len(new_seq)), 2))
            new_seq[i:j + 1] = reversed(new_seq[i:j + 1])

        return new_seq

    def _crossover_sequences(self, seq1: List[int], seq2: List[int]) -> List[int]:
        if len(seq1) < 3:
            return seq1.copy()

        n = len(seq1)
        start, end = sorted(random.sample(range(n), 2))

        child = [0] * n
        child[start:end + 1] = seq1[start:end + 1]

        used = set(child[start:end + 1])
        pos = 0
        for gene in seq2:
            if gene not in used:
                while pos < n and child[pos] != 0:
                    pos += 1
                if pos < n:
                    child[pos] = gene
                    used.add(gene)

        return child

    def _evaluate_sequence(self, seq: List[int]) -> Tuple[bool, int, List[SimScheduleEntry]]:
        success, entries = self.scheduler.schedule_order_sequence(seq)
        if not success:
            return False, float('inf'), []
        metrics = compute_metrics(entries)
        value = compute_objective_value(metrics, self.objective)
        return True, value, entries

    def _report_progress(self, iteration: int, value: int, entries: List[SimScheduleEntry], is_best: bool):
        self.explored_count += 1
        if self.progress_callback:
            try:
                self.progress_callback(iteration, value, entries, is_best)
            except Exception:
                pass

    def run(self) -> Tuple[List[SimScheduleEntry], List[SimScheduleEntry], int, int]:
        import time as time_module

        start_time = time_module.time()
        deadline = start_time + self.max_duration_seconds

        greedy_seq = self._get_greedy_sequence()
        success, baseline_value, baseline_entries = self._evaluate_sequence(greedy_seq)
        if not success:
            raise Exception("贪心基线排产失败，请检查工单数据")

        self.baseline_value = baseline_value
        self.baseline_entries = baseline_entries
        self.best_value = baseline_value
        self.best_entries = baseline_entries

        self._report_progress(0, baseline_value, baseline_entries, True)

        population_size = min(50, max(10, len(self.order_ids) * 3))
        population: List[Tuple[int, List[int], List[SimScheduleEntry]]] = []

        population.append((baseline_value, greedy_seq, baseline_entries))

        for _ in range(population_size - 1):
            if self.cancel_check and self.cancel_check():
                break
            if time_module.time() > deadline:
                break

            seq = self._get_random_sequence()
            success, val, entries = self._evaluate_sequence(seq)
            if success and val != float('inf'):
                population.append((val, seq, entries))
                self._report_progress(self.explored_count, val, entries, val < self.best_value)

                if val < self.best_value:
                    self.best_value = val
                    self.best_entries = entries

        population.sort(key=lambda x: x[0])
        if len(population) > population_size:
            population = population[:population_size]

        iteration = len(population)
        no_improve_count = 0

        while time_module.time() < deadline:
            if self.cancel_check and self.cancel_check():
                break

            if no_improve_count > 200 and len(population) > 1:
                population = population[:max(5, len(population) // 2)]
                while len(population) < population_size:
                    seq = self._get_random_sequence()
                    success, val, entries = self._evaluate_sequence(seq)
                    if success and val != float('inf'):
                        population.append((val, seq, entries))
                        self._report_progress(iteration, val, entries, val < self.best_value)
                        iteration += 1
                        if val < self.best_value:
                            self.best_value = val
                            self.best_entries = entries
                            no_improve_count = 0
                population.sort(key=lambda x: x[0])
                no_improve_count = 0

            parent_idx1 = random.randint(0, min(len(population) - 1, 5))
            parent_idx2 = random.randint(0, min(len(population) - 1, 10))
            if parent_idx1 == parent_idx2:
                parent_idx2 = (parent_idx1 + 1) % len(population)

            _, p1_seq, _ = population[parent_idx1]
            _, p2_seq, _ = population[parent_idx2]

            child_seq = self._crossover_sequences(p1_seq, p2_seq)

            if random.random() < 0.6:
                child_seq = self._mutate_sequence(child_seq)
            if random.random() < 0.3:
                child_seq = self._mutate_sequence(child_seq)

            success, child_val, child_entries = self._evaluate_sequence(child_seq)
            iteration += 1

            if success and child_val != float('inf'):
                self._report_progress(iteration, child_val, child_entries, child_val < self.best_value)

                if child_val < self.best_value:
                    self.best_value = child_val
                    self.best_entries = child_entries
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                if len(population) < population_size or child_val < population[-1][0]:
                    population.append((child_val, child_seq, child_entries))
                    population.sort(key=lambda x: x[0])
                    if len(population) > population_size:
                        population.pop()
            else:
                no_improve_count += 1

            if self.explored_count % 50 == 0 and self.progress_callback:
                self._report_progress(iteration, self.best_value, self.best_entries or [], False)

        return self.best_entries or [], self.baseline_entries or [], self.best_value or 0, self.baseline_value or 0


_optimization_tasks: Dict[int, Dict] = {}
_tasks_lock = threading.Lock()


def register_task(task_id: int):
    with _tasks_lock:
        _optimization_tasks[task_id] = {
            "cancelled": False,
            "start_time": None
        }


def mark_task_started(task_id: int):
    with _tasks_lock:
        if task_id in _optimization_tasks:
            _optimization_tasks[task_id]["start_time"] = datetime.now()


def cancel_task(task_id: int) -> bool:
    with _tasks_lock:
        if task_id in _optimization_tasks:
            _optimization_tasks[task_id]["cancelled"] = True
            return True
    return False


def is_task_cancelled(task_id: int) -> bool:
    with _tasks_lock:
        if task_id in _optimization_tasks:
            return _optimization_tasks[task_id]["cancelled"]
    return False


def get_task_remaining_seconds(task_id: int, max_duration_seconds: int) -> Optional[int]:
    with _tasks_lock:
        if task_id in _optimization_tasks and _optimization_tasks[task_id]["start_time"]:
            elapsed = (datetime.now() - _optimization_tasks[task_id]["start_time"]).total_seconds()
            remaining = max(0, int(max_duration_seconds - elapsed))
            return remaining
    return None


def cleanup_task(task_id: int):
    with _tasks_lock:
        if task_id in _optimization_tasks:
            del _optimization_tasks[task_id]


def serialize_entries(entries: List[SimScheduleEntry]) -> str:
    return json.dumps([e.to_dict() for e in entries], ensure_ascii=False)


def deserialize_entries(json_str: Optional[str]) -> List[SimScheduleEntry]:
    if not json_str:
        return []
    try:
        data = json.loads(json_str)
        return [SimScheduleEntry.from_dict(d) for d in data]
    except Exception:
        return []


def entries_to_schedule_schema(entries: List[SimScheduleEntry]) -> List[OptimizationScheduleEntry]:
    return [
        OptimizationScheduleEntry(
            order_id=e.order_id,
            order_no=e.order_no,
            step_id=e.step_id,
            step_order=e.step_order,
            step_name=e.step_name,
            device_id=e.device_id,
            device_name=e.device_name,
            start_time=e.start_time,
            end_time=e.end_time,
            changeover_minutes=e.changeover_minutes
        )
        for e in entries
    ]
