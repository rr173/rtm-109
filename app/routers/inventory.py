from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Material, MaterialLock, WorkOrder, ProcessStep
from app.schemas import (
    MaterialCreate, MaterialUpdate, Material as MaterialSchema,
    MaterialInventoryResponse, StockInRequest,
    OrderMaterialLocksResponse, MaterialLockDetail
)
from sqlalchemy import func

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.post("/materials", response_model=MaterialSchema, status_code=201)
def create_material(material: MaterialCreate, db: Session = Depends(get_db)):
    existing = db.query(Material).filter(Material.name == material.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Material '{material.name}' already exists")

    db_material = Material(
        name=material.name,
        unit=material.unit,
        total_quantity=material.initial_quantity,
        description=material.description
    )
    db.add(db_material)
    db.commit()
    db.refresh(db_material)
    return db_material


@router.get("/materials", response_model=List[MaterialSchema])
def list_materials(db: Session = Depends(get_db)):
    return db.query(Material).order_by(Material.id).all()


@router.get("/materials/{material_id}", response_model=MaterialSchema)
def get_material(material_id: int, db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")
    return material


@router.put("/materials/{material_id}", response_model=MaterialSchema)
def update_material(material_id: int, material: MaterialUpdate, db: Session = Depends(get_db)):
    db_material = db.query(Material).filter(Material.id == material_id).first()
    if not db_material:
        raise HTTPException(status_code=404, detail="Material not found")

    if material.name is not None:
        existing = db.query(Material).filter(
            Material.name == material.name,
            Material.id != material_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Material '{material.name}' already exists")
        db_material.name = material.name

    if material.unit is not None:
        db_material.unit = material.unit
    if material.description is not None:
        db_material.description = material.description

    db.commit()
    db.refresh(db_material)
    return db_material


@router.delete("/materials/{material_id}", status_code=204)
def delete_material(material_id: int, db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    locks = db.query(MaterialLock).filter(MaterialLock.material_id == material_id).count()
    if locks > 0:
        raise HTTPException(status_code=400, detail="无法删除：该物料存在锁定记录")

    from app.models import StepMaterialRequirement
    usages = db.query(StepMaterialRequirement).filter(
        StepMaterialRequirement.material_id == material_id
    ).count()
    if usages > 0:
        raise HTTPException(status_code=400, detail="无法删除：该物料正在被工艺路线使用")

    db.delete(material)
    db.commit()
    return None


@router.post("/materials/{material_id}/stock-in", response_model=MaterialInventoryResponse)
def stock_in(material_id: int, req: StockInRequest, db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    material.total_quantity += req.quantity
    db.commit()
    db.refresh(material)

    locked = db.query(func.coalesce(func.sum(MaterialLock.quantity), 0)).filter(
        MaterialLock.material_id == material_id
    ).scalar()

    return MaterialInventoryResponse(
        id=material.id,
        name=material.name,
        unit=material.unit,
        total_quantity=material.total_quantity,
        locked_quantity=locked,
        available_quantity=material.total_quantity - locked,
        description=material.description
    )


@router.get("/materials/{material_id}/inventory", response_model=MaterialInventoryResponse)
def get_material_inventory(material_id: int, db: Session = Depends(get_db)):
    material = db.query(Material).filter(Material.id == material_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="Material not found")

    locked = db.query(func.coalesce(func.sum(MaterialLock.quantity), 0)).filter(
        MaterialLock.material_id == material_id
    ).scalar()

    return MaterialInventoryResponse(
        id=material.id,
        name=material.name,
        unit=material.unit,
        total_quantity=material.total_quantity,
        locked_quantity=locked,
        available_quantity=material.total_quantity - locked,
        description=material.description
    )


@router.get("/inventory/all", response_model=List[MaterialInventoryResponse])
def get_all_inventory(db: Session = Depends(get_db)):
    materials = db.query(Material).order_by(Material.id).all()
    result = []
    for material in materials:
        locked = db.query(func.coalesce(func.sum(MaterialLock.quantity), 0)).filter(
            MaterialLock.material_id == material.id
        ).scalar()
        result.append(MaterialInventoryResponse(
            id=material.id,
            name=material.name,
            unit=material.unit,
            total_quantity=material.total_quantity,
            locked_quantity=locked,
            available_quantity=material.total_quantity - locked,
            description=material.description
        ))
    return result


@router.get("/order-locks/{order_id}", response_model=OrderMaterialLocksResponse)
def get_order_material_locks(order_id: int, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    locks = db.query(MaterialLock).filter(MaterialLock.order_id == order_id).all()
    lock_details = []
    total_quantity = 0

    for lock in locks:
        material = db.query(Material).filter(Material.id == lock.material_id).first()
        step = db.query(ProcessStep).filter(ProcessStep.id == lock.step_id).first()
        total_quantity += lock.quantity
        lock_details.append(MaterialLockDetail(
            id=lock.id,
            order_id=lock.order_id,
            step_id=lock.step_id,
            step_name=step.step_name if step else f"Step-{lock.step_id}",
            material_id=lock.material_id,
            material_name=material.name if material else f"Material-{lock.material_id}",
            quantity=lock.quantity,
            unit=material.unit if material else "",
            created_at=lock.created_at
        ))

    return OrderMaterialLocksResponse(
        order_id=order.id,
        order_no=order.order_no,
        locks=lock_details,
        total_locked_quantity=total_quantity
    )
