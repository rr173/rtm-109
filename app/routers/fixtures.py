from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.database import get_db
from app.models import FixtureType, Fixture, WorkOrder, ScheduleEntry
from app.schemas import (
    FixtureTypeCreate, FixtureTypeUpdate, FixtureType as FixtureTypeSchema,
    FixtureCreate, FixtureUpdate, Fixture as FixtureSchema,
    FixtureTimelineResponse, FixtureDayTimeline, FixtureTimelineEntry,
    FixtureOccupancyEntry
)
from app.scheduler import (
    get_fixture_timeline,
    check_fixture_type_in_use,
    check_fixture_has_future_occupancy
)

router = APIRouter(prefix="/fixtures", tags=["fixtures"])


@router.post("/types", response_model=FixtureTypeSchema, status_code=201)
def create_fixture_type(fixture_type: FixtureTypeCreate, db: Session = Depends(get_db)):
    existing = db.query(FixtureType).filter(FixtureType.name == fixture_type.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Fixture type '{fixture_type.name}' already exists")
    
    if fixture_type.turn_over_minutes < 0:
        raise HTTPException(status_code=400, detail="turn_over_minutes must be >= 0")
    
    db_fixture_type = FixtureType(
        name=fixture_type.name,
        description=fixture_type.description,
        turn_over_minutes=fixture_type.turn_over_minutes
    )
    db.add(db_fixture_type)
    db.commit()
    db.refresh(db_fixture_type)
    return db_fixture_type


@router.get("/types", response_model=List[FixtureTypeSchema])
def list_fixture_types(db: Session = Depends(get_db)):
    fixture_types = db.query(FixtureType).order_by(FixtureType.id).all()
    return fixture_types


@router.get("/types/{fixture_type_id}", response_model=FixtureTypeSchema)
def get_fixture_type(fixture_type_id: int, db: Session = Depends(get_db)):
    fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture_type_id).first()
    if not fixture_type:
        raise HTTPException(status_code=404, detail="Fixture type not found")
    return fixture_type


@router.put("/types/{fixture_type_id}", response_model=FixtureTypeSchema)
def update_fixture_type(fixture_type_id: int, fixture_type: FixtureTypeUpdate, db: Session = Depends(get_db)):
    db_fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture_type_id).first()
    if not db_fixture_type:
        raise HTTPException(status_code=404, detail="Fixture type not found")
    
    if fixture_type.name is not None:
        existing = db.query(FixtureType).filter(
            FixtureType.name == fixture_type.name,
            FixtureType.id != fixture_type_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Fixture type '{fixture_type.name}' already exists")
        db_fixture_type.name = fixture_type.name
    
    if fixture_type.description is not None:
        db_fixture_type.description = fixture_type.description
    
    if fixture_type.turn_over_minutes is not None:
        if fixture_type.turn_over_minutes < 0:
            raise HTTPException(status_code=400, detail="turn_over_minutes must be >= 0")
        db_fixture_type.turn_over_minutes = fixture_type.turn_over_minutes
    
    db.commit()
    db.refresh(db_fixture_type)
    return db_fixture_type


@router.delete("/types/{fixture_type_id}", status_code=204)
def delete_fixture_type(fixture_type_id: int, db: Session = Depends(get_db)):
    db_fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture_type_id).first()
    if not db_fixture_type:
        raise HTTPException(status_code=404, detail="Fixture type not found")
    
    in_use, issues = check_fixture_type_in_use(db, fixture_type_id)
    if in_use:
        raise HTTPException(
            status_code=400,
            detail=f"无法删除工装类型: {'; '.join(issues)}。请先修改相关工艺路线。"
        )
    
    db.delete(db_fixture_type)
    db.commit()
    return None


@router.post("/", response_model=FixtureSchema, status_code=201)
def create_fixture(fixture: FixtureCreate, db: Session = Depends(get_db)):
    existing = db.query(Fixture).filter(Fixture.code == fixture.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Fixture with code '{fixture.code}' already exists")
    
    fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture.fixture_type_id).first()
    if not fixture_type:
        raise HTTPException(status_code=400, detail=f"Fixture type with id {fixture.fixture_type_id} not found")
    
    if not fixture.compatible_device_types.strip():
        raise HTTPException(status_code=400, detail="compatible_device_types cannot be empty")
    
    db_fixture = Fixture(
        code=fixture.code,
        fixture_type_id=fixture.fixture_type_id,
        compatible_device_types=fixture.compatible_device_types,
        status=fixture.status
    )
    db.add(db_fixture)
    db.commit()
    db.refresh(db_fixture)
    
    db_fixture.fixture_type_name = fixture_type.name
    return db_fixture


@router.get("/", response_model=List[FixtureSchema])
def list_fixtures(fixture_type_id: Optional[int] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Fixture).options(joinedload(Fixture.fixture_type))
    
    if fixture_type_id is not None:
        query = query.filter(Fixture.fixture_type_id == fixture_type_id)
    
    if status is not None:
        query = query.filter(Fixture.status == status)
    
    fixtures = query.order_by(Fixture.id).all()
    
    result = []
    for f in fixtures:
        fixture_schema = FixtureSchema.from_orm(f)
        if f.fixture_type:
            fixture_schema.fixture_type_name = f.fixture_type.name
        result.append(fixture_schema)
    
    return result


@router.get("/{fixture_id}", response_model=FixtureSchema)
def get_fixture(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.query(Fixture).options(joinedload(Fixture.fixture_type)).filter(Fixture.id == fixture_id).first()
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    
    fixture_schema = FixtureSchema.from_orm(fixture)
    if fixture.fixture_type:
        fixture_schema.fixture_type_name = fixture.fixture_type.name
    return fixture_schema


@router.put("/{fixture_id}", response_model=FixtureSchema)
def update_fixture(fixture_id: int, fixture: FixtureUpdate, db: Session = Depends(get_db)):
    db_fixture = db.query(Fixture).options(joinedload(Fixture.fixture_type)).filter(Fixture.id == fixture_id).first()
    if not db_fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    
    if fixture.code is not None:
        existing = db.query(Fixture).filter(
            Fixture.code == fixture.code,
            Fixture.id != fixture_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Fixture with code '{fixture.code}' already exists")
        db_fixture.code = fixture.code
    
    if fixture.fixture_type_id is not None:
        fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture.fixture_type_id).first()
        if not fixture_type:
            raise HTTPException(status_code=400, detail=f"Fixture type with id {fixture.fixture_type_id} not found")
        db_fixture.fixture_type_id = fixture.fixture_type_id
    
    if fixture.compatible_device_types is not None:
        if not fixture.compatible_device_types.strip():
            raise HTTPException(status_code=400, detail="compatible_device_types cannot be empty")
        db_fixture.compatible_device_types = fixture.compatible_device_types
    
    if fixture.status is not None:
        db_fixture.status = fixture.status
    
    db.commit()
    db.refresh(db_fixture)
    
    fixture_schema = FixtureSchema.from_orm(db_fixture)
    if db_fixture.fixture_type:
        fixture_schema.fixture_type_name = db_fixture.fixture_type.name
    return fixture_schema


@router.delete("/{fixture_id}", status_code=204)
def delete_fixture(fixture_id: int, db: Session = Depends(get_db)):
    db_fixture = db.query(Fixture).filter(Fixture.id == fixture_id).first()
    if not db_fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    
    has_occupancy, issues = check_fixture_has_future_occupancy(db, fixture_id)
    if has_occupancy:
        raise HTTPException(
            status_code=400,
            detail=f"无法删除工装: {'; '.join(issues)}。请先取消相关工单。"
        )
    
    db.delete(db_fixture)
    db.commit()
    return None


@router.get("/{fixture_id}/timeline", response_model=FixtureTimelineResponse)
def get_fixture_timeline_api(fixture_id: int, look_ahead_days: int = 7, db: Session = Depends(get_db)):
    if look_ahead_days < 1 or look_ahead_days > 30:
        raise HTTPException(status_code=400, detail="look_ahead_days must be between 1 and 30")
    
    result = get_fixture_timeline(db, fixture_id, look_ahead_days)
    
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    
    days = []
    for day_data in result["days"]:
        entries = []
        for entry in day_data["entries"]:
            entries.append(FixtureTimelineEntry(**entry))
        days.append(FixtureDayTimeline(
            date=day_data["date"],
            entries=entries
        ))
    
    current_occupancy = None
    if result["current_occupancy"]:
        current_occupancy = FixtureOccupancyEntry(**result["current_occupancy"])
    
    return FixtureTimelineResponse(
        fixture_id=result["fixture_id"],
        fixture_code=result["fixture_code"],
        fixture_type_name=result["fixture_type_name"],
        status=result["status"],
        current_occupancy=current_occupancy,
        days=days
    )
