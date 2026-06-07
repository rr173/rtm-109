from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import ProcessRoute, ProcessStep
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
        )
        db.add(db_step)

    db.commit()
    db.refresh(db_route)
    return db_route


@router.get("/", response_model=List[ProcessRouteSchema])
def list_routes(db: Session = Depends(get_db)):
    return db.query(ProcessRoute).order_by(ProcessRoute.id).all()


@router.get("/{product_name}", response_model=ProcessRouteSchema)
def get_route(product_name: str, db: Session = Depends(get_db)):
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not route:
        raise HTTPException(status_code=404, detail="Process route not found")
    return route


@router.put("/{product_name}", response_model=ProcessRouteSchema)
def update_route(product_name: str, route: ProcessRouteCreate, db: Session = Depends(get_db)):
    db_route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not db_route:
        raise HTTPException(status_code=404, detail="Process route not found")

    if not route.steps:
        raise HTTPException(status_code=400, detail="Process route must have at least one step")

    step_orders = [s.step_order for s in route.steps]
    if len(step_orders) != len(set(step_orders)):
        raise HTTPException(status_code=400, detail="Step orders must be unique")

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
        )
        db.add(db_step)

    db.commit()
    db.refresh(db_route)
    return db_route


@router.delete("/{product_name}", status_code=204)
def delete_route(product_name: str, db: Session = Depends(get_db)):
    db_route = db.query(ProcessRoute).filter(ProcessRoute.product_name == product_name).first()
    if not db_route:
        raise HTTPException(status_code=404, detail="Process route not found")
    db.delete(db_route)
    db.commit()
    return None
