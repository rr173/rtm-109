from datetime import datetime, timedelta, time, date
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, and_

from app.models import (
    Employee, EmployeeSkill, Skill, Team, ShiftSchedule,
    ScheduleEntry, ScheduleEntryEmployee, ProcessStep, Device,
    ScenarioStaffingOverride, WorkOrder
)
from app.schemas import StaffingCheckResult


SHIFT_TIMES = {
    "morning": ("08:00", "16:00"),
    "middle": ("16:00", "00:00"),
    "night": ("00:00", "08:00"),
    "off": (None, None),
}


def get_shift_times(shift_type: str) -> Tuple[Optional[str], Optional[str]]:
    return SHIFT_TIMES.get(shift_type, (None, None))


def parse_time_str(time_str: str) -> time:
    h, m = map(int, time_str.split(":"))
    return time(h, m)


def is_time_in_range(check_time: datetime, start_str: str, end_str: str) -> bool:
    start = parse_time_str(start_str)
    end = parse_time_str(end_str)
    t = check_time.time()
    
    if start <= end:
        return start <= t <= end
    else:
        return t >= start or t <= end


def is_datetime_in_range(dt: datetime, start_str: str, end_str: str, day_of_week: int) -> bool:
    if dt.weekday() != day_of_week:
        return False
    return is_time_in_range(dt, start_str, end_str)


def get_employee_skills(db: Session, employee_id: int) -> List[EmployeeSkill]:
    return db.query(EmployeeSkill).options(
        joinedload(EmployeeSkill.skill)
    ).filter(
        EmployeeSkill.employee_id == employee_id
    ).all()


def has_required_skill(
    db: Session,
    employee_id: int,
    required_skill_id: int,
    required_level: int = 1
) -> bool:
    emp_skill = db.query(EmployeeSkill).filter(
        EmployeeSkill.employee_id == employee_id,
        EmployeeSkill.skill_id == required_skill_id,
        EmployeeSkill.skill_level >= required_level
    ).first()
    return emp_skill is not None


def get_employees_with_skill(
    db: Session,
    skill_id: int,
    min_level: int = 1,
    only_active: bool = True
) -> List[Employee]:
    query = db.query(Employee).join(EmployeeSkill).filter(
        EmployeeSkill.skill_id == skill_id,
        EmployeeSkill.skill_level >= min_level
    )
    if only_active:
        query = query.filter(Employee.status == "active")
    return query.all()


def get_shift_for_employee(
    db: Session,
    employee_id: int,
    check_date: date,
    scenario_id: Optional[int] = None
) -> Optional[ShiftSchedule]:
    query = db.query(ShiftSchedule).filter(
        ShiftSchedule.employee_id == employee_id,
        ShiftSchedule.effective_date <= check_date,
        (ShiftSchedule.end_date.is_(None) | (ShiftSchedule.end_date >= check_date))
    )
    
    if scenario_id is not None:
        query = query.filter(
            or_(ShiftSchedule.scenario_id == scenario_id, ShiftSchedule.scenario_id.is_(None))
        )
    else:
        query = query.filter(ShiftSchedule.scenario_id.is_(None))
    
    query = query.order_by(ShiftSchedule.scenario_id.is_(None).asc(), ShiftSchedule.effective_date.desc())
    
    return query.first()


def get_shift_type_for_day(shift_schedule: ShiftSchedule, day_of_week: int) -> Optional[str]:
    day_field = f"day_{day_of_week}"
    return getattr(shift_schedule, day_field, None)


def is_employee_on_duty(
    db: Session,
    employee_id: int,
    check_time: datetime,
    scenario_id: Optional[int] = None
) -> Tuple[bool, Optional[ShiftSchedule]]:
    shift_schedule = get_shift_for_employee(db, employee_id, check_time.date(), scenario_id)
    if not shift_schedule:
        return False, None
    
    day_of_week = check_time.date().weekday()
    shift_type = get_shift_type_for_day(shift_schedule, day_of_week)
    
    if not shift_type or shift_type == "off":
        return False, None
    
    start_time, end_time = get_shift_times(shift_type)
    if not start_time or not end_time:
        return False, None
    
    return is_time_in_range(check_time, start_time, end_time), shift_schedule


def get_employee_occupied_slots(
    db: Session,
    employee_id: int,
    exclude_order_id: Optional[int] = None,
    scenario_id: Optional[int] = None
) -> List[Tuple[datetime, datetime, int, int]]:
    query = db.query(ScheduleEntry).join(ScheduleEntryEmployee).filter(
        ScheduleEntryEmployee.employee_id == employee_id,
        ScheduleEntry.is_completed == False
    )
    
    if scenario_id is not None:
        query = query.filter(
            or_(ScheduleEntry.scenario_id == scenario_id, ScheduleEntry.scenario_id.is_(None))
        )
    else:
        query = query.filter(ScheduleEntry.scenario_id.is_(None))
    
    if exclude_order_id is not None:
        query = query.filter(ScheduleEntry.order_id != exclude_order_id)
    
    entries = query.order_by(ScheduleEntry.start_time).all()
    slots = []
    for e in entries:
        is_locked = False
        if e.order:
            is_locked = e.order.is_locked or e.is_delivered_locked
        slots.append((e.start_time, e.end_time, e.id, is_locked))
    
    return slots


def get_available_employees_for_step(
    db: Session,
    step: ProcessStep,
    start_time: datetime,
    end_time: datetime,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    scenario_id: Optional[int] = None
) -> Tuple[List[Employee], StaffingCheckResult]:
    if step.required_skill_id is None:
        return [], StaffingCheckResult(
            has_available_staff=True,
            detail="工序无技能要求，无需检查人员"
        )
    
    required_skill = db.query(Skill).filter(Skill.id == step.required_skill_id).first()
    if not required_skill:
        return [], StaffingCheckResult(
            has_available_staff=False,
            missing_skill=f"技能ID {step.required_skill_id} 不存在",
            detail=f"工序要求的技能不存在"
        )
    
    required_level = step.required_skill_level or 1
    
    eligible_employees = get_employees_with_skill(
        db, step.required_skill_id, required_level
    )
    
    if not eligible_employees:
        return [], StaffingCheckResult(
            has_available_staff=False,
            missing_skill=required_skill.name,
            missing_skill_level=required_level,
            shortage_count=1,
            detail=f"没有具备技能 '{required_skill.name}' (等级{required_level}) 的员工"
        )
    
    available_employees = []
    on_duty_count = 0
    
    for emp in eligible_employees:
        on_duty, shift = is_employee_on_duty(db, emp.id, start_time, scenario_id)
        if not on_duty:
            continue
        
        end_on_duty, _ = is_employee_on_duty(db, emp.id, end_time, scenario_id)
        if not end_on_duty:
            continue
        
        on_duty_count += 1
        
        occupied = get_employee_occupied_slots(db, emp.id, exclude_order_id, scenario_id)
        
        if sibling_entries:
            for (emp_id, s, e) in sibling_entries:
                if emp_id == emp.id:
                    occupied.append((s, e, 0, True))
            occupied.sort(key=lambda x: x[0])
        
        has_conflict = False
        for (occ_start, occ_end, _, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if start_time < occ_end and end_time > occ_start:
                has_conflict = True
                break
        
        if not has_conflict:
            available_employees.append(emp)
    
    if not available_employees:
        if on_duty_count == 0:
            return [], StaffingCheckResult(
                has_available_staff=False,
                missing_skill=required_skill.name,
                missing_skill_level=required_level,
                shortage_count=len(eligible_employees),
                detail=f"该时段没有具备技能 '{required_skill.name}' (等级{required_level}) 的员工在岗"
            )
        else:
            return [], StaffingCheckResult(
                has_available_staff=False,
                missing_skill=required_skill.name,
                missing_skill_level=required_level,
                shortage_count=on_duty_count,
                detail=f"具备技能 '{required_skill.name}' (等级{required_level}) 的{on_duty_count}名员工均已被分配其他任务"
            )
    
    return available_employees, StaffingCheckResult(
        has_available_staff=True,
        available_employees=[emp.id for emp in available_employees],
        detail=f"找到 {len(available_employees)} 名可用员工"
    )


def find_earliest_staff_slot(
    db: Session,
    employee_id: int,
    earliest_start: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    scenario_id: Optional[int] = None
) -> Optional[datetime]:
    duration = timedelta(minutes=duration_minutes)
    current_start = earliest_start
    
    max_iterations = 365 * 24 * 60
    iterations = 0
    
    while iterations < max_iterations:
        iterations += 1
        moved = False
        
        on_duty, shift = is_employee_on_duty(db, employee_id, current_start, scenario_id)
        if not on_duty or not shift:
            current_start = _get_next_shift_start(db, employee_id, current_start, scenario_id)
            if current_start is None:
                return None
            continue
        
        shift_end = parse_time_str(shift.end_time)
        shift_end_dt = datetime.combine(current_start.date(), shift_end)
        if shift_end <= parse_time_str(shift.start_time):
            shift_end_dt += timedelta(days=1)
        
        if current_start + duration > shift_end_dt:
            current_start = _get_next_shift_start(db, employee_id, current_start, scenario_id)
            if current_start is None:
                return None
            continue
        
        occupied = get_employee_occupied_slots(db, employee_id, exclude_order_id, scenario_id)
        
        if sibling_entries:
            for (emp_id, s, e) in sibling_entries:
                if emp_id == employee_id:
                    occupied.append((s, e, 0, True))
            occupied.sort(key=lambda x: x[0])
        
        for (occ_start, occ_end, _, is_locked) in occupied:
            if respect_locked and not is_locked:
                continue
            if current_start < occ_end and current_start + duration > occ_start:
                current_start = occ_end
                moved = True
                break
        
        if moved:
            continue
        
        on_duty_at_end, _ = is_employee_on_duty(db, employee_id, current_start + duration, scenario_id)
        if not on_duty_at_end:
            current_start = _get_next_shift_start(db, employee_id, current_start, scenario_id)
            if current_start is None:
                return None
            continue
        
        return current_start
    
    return None


def _get_next_shift_start(
    db: Session,
    employee_id: int,
    from_dt: datetime,
    scenario_id: Optional[int] = None
) -> Optional[datetime]:
    for day_offset in range(14):
        check_date = from_dt.date() + timedelta(days=day_offset)
        shift_schedule = get_shift_for_employee(db, employee_id, check_date, scenario_id)
        if not shift_schedule:
            continue
        
        day_of_week = check_date.weekday()
        shift_type = get_shift_type_for_day(shift_schedule, day_of_week)
        
        if shift_type and shift_type != "off":
            start_time_str, _ = get_shift_times(shift_type)
            if not start_time_str:
                continue
            
            start_time = parse_time_str(start_time_str)
            shift_start_dt = datetime.combine(check_date, start_time)
            if shift_start_dt > from_dt:
                return shift_start_dt
            else:
                next_day = check_date + timedelta(days=1)
                return datetime.combine(next_day, start_time)
    return None


def select_best_employee(
    db: Session,
    step: ProcessStep,
    device: Device,
    start_time: datetime,
    duration_minutes: int,
    exclude_order_id: Optional[int] = None,
    respect_locked: bool = True,
    sibling_entries: Optional[List[Tuple[int, datetime, datetime]]] = None,
    order_priority: int = 5,
    scenario_id: Optional[int] = None
) -> Tuple[Optional[Employee], Optional[datetime], Optional[StaffingCheckResult]]:
    if step.required_skill_id is None:
        return None, start_time, StaffingCheckResult(
            has_available_staff=True,
            detail="工序无技能要求"
        )
    
    available_emps, check_result = get_available_employees_for_step(
        db, step, start_time, start_time + timedelta(minutes=duration_minutes),
        exclude_order_id, respect_locked, sibling_entries, scenario_id
    )
    
    if check_result.has_available_staff and available_emps:
        best_emp = min(available_emps, key=lambda e: _calculate_employee_load(db, e.id))
        return best_emp, start_time, check_result
    
    if step.required_skill_id is None:
        return None, start_time, check_result
    
    required_skill = db.query(Skill).filter(Skill.id == step.required_skill_id).first()
    if not required_skill:
        return None, None, check_result
    
    required_level = step.required_skill_level or 1
    eligible_employees = get_employees_with_skill(db, step.required_skill_id, required_level)
    
    best_start = None
    best_emp = None
    
    for emp in eligible_employees:
        emp_start = find_earliest_staff_slot(
            db, emp.id, start_time, duration_minutes,
            exclude_order_id, respect_locked, sibling_entries, scenario_id
        )
        if emp_start is not None:
            if best_start is None or emp_start < best_start:
                best_start = emp_start
                best_emp = emp
    
    if best_emp and best_start:
        return best_emp, best_start, StaffingCheckResult(
            has_available_staff=True,
            available_employees=[best_emp.id],
            detail=f"找到可用员工: {best_emp.name}"
        )
    
    return None, None, check_result


def _calculate_employee_load(db: Session, employee_id: int) -> int:
    entries = db.query(ScheduleEntry).join(ScheduleEntryEmployee).filter(
        ScheduleEntryEmployee.employee_id == employee_id,
        ScheduleEntry.is_completed == False,
        ScheduleEntry.scenario_id.is_(None)
    ).all()
    total_minutes = 0
    for e in entries:
        delta = e.end_time - e.start_time
        total_minutes += int(delta.total_seconds() / 60)
    return total_minutes


def assign_employee_to_entry(
    db: Session,
    schedule_entry_id: int,
    employee_id: int,
    assignment_type: str = "primary",
    scenario_id: Optional[int] = None
) -> ScheduleEntryEmployee:
    assignment = ScheduleEntryEmployee(
        schedule_entry_id=schedule_entry_id,
        employee_id=employee_id,
        assignment_type=assignment_type,
        scenario_id=scenario_id
    )
    db.add(assignment)
    
    entry = db.query(ScheduleEntry).filter(ScheduleEntry.id == schedule_entry_id).first()
    if entry:
        entry.operator_id = employee_id
    
    db.flush()
    return assignment


def get_employee_timeline(
    db: Session,
    employee_id: int,
    look_ahead_days: int = 7
) -> Dict:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        return {"success": False, "message": f"员工 ID {employee_id} 不存在"}
    
    now = datetime.now()
    end_time = now + timedelta(days=look_ahead_days)
    
    schedule_entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order),
        joinedload(ScheduleEntry.sub_batch),
        joinedload(ScheduleEntry.device)
    ).join(ScheduleEntryEmployee).filter(
        ScheduleEntryEmployee.employee_id == employee_id,
        ScheduleEntry.start_time < end_time,
        ScheduleEntry.end_time > now,
        ScheduleEntry.is_completed == False,
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.start_time).all()
    
    shift_schedules = []
    for day_offset in range(look_ahead_days):
        check_date = now.date() + timedelta(days=day_offset)
        shift = get_shift_for_employee(db, employee_id, check_date)
        if shift:
            shift_schedules.append(shift)
    
    days = []
    for day_offset in range(look_ahead_days):
        current_date = now.date() + timedelta(days=day_offset)
        day_start = datetime.combine(current_date, time.min)
        day_end = day_start + timedelta(days=1)
        
        entries = []
        
        shift_schedule = get_shift_for_employee(db, employee_id, current_date)
        if shift_schedule:
            day_of_week = current_date.weekday()
            shift_type = get_shift_type_for_day(shift_schedule, day_of_week)
            
            if shift_type == "off" or not shift_type:
                entries.append({
                    "type": "rest",
                    "start_time": day_start,
                    "end_time": day_end,
                    "description": "休息日",
                    "shift_type": "休息"
                })
            else:
                start_time_str, end_time_str = get_shift_times(shift_type)
                if start_time_str and end_time_str:
                    shift_start_t = parse_time_str(start_time_str)
                    shift_end_t = parse_time_str(end_time_str)
                    shift_start_dt = datetime.combine(current_date, shift_start_t)
                    shift_end_dt = datetime.combine(current_date, shift_end_t)
                    if shift_end_t <= shift_start_t:
                        shift_end_dt += timedelta(days=1)
                    
                    shift_type_cn = {"morning": "早班", "middle": "中班", "night": "夜班"}.get(shift_type, shift_type)
                    entries.append({
                        "type": "shift",
                        "start_time": max(shift_start_dt, day_start),
                        "end_time": min(shift_end_dt, day_end),
                        "description": f"上班 ({shift_type_cn})",
                        "shift_type": shift_type
                    })
        
        for entry in schedule_entries:
            entry_start = entry.start_time
            entry_end = entry.end_time
            
            if entry_end <= day_start or entry_start >= day_end:
                continue
            
            e_start = max(entry_start, day_start)
            e_end = min(entry_end, day_end)
            
            order = entry.order
            sub_batch = entry.sub_batch
            device = entry.device
            
            description = f"{order.order_no if order else '未知工单'} - {entry.step_name}"
            if sub_batch:
                description += f" ({sub_batch.batch_no})"
            
            entries.append({
                "type": "production",
                "start_time": e_start,
                "end_time": e_end,
                "description": description,
                "order_no": order.order_no if order else None,
                "sub_batch_no": sub_batch.batch_no if sub_batch else None,
                "step_name": entry.step_name,
                "device_name": device.name if device else None
            })
        
        entries.sort(key=lambda x: x["start_time"])
        days.append({
            "date": current_date.isoformat(),
            "entries": entries
        })
    
    return {
        "success": True,
        "employee_id": employee.id,
        "employee_no": employee.employee_no,
        "employee_name": employee.name,
        "days": days
    }


def get_team_daily_status(
    db: Session,
    team_id: int,
    check_date: Optional[date] = None
) -> Dict:
    if check_date is None:
        check_date = date.today()
    
    team = db.query(Team).options(
        joinedload(Team.employees).joinedload(Employee.skills).joinedload(EmployeeSkill.skill)
    ).filter(Team.id == team_id).first()
    
    if not team:
        return {"success": False, "message": f"班组 ID {team_id} 不存在"}
    
    employees = team.employees or []
    total_employees = len(employees)
    
    on_duty_count = 0
    on_rest_count = 0
    skill_coverage_map: Dict[int, Dict] = {}
    
    for emp in employees:
        if emp.status != "active":
            continue
        
        shift_schedule = get_shift_for_employee(db, emp.id, check_date)
        if not shift_schedule:
            continue
        
        day_of_week = check_date.weekday()
        shift_type = get_shift_type_for_day(shift_schedule, day_of_week)
        
        if shift_type == "off" or not shift_type:
            on_rest_count += 1
            continue
        
        on_duty_count += 1
        
        for es in emp.skills:
            skill_id = es.skill_id
            if skill_id not in skill_coverage_map:
                skill = db.query(Skill).filter(Skill.id == skill_id).first()
                if skill:
                    skill_coverage_map[skill_id] = {
                        "skill_id": skill_id,
                        "skill_name": skill.name,
                        "skill_code": skill.code,
                        "total_employees": 0,
                        "employees_by_level": {}
                    }
            
            if skill_id in skill_coverage_map:
                skill_coverage_map[skill_id]["total_employees"] += 1
                level = es.skill_level
                skill_coverage_map[skill_id]["employees_by_level"][level] = \
                    skill_coverage_map[skill_id]["employees_by_level"].get(level, 0) + 1
    
    skill_coverage = list(skill_coverage_map.values())
    
    return {
        "success": True,
        "date": check_date.isoformat(),
        "team_id": team.id,
        "team_name": team.name,
        "total_employees": total_employees,
        "on_duty_count": on_duty_count,
        "on_rest_count": on_rest_count,
        "skill_coverage": skill_coverage
    }


def check_device_staffing(
    db: Session,
    device_id: int,
    start_time: datetime,
    end_time: datetime,
    required_skill_id: Optional[int] = None,
    required_skill_level: int = 1
) -> Dict:
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return {"success": False, "message": f"设备 ID {device_id} 不存在"}
    
    if required_skill_id is None:
        return {
            "success": True,
            "device_id": device.id,
            "device_name": device.name,
            "device_type": device.device_type,
            "start_time": start_time,
            "end_time": end_time,
            "has_available_operator": True,
            "available_operators": [],
            "required_skill": None,
            "required_skill_level": None,
            "detail": "该设备操作无技能要求"
        }
    
    required_skill = db.query(Skill).filter(Skill.id == required_skill_id).first()
    if not required_skill:
        return {"success": False, "message": f"技能 ID {required_skill_id} 不存在"}
    
    eligible_employees = get_employees_with_skill(db, required_skill_id, required_skill_level)
    
    available_operators = []
    for emp in eligible_employees:
        on_duty_start, _ = is_employee_on_duty(db, emp.id, start_time)
        on_duty_end, _ = is_employee_on_duty(db, emp.id, end_time)
        
        if not on_duty_start or not on_duty_end:
            continue
        
        occupied = get_employee_occupied_slots(db, emp.id)
        has_conflict = False
        for (occ_start, occ_end, _, _) in occupied:
            if start_time < occ_end and end_time > occ_start:
                has_conflict = True
                break
        
        if not has_conflict:
            available_operators.append({
                "employee_id": emp.id,
                "employee_no": emp.employee_no,
                "employee_name": emp.name,
                "skill_level": next(
                    (es.skill_level for es in emp.skills if es.skill_id == required_skill_id),
                    None
                )
            })
    
    return {
        "success": True,
        "device_id": device.id,
        "device_name": device.name,
        "device_type": device.device_type,
        "start_time": start_time,
        "end_time": end_time,
        "has_available_operator": len(available_operators) > 0,
        "available_operators": available_operators,
        "required_skill": required_skill.name,
        "required_skill_level": required_skill_level,
        "detail": f"找到 {len(available_operators)} 名可用操作工" if available_operators else f"没有可用的{required_skill.name}操作工"
    }


def create_weekly_schedule(
    db: Session,
    employee_id: int,
    week_start_date: str,
    shift_map: Dict[int, str],
    is_temporary: bool = False
) -> ShiftSchedule:
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise ValueError(f"员工 ID {employee_id} 不存在")
    
    week_start = date.fromisoformat(week_start_date)
    if week_start.weekday() != 0:
        week_start = week_start - timedelta(days=week_start.weekday())
    
    week_end = week_start + timedelta(days=6)
    
    existing = db.query(ShiftSchedule).filter(
        ShiftSchedule.employee_id == employee_id,
        ShiftSchedule.effective_date <= week_end,
        (ShiftSchedule.end_date.is_(None) | (ShiftSchedule.end_date >= week_start)),
        ShiftSchedule.scenario_id.is_(None)
    ).all()
    for e in existing:
        db.delete(e)
    db.flush()
    
    shift_type_map = {
        "早班": "morning",
        "中班": "middle",
        "夜班": "night",
        "休息": "off"
    }
    
    schedule_data = {
        "employee_id": employee_id,
        "effective_date": week_start,
        "end_date": week_end,
        "is_temporary": is_temporary,
        "status": "active"
    }
    
    for day_of_week in range(7):
        shift_cn = shift_map.get(day_of_week, "休息")
        shift_en = shift_type_map.get(shift_cn, shift_cn)
        schedule_data[f"day_{day_of_week}"] = shift_en
    
    schedule = ShiftSchedule(**schedule_data)
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    
    return schedule


def get_scenario_staffing_overrides(
    db: Session,
    scenario_id: int
) -> List[ScenarioStaffingOverride]:
    return db.query(ScenarioStaffingOverride).filter(
        ScenarioStaffingOverride.scenario_id == scenario_id
    ).all()


def release_employees_for_entries(
    db: Session,
    schedule_entry_ids: List[int],
    scenario_id: Optional[int] = None
) -> int:
    if not schedule_entry_ids:
        return 0
    
    query = db.query(ScheduleEntryEmployee).filter(
        ScheduleEntryEmployee.scenario_id == scenario_id if scenario_id else ScheduleEntryEmployee.scenario_id.is_(None),
        ScheduleEntryEmployee.schedule_entry_id.in_(schedule_entry_ids)
    )
    entries = query.all()
    count = len(entries)
    for entry in entries:
        db.delete(entry)
    db.flush()
    
    schedule_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.id.in_(schedule_entry_ids),
        ScheduleEntry.scenario_id == scenario_id if scenario_id else ScheduleEntry.scenario_id.is_(None)
    ).all()
    for se in schedule_entries:
        se.operator_id = None
    
    db.flush()
    return count


def release_employees_for_order(
    db: Session,
    order_id: int,
    scenario_id: Optional[int] = None
) -> int:
    query = db.query(ScheduleEntryEmployee).filter(
        ScheduleEntryEmployee.scenario_id == scenario_id if scenario_id else ScheduleEntryEmployee.scenario_id.is_(None)
    ).join(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id
    )
    entries = query.all()
    count = len(entries)
    for entry in entries:
        db.delete(entry)
    db.flush()
    
    schedule_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.scenario_id == scenario_id if scenario_id else ScheduleEntry.scenario_id.is_(None)
    ).all()
    for se in schedule_entries:
        se.operator_id = None
    
    db.flush()
    return count
