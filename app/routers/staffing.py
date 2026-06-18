from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime, timedelta, date
from app.database import get_db
from app.models import Team, Employee, Skill, EmployeeSkill, ShiftSchedule, ScheduleEntry, Device, ProcessStep
from app.schemas import (
    TeamCreate, Team as TeamSchema, TeamUpdate,
    EmployeeCreate, Employee as EmployeeSchema, EmployeeUpdate,
    SkillCreate, Skill as SkillSchema, SkillUpdate,
    EmployeeSkillCreate, EmployeeSkill as EmployeeSkillSchema,
    ShiftScheduleCreate, ShiftSchedule as ShiftScheduleSchema,
    EmployeeTimelineEntry, TeamDailySummary, DeviceStaffingCheckResult
)
from app.staffing_service import get_available_employees_for_step

router = APIRouter(prefix="/staffing", tags=["staffing"])


@router.post("/skills/", response_model=SkillSchema, status_code=201)
def create_skill(skill: SkillCreate, db: Session = Depends(get_db)):
    existing = db.query(Skill).filter(Skill.code == skill.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Skill with code '{skill.code}' already exists")
    
    db_skill = Skill(
        code=skill.code,
        name=skill.name,
        description=skill.description,
        compatible_device_types=skill.compatible_device_types
    )
    db.add(db_skill)
    db.commit()
    db.refresh(db_skill)
    return db_skill


@router.get("/skills/", response_model=List[SkillSchema])
def list_skills(db: Session = Depends(get_db)):
    return db.query(Skill).order_by(Skill.id).all()


@router.get("/skills/{skill_id}", response_model=SkillSchema)
def get_skill(skill_id: int, db: Session = Depends(get_db)):
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/skills/{skill_id}", response_model=SkillSchema)
def update_skill(skill_id: int, skill: SkillUpdate, db: Session = Depends(get_db)):
    db_skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not db_skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    if skill.code is not None:
        existing = db.query(Skill).filter(Skill.code == skill.code, Skill.id != skill_id).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Skill with code '{skill.code}' already exists")
        db_skill.code = skill.code
    if skill.name is not None:
        db_skill.name = skill.name
    if skill.description is not None:
        db_skill.description = skill.description
    if skill.compatible_device_types is not None:
        db_skill.compatible_device_types = skill.compatible_device_types
    
    db.commit()
    db.refresh(db_skill)
    return db_skill


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: int, db: Session = Depends(get_db)):
    db_skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not db_skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    employee_skill_count = db.query(EmployeeSkill).filter(EmployeeSkill.skill_id == skill_id).count()
    if employee_skill_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete skill: {employee_skill_count} employees still have this skill")
    
    step_count = db.query(ProcessStep).filter(ProcessStep.required_skill_id == skill_id).count()
    if step_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete skill: {step_count} process steps still require this skill")
    
    db.delete(db_skill)
    db.commit()


@router.post("/teams/", response_model=TeamSchema, status_code=201)
def create_team(team: TeamCreate, db: Session = Depends(get_db)):
    existing = db.query(Team).filter(Team.name == team.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Team with name '{team.name}' already exists")
    
    db_team = Team(
        name=team.name,
        description=team.description,
        shift_type=team.shift_type
    )
    db.add(db_team)
    db.commit()
    db.refresh(db_team)
    return db_team


@router.get("/teams/", response_model=List[TeamSchema])
def list_teams(db: Session = Depends(get_db)):
    return db.query(Team).options(joinedload(Team.employees)).order_by(Team.id).all()


@router.get("/teams/{team_id}", response_model=TeamSchema)
def get_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(Team).options(joinedload(Team.employees)).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


@router.put("/teams/{team_id}", response_model=TeamSchema)
def update_team(team_id: int, team: TeamUpdate, db: Session = Depends(get_db)):
    db_team = db.query(Team).filter(Team.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if team.name is not None:
        existing = db.query(Team).filter(Team.name == team.name, Team.id != team_id).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Team with name '{team.name}' already exists")
        db_team.name = team.name
    if team.description is not None:
        db_team.description = team.description
    if team.shift_type is not None:
        db_team.shift_type = team.shift_type
    
    db.commit()
    db.refresh(db_team)
    return db_team


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(team_id: int, db: Session = Depends(get_db)):
    db_team = db.query(Team).filter(Team.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    employee_count = db.query(Employee).filter(Employee.team_id == team_id).count()
    if employee_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete team: {employee_count} employees still belong to this team")
    
    db.delete(db_team)
    db.commit()


@router.get("/teams/{team_id}/daily-summary", response_model=TeamDailySummary)
def get_team_daily_summary(
    team_id: int,
    check_date: Optional[date] = Query(None, description="查询日期，默认今日"),
    db: Session = Depends(get_db)
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    if check_date is None:
        check_date = date.today()
    
    employees = db.query(Employee).filter(Employee.team_id == team_id).all()
    
    on_duty_count = 0
    skill_coverage = {}
    
    for emp in employees:
        schedules = db.query(ShiftSchedule).filter(
            ShiftSchedule.employee_id == emp.id,
            ShiftSchedule.effective_date <= check_date,
            (ShiftSchedule.end_date.is_(None) | (ShiftSchedule.end_date >= check_date))
        ).all()
        
        weekday = check_date.weekday()
        for sched in schedules:
            day_key = f"day_{weekday}"
            shift_info = getattr(sched, day_key, None)
            if shift_info and shift_info != "off":
                on_duty_count += 1
                for es in emp.skills:
                    skill = db.query(Skill).filter(Skill.id == es.skill_id).first()
                    if skill:
                        if skill.name not in skill_coverage:
                            skill_coverage[skill.name] = {"count": 0, "max_level": 0}
                        skill_coverage[skill.name]["count"] += 1
                        if es.skill_level > skill_coverage[skill.name]["max_level"]:
                            skill_coverage[skill.name]["max_level"] = es.skill_level
                break
    
    return {
        "team_id": team_id,
        "team_name": team.name,
        "check_date": check_date,
        "total_employees": len(employees),
        "on_duty_count": on_duty_count,
        "skill_coverage": skill_coverage
    }


@router.post("/employees/", response_model=EmployeeSchema, status_code=201)
def create_employee(employee: EmployeeCreate, db: Session = Depends(get_db)):
    existing = db.query(Employee).filter(Employee.employee_no == employee.employee_no).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Employee with employee_no '{employee.employee_no}' already exists")
    
    if employee.team_id is not None:
        team = db.query(Team).filter(Team.id == employee.team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail=f"Team with id {employee.team_id} not found")
    
    db_employee = Employee(
        employee_no=employee.employee_no,
        name=employee.name,
        team_id=employee.team_id,
        phone=employee.phone,
        email=employee.email,
        status=employee.status
    )
    db.add(db_employee)
    
    if employee.skills:
        for skill_data in employee.skills:
            skill = db.query(Skill).filter(Skill.id == skill_data.skill_id).first()
            if not skill:
                db.rollback()
                raise HTTPException(status_code=404, detail=f"Skill with id {skill_data.skill_id} not found")
            if skill_data.skill_level < 1 or skill_data.skill_level > 5:
                db.rollback()
                raise HTTPException(status_code=400, detail="Skill level must be between 1 and 5")
            
            es = EmployeeSkill(
                employee_id=db_employee.id,
                skill_id=skill_data.skill_id,
                skill_level=skill_data.skill_level
            )
            db_employee.skills.append(es)
    
    db.commit()
    db.refresh(db_employee)
    return db_employee


@router.get("/employees/", response_model=List[EmployeeSchema])
def list_employees(team_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(Employee).options(joinedload(Employee.skills).joinedload(EmployeeSkill.skill))
    if team_id:
        query = query.filter(Employee.team_id == team_id)
    return query.order_by(Employee.id).all()


@router.get("/employees/{employee_id}", response_model=EmployeeSchema)
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    employee = db.query(Employee).options(
        joinedload(Employee.skills).joinedload(EmployeeSkill.skill),
        joinedload(Employee.schedules)
    ).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.get("/employees/{employee_id}/timeline", response_model=List[EmployeeTimelineEntry])
def get_employee_timeline(
    employee_id: int,
    days_ahead: int = Query(7, ge=1, le=30, description="查询未来天数"),
    db: Session = Depends(get_db)
):
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    start_date = date.today()
    end_date = start_date + timedelta(days=days_ahead)
    
    timeline = []
    
    for i in range(days_ahead):
        current_date = start_date + timedelta(days=i)
        weekday = current_date.weekday()
        
        schedules = db.query(ShiftSchedule).filter(
            ShiftSchedule.employee_id == employee_id,
            ShiftSchedule.effective_date <= current_date,
            (ShiftSchedule.end_date.is_(None) | (ShiftSchedule.end_date >= current_date))
        ).all()
        
        day_key = f"day_{weekday}"
        for sched in schedules:
            shift_info = getattr(sched, day_key, None)
            if shift_info == "off":
                timeline.append({
                    "date": current_date,
                    "type": "rest",
                    "start_time": None,
                    "end_time": None,
                    "description": "休息日"
                })
            elif shift_info:
                shift_map = {
                    "morning": ("早班", "08:00", "16:00"),
                    "middle": ("中班", "16:00", "00:00"),
                    "night": ("夜班", "00:00", "08:00")
                }
                if shift_info in shift_map:
                    desc, s_time, e_time = shift_map[shift_info]
                    timeline.append({
                        "date": current_date,
                        "type": "shift",
                        "start_time": s_time,
                        "end_time": e_time,
                        "description": desc
                    })
    
    assigned_entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.operator_id == employee_id,
        ScheduleEntry.start_time >= datetime.combine(start_date, datetime.min.time()),
        ScheduleEntry.start_time <= datetime.combine(end_date, datetime.max.time()),
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.start_time).all()
    
    for entry in assigned_entries:
        timeline.append({
            "date": entry.start_time.date(),
            "type": "assignment",
            "start_time": entry.start_time.strftime("%H:%M"),
            "end_time": entry.end_time.strftime("%H:%M"),
            "description": f"工单#{entry.order_id} - {entry.step_name}"
        })
    
    timeline.sort(key=lambda x: (x["date"], x["start_time"] or "00:00"))
    return timeline


@router.put("/employees/{employee_id}", response_model=EmployeeSchema)
def update_employee(employee_id: int, employee: EmployeeUpdate, db: Session = Depends(get_db)):
    db_employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    if employee.employee_no is not None:
        existing = db.query(Employee).filter(
            Employee.employee_no == employee.employee_no,
            Employee.id != employee_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Employee with employee_no '{employee.employee_no}' already exists")
        db_employee.employee_no = employee.employee_no
    if employee.name is not None:
        db_employee.name = employee.name
    if employee.team_id is not None:
        if employee.team_id == 0:
            db_employee.team_id = None
        else:
            team = db.query(Team).filter(Team.id == employee.team_id).first()
            if not team:
                raise HTTPException(status_code=404, detail=f"Team with id {employee.team_id} not found")
            db_employee.team_id = employee.team_id
    if employee.phone is not None:
        db_employee.phone = employee.phone
    if employee.email is not None:
        db_employee.email = employee.email
    if employee.status is not None:
        db_employee.status = employee.status
    
    db.commit()
    db.refresh(db_employee)
    return db_employee


@router.post("/employees/{employee_id}/skills/", response_model=EmployeeSkillSchema, status_code=201)
def add_employee_skill(
    employee_id: int,
    skill_data: EmployeeSkillCreate,
    db: Session = Depends(get_db)
):
    employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    skill = db.query(Skill).filter(Skill.id == skill_data.skill_id).first()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    
    existing = db.query(EmployeeSkill).filter(
        EmployeeSkill.employee_id == employee_id,
        EmployeeSkill.skill_id == skill_data.skill_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee already has this skill")
    
    if skill_data.skill_level < 1 or skill_data.skill_level > 5:
        raise HTTPException(status_code=400, detail="Skill level must be between 1 and 5")
    
    es = EmployeeSkill(
        employee_id=employee_id,
        skill_id=skill_data.skill_id,
        skill_level=skill_data.skill_level
    )
    db.add(es)
    db.commit()
    db.refresh(es)
    return es


@router.put("/employees/{employee_id}/skills/{skill_id}", response_model=EmployeeSkillSchema)
def update_employee_skill(
    employee_id: int,
    skill_id: int,
    skill_data: EmployeeSkillCreate,
    db: Session = Depends(get_db)
):
    es = db.query(EmployeeSkill).filter(
        EmployeeSkill.employee_id == employee_id,
        EmployeeSkill.skill_id == skill_id
    ).first()
    if not es:
        raise HTTPException(status_code=404, detail="Employee skill not found")
    
    if skill_data.skill_level < 1 or skill_data.skill_level > 5:
        raise HTTPException(status_code=400, detail="Skill level must be between 1 and 5")
    
    es.skill_level = skill_data.skill_level
    db.commit()
    db.refresh(es)
    return es


@router.delete("/employees/{employee_id}/skills/{skill_id}", status_code=204)
def remove_employee_skill(employee_id: int, skill_id: int, db: Session = Depends(get_db)):
    es = db.query(EmployeeSkill).filter(
        EmployeeSkill.employee_id == employee_id,
        EmployeeSkill.skill_id == skill_id
    ).first()
    if not es:
        raise HTTPException(status_code=404, detail="Employee skill not found")
    db.delete(es)
    db.commit()


@router.delete("/employees/{employee_id}", status_code=204)
def delete_employee(employee_id: int, db: Session = Depends(get_db)):
    db_employee = db.query(Employee).filter(Employee.id == employee_id).first()
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    schedule_count = db.query(ScheduleEntry).filter(
        ScheduleEntry.operator_id == employee_id,
        ScheduleEntry.scenario_id.is_(None),
        ScheduleEntry.end_time >= datetime.now()
    ).count()
    if schedule_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete employee: {schedule_count} future schedule entries exist")
    
    db.query(EmployeeSkill).filter(EmployeeSkill.employee_id == employee_id).delete()
    db.query(ShiftSchedule).filter(ShiftSchedule.employee_id == employee_id).delete()
    db.delete(db_employee)
    db.commit()


@router.post("/schedules/", response_model=ShiftScheduleSchema, status_code=201)
def create_schedule(schedule: ShiftScheduleCreate, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.id == schedule.employee_id).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    if schedule.effective_date > schedule.end_date:
        raise HTTPException(status_code=400, detail="effective_date must be <= end_date")
    
    valid_shifts = {"morning", "middle", "night", "off"}
    for day in ["day_0", "day_1", "day_2", "day_3", "day_4", "day_5", "day_6"]:
        val = getattr(schedule, day)
        if val and val not in valid_shifts:
            raise HTTPException(status_code=400, detail=f"Invalid shift type '{val}' for {day}. Must be one of: morning, middle, night, off")
    
    overlapping = db.query(ShiftSchedule).filter(
        ShiftSchedule.employee_id == schedule.employee_id,
        ShiftSchedule.id != schedule.id if hasattr(schedule, 'id') else True,
        (
            (ShiftSchedule.effective_date <= schedule.end_date) &
            ((ShiftSchedule.end_date.is_(None)) | (ShiftSchedule.end_date >= schedule.effective_date))
        )
    ).count()
    if overlapping > 0:
        raise HTTPException(status_code=400, detail="Overlapping schedule exists for this employee")
    
    db_schedule = ShiftSchedule(
        employee_id=schedule.employee_id,
        effective_date=schedule.effective_date,
        end_date=schedule.end_date,
        day_0=schedule.day_0,
        day_1=schedule.day_1,
        day_2=schedule.day_2,
        day_3=schedule.day_3,
        day_4=schedule.day_4,
        day_5=schedule.day_5,
        day_6=schedule.day_6,
        status=schedule.status,
        is_temporary=schedule.is_temporary
    )
    db.add(db_schedule)
    db.commit()
    db.refresh(db_schedule)
    return db_schedule


@router.get("/schedules/", response_model=List[ShiftScheduleSchema])
def list_schedules(
    employee_id: Optional[int] = None,
    effective_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    query = db.query(ShiftSchedule)
    if employee_id:
        query = query.filter(ShiftSchedule.employee_id == employee_id)
    if effective_date:
        query = query.filter(
            ShiftSchedule.effective_date <= effective_date,
            (ShiftSchedule.end_date.is_(None) | (ShiftSchedule.end_date >= effective_date))
        )
    return query.order_by(ShiftSchedule.effective_date.desc()).all()


@router.get("/schedules/{schedule_id}", response_model=ShiftScheduleSchema)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = db.query(ShiftSchedule).filter(ShiftSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.put("/schedules/{schedule_id}", response_model=ShiftScheduleSchema)
def update_schedule(schedule_id: int, schedule: ShiftScheduleCreate, db: Session = Depends(get_db)):
    db_schedule = db.query(ShiftSchedule).filter(ShiftSchedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    if schedule.effective_date > schedule.end_date:
        raise HTTPException(status_code=400, detail="effective_date must be <= end_date")
    
    valid_shifts = {"morning", "middle", "night", "off"}
    for day in ["day_0", "day_1", "day_2", "day_3", "day_4", "day_5", "day_6"]:
        val = getattr(schedule, day)
        if val and val not in valid_shifts:
            raise HTTPException(status_code=400, detail=f"Invalid shift type '{val}' for {day}. Must be one of: morning, middle, night, off")
    
    overlapping = db.query(ShiftSchedule).filter(
        ShiftSchedule.employee_id == schedule.employee_id,
        ShiftSchedule.id != schedule_id,
        (
            (ShiftSchedule.effective_date <= schedule.end_date) &
            ((ShiftSchedule.end_date.is_(None)) | (ShiftSchedule.end_date >= schedule.effective_date))
        )
    ).count()
    if overlapping > 0:
        raise HTTPException(status_code=400, detail="Overlapping schedule exists for this employee")
    
    db_schedule.effective_date = schedule.effective_date
    db_schedule.end_date = schedule.end_date
    db_schedule.day_0 = schedule.day_0
    db_schedule.day_1 = schedule.day_1
    db_schedule.day_2 = schedule.day_2
    db_schedule.day_3 = schedule.day_3
    db_schedule.day_4 = schedule.day_4
    db_schedule.day_5 = schedule.day_5
    db_schedule.day_6 = schedule.day_6
    db_schedule.status = schedule.status
    db_schedule.is_temporary = schedule.is_temporary
    
    db.commit()
    db.refresh(db_schedule)
    return db_schedule


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    db_schedule = db.query(ShiftSchedule).filter(ShiftSchedule.id == schedule_id).first()
    if not db_schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    db.delete(db_schedule)
    db.commit()


@router.get("/device-check/{device_id}", response_model=DeviceStaffingCheckResult)
def check_device_staffing(
    device_id: int,
    check_time: datetime,
    duration_minutes: int = Query(60, ge=1, description="检查时长(分钟)"),
    required_skill_level: Optional[int] = Query(None, ge=1, le=5, description="要求的技能等级"),
    db: Session = Depends(get_db)
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    end_time = check_time + timedelta(minutes=duration_minutes)
    
    step = ProcessStep(
        device_type=device.device_type,
        required_skill_level=required_skill_level
    )
    
    result = get_available_employees_for_step(
        db, step, device, check_time, duration_minutes
    )
    
    employee_list = []
    for emp in result.available_employees:
        skill_level = None
        for es in emp.skills:
            skill = db.query(Skill).filter(Skill.id == es.skill_id).first()
            if skill and device.device_type in (skill.compatible_device_types or ""):
                skill_level = es.skill_level
                break
        employee_list.append({
            "employee_id": emp.id,
            "employee_no": emp.employee_no,
            "name": emp.name,
            "skill_level": skill_level
        })
    
    return {
        "device_id": device_id,
        "device_name": device.name,
        "check_time": check_time,
        "end_time": end_time,
        "has_available_staff": result.has_available_staff,
        "available_count": len(employee_list),
        "available_employees": employee_list,
        "missing_skill": result.missing_skill,
        "missing_skill_level": result.missing_skill_level,
        "detail": result.detail
    }
