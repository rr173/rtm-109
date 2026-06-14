from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from app.database import get_db
from app.models import ProcessRoute, ProcessStep, StepMaterialRequirement, Material, WorkOrder, FixtureType
from app.schemas import ProcessRouteCreate, ProcessRoute as ProcessRouteSchema

router = APIRouter(prefix="/routes", tags=["routes"])


@router.post("/", response_model=ProcessRouteSchema, status_code=201)
def create_route(route: ProcessRouteCreate, db: Session = Depends(get_db)):
    existing = db.query(ProcessRoute).filter(ProcessRoute.product_name == route.product_name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Route for product '{route.product_name}' already exists")

    if not route.steps:
        raise HTTPException(status_code=400, detail="Process route must have at least one step")

    step_orders = [s.step_order for s in route.steps]
    if len(step_orders) != len(set(step_orders)):
        raise HTTPException(status_code=400, detail="Step orders must be unique")

    for step in route.steps:
        for req in step.material_requirements:
            material = db.query(Material).filter(Material.id == req.material_id).first()
            if not material:
                raise HTTPException(status_code=400, detail=f"Material with id {req.material_id} not found")
            if req.quantity <= 0:
                raise HTTPException(status_code=400, detail="Material quantity must be positive")
        
        if step.fixture_type_id is not None:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if not fixture_type:
                raise HTTPException(status_code=400, detail=f"Fixture type with id {step.fixture_type_id} not found")

    db_route = ProcessRoute(product_name=route.product_name)
    db.add(db_route)
    db.flush()

    for step in sorted(route.steps, key=lambda s: s.step_order):
        db_step = ProcessStep(
            route_id=db_route.id,
            step_order=step.step_order,
            step_name=step.step_name,
            device_type=step.device_type,
            duration_minutes=step.duration_minutes,
            min_gap_after=step.min_gap_after,
            fixture_type_id=step.fixture_type_id,
        )
        db.add(db_step)
        db.flush()

        for req in step.material_requirements:
            db_req = StepMaterialRequirement(
                step_id=db_step.id,
                material_id=req.material_id,
                quantity=req.quantity
            )
            db.add(db_req)

    db.commit()
    db.refresh(db_route)
    return db_route


def _enrich_step_with_material_names(db: Session, step):
    for req in step.material_requirements:
        material = db.query(Material).filter(Material.id == req.material_id).first()
        if material:
            req.material_name = material.name
    if step.fixture_type_id:
        fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
        if fixture_type:
            step.fixture_type_name = fixture_type.name
    return step


def _check_route_in_use(db: Session, route: ProcessRoute) -> List[str]:
    issues = []
    scheduled_orders = db.query(WorkOrder).filter(
        WorkOrder.product_name == route.product_name,
        WorkOrder.status.in_(["scheduled", "locked"]),
        WorkOrder.scenario_id.is_(None)
    ).all()
    if scheduled_orders:
        order_nos = [o.order_no for o in scheduled_orders]
        issues.append(f"存在已排产的工单: {', '.join(order_nos)}")
    return issues


@router.get("/", response_model=List[ProcessRouteSchema])
def list_routes(db: Session = Depends(get_db)):
    routes = db.query(ProcessRoute).options(
        joinedload(ProcessRoute.steps).joinedload(ProcessStep.material_requirements)
    ).order_by(ProcessRoute.id).all()
    for route in routes:
        for step in route.steps:
            _enrich_step_with_material_names(db, step)
    return routes


@router.get("/{product_name}", response_model=ProcessRouteSchema)
def get_route(product_name: str, db: Session = Depends(get_db)):
    route = db.query(ProcessRoute).options(
        joinedload(ProcessRoute.steps).joinedload(ProcessStep.material_requirements)
    ).filter(ProcessRoute.product_name == product_name).first()
    if not route:
        raise HTTPException(status_code=404, detail="Process route not found")
    for step in route.steps:
        _enrich_step_with_material_names(db, step)
    return route


@router.put("/{product_name}", response_model=ProcessRouteSchema)
def update_route(product_name: str, route: ProcessRouteCreate, db: Session = Depends(get_db)):
    db_route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not db_route:
        raise HTTPException(status_code=404, detail="Process route not found")

    issues = _check_route_in_use(db, db_route)
    if issues:
        raise HTTPException(
            status_code=400,
            detail=f"无法更新工艺路线: {'; '.join(issues)}。请先取消或删除相关工单后再修改。"
        )

    if not route.steps:
        raise HTTPException(status_code=400, detail="Process route must have at least one step")

    step_orders = [s.step_order for s in route.steps]
    if len(step_orders) != len(set(step_orders)):
        raise HTTPException(status_code=400, detail="Step orders must be unique")

    for step in route.steps:
        for req in step.material_requirements:
            material = db.query(Material).filter(Material.id == req.material_id).first()
            if not material:
                raise HTTPException(status_code=400, detail=f"Material with id {req.material_id} not found")
            if req.quantity <= 0:
                raise HTTPException(status_code=400, detail="Material quantity must be positive")
        
        if step.fixture_type_id is not None:
            fixture_type = db.query(FixtureType).filter(FixtureType.id == step.fixture_type_id).first()
            if not fixture_type:
                raise HTTPException(status_code=400, detail=f"Fixture type with id {step.fixture_type_id} not found")

    db.query(ProcessStep).filter(ProcessStep.route_id == db_route.id).delete()

    if product_name != route.product_name:
        existing = db.query(ProcessRoute).filter(ProcessRoute.product_name == route.product_name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Route for product '{route.product_name}' already exists")
        db_route.product_name = route.product_name

    for step in sorted(route.steps, key=lambda s: s.step_order):
        db_step = ProcessStep(
            route_id=db_route.id,
            step_order=step.step_order,
            step_name=step.step_name,
            device_type=step.device_type,
            duration_minutes=step.duration_minutes,
            min_gap_after=step.min_gap_after,
            fixture_type_id=step.fixture_type_id,
        )
        db.add(db_step)
        db.flush()

        for req in step.material_requirements:
            db_req = StepMaterialRequirement(
                step_id=db_step.id,
                material_id=req.material_id,
                quantity=req.quantity
            )
            db.add(db_req)

    db.commit()
    db.refresh(db_route)
    for step in db_route.steps:
        _enrich_step_with_material_names(db, step)
    return db_route


@router.delete("/{product_name}", status_code=204)
def delete_route(product_name: str, db: Session = Depends(get_db)):
    db_route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not db_route:
        raise HTTPException(status_code=404, detail="Process route not found")

    issues = _check_route_in_use(db, db_route)
    if issues:
        raise HTTPException(
            status_code=400,
            detail=f"无法删除工艺路线: {'; '.join(issues)}。请先取消或删除相关工单。"
        )

    db.delete(db_route)
    db.commit()
    return None
