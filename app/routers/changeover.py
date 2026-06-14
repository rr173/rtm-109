from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.models import ProductFamily, ChangeoverRule, ScenarioChangeoverOverride, ProcessRoute
from app.schemas import (
    ProductFamilyCreate, ProductFamilyUpdate, ProductFamily,
    ChangeoverRuleCreate, ChangeoverRuleUpdate, ChangeoverRule,
    ScenarioChangeoverOverrideCreate, ScenarioChangeoverOverrideUpdate,
    ScenarioChangeoverOverride,
)

router = APIRouter(prefix="/changeover", tags=["changeover"])


@router.get("/product-families", response_model=List[ProductFamily])
def list_product_families(db: Session = Depends(get_db)):
    return db.query(ProductFamily).order_by(ProductFamily.id).all()


@router.post("/product-families", response_model=ProductFamily, status_code=201)
def create_product_family(data: ProductFamilyCreate, db: Session = Depends(get_db)):
    existing = db.query(ProductFamily).filter(ProductFamily.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"产品族 '{data.name}' 已存在")
    obj = ProductFamily(name=data.name, description=data.description)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/product-families/{family_id}", response_model=ProductFamily)
def update_product_family(family_id: int, data: ProductFamilyUpdate, db: Session = Depends(get_db)):
    obj = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="产品族不存在")
    if data.name is not None:
        obj.name = data.name
    if data.description is not None:
        obj.description = data.description
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/product-families/{family_id}")
def delete_product_family(family_id: int, db: Session = Depends(get_db)):
    obj = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="产品族不存在")
    linked_routes = db.query(ProcessRoute).filter(ProcessRoute.product_family_id == family_id).count()
    if linked_routes > 0:
        raise HTTPException(status_code=400, detail=f"该产品族下有 {linked_routes} 条工艺路线关联，无法删除")
    linked_rules = db.query(ChangeoverRule).filter(
        (ChangeoverRule.from_product_family_id == family_id) | (ChangeoverRule.to_product_family_id == family_id)
    ).count()
    if linked_rules > 0:
        raise HTTPException(status_code=400, detail=f"该产品族有 {linked_rules} 条换型规则关联，无法删除")
    db.delete(obj)
    db.commit()
    return {"message": "产品族已删除"}


@router.get("/rules", response_model=List[ChangeoverRule])
def list_changeover_rules(
    device_id: Optional[int] = Query(None),
    device_type: Optional[str] = Query(None),
    changeover_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(ChangeoverRule)
    if device_id is not None:
        query = query.filter(ChangeoverRule.device_id == device_id)
    if device_type is not None:
        query = query.filter(ChangeoverRule.device_type == device_type)
    if changeover_type is not None:
        query = query.filter(ChangeoverRule.changeover_type == changeover_type)
    return query.order_by(ChangeoverRule.priority.desc(), ChangeoverRule.id).all()


@router.post("/rules", response_model=ChangeoverRule, status_code=201)
def create_changeover_rule(data: ChangeoverRuleCreate, db: Session = Depends(get_db)):
    if data.from_product_family_id is not None:
        family = db.query(ProductFamily).filter(ProductFamily.id == data.from_product_family_id).first()
        if not family:
            raise HTTPException(status_code=400, detail="源产品族不存在")
    if data.to_product_family_id is not None:
        family = db.query(ProductFamily).filter(ProductFamily.id == data.to_product_family_id).first()
        if not family:
            raise HTTPException(status_code=400, detail="目标产品族不存在")
    if data.device_id is not None:
        from app.models import Device
        device = db.query(Device).filter(Device.id == data.device_id).first()
        if not device:
            raise HTTPException(status_code=400, detail="设备不存在")
    obj = ChangeoverRule(
        device_id=data.device_id,
        device_type=data.device_type,
        from_product_family_id=data.from_product_family_id,
        to_product_family_id=data.to_product_family_id,
        from_product_name=data.from_product_name,
        to_product_name=data.to_product_name,
        changeover_type=data.changeover_type,
        changeover_minutes=data.changeover_minutes,
        same_product_minutes=data.same_product_minutes,
        same_family_minutes=data.same_family_minutes,
        cross_family_minutes=data.cross_family_minutes,
        priority=data.priority,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/rules/{rule_id}", response_model=ChangeoverRule)
def update_changeover_rule(rule_id: int, data: ChangeoverRuleUpdate, db: Session = Depends(get_db)):
    obj = db.query(ChangeoverRule).filter(ChangeoverRule.id == rule_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="换型规则不存在")
    for field, value in data.dict(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/rules/{rule_id}")
def delete_changeover_rule(rule_id: int, db: Session = Depends(get_db)):
    obj = db.query(ChangeoverRule).filter(ChangeoverRule.id == rule_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="换型规则不存在")
    db.delete(obj)
    db.commit()
    return {"message": "换型规则已删除"}


@router.get("/scenarios/{scenario_id}/overrides", response_model=List[ScenarioChangeoverOverride])
def list_scenario_changeover_overrides(scenario_id: int, db: Session = Depends(get_db)):
    return db.query(ScenarioChangeoverOverride).filter(
        ScenarioChangeoverOverride.scenario_id == scenario_id
    ).order_by(ScenarioChangeoverOverride.id).all()


@router.post("/scenarios/{scenario_id}/overrides", response_model=ScenarioChangeoverOverride, status_code=201)
def create_scenario_changeover_override(
    scenario_id: int, data: ScenarioChangeoverOverrideCreate, db: Session = Depends(get_db)
):
    from app.models import Scenario
    scenario = db.query(Scenario).filter(Scenario.id == scenario_id).first()
    if not scenario:
        raise HTTPException(status_code=404, detail="预案不存在")
    obj = ScenarioChangeoverOverride(
        scenario_id=scenario_id,
        changeover_rule_id=data.changeover_rule_id,
        device_id=data.device_id,
        device_type=data.device_type,
        from_product_name=data.from_product_name,
        to_product_name=data.to_product_name,
        override_type=data.override_type,
        changeover_minutes=data.changeover_minutes,
        new_changeover_minutes=data.new_changeover_minutes,
        reason=data.reason,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/scenarios/{scenario_id}/overrides/{override_id}", response_model=ScenarioChangeoverOverride)
def update_scenario_changeover_override(
    scenario_id: int, override_id: int,
    data: ScenarioChangeoverOverrideUpdate, db: Session = Depends(get_db)
):
    obj = db.query(ScenarioChangeoverOverride).filter(
        ScenarioChangeoverOverride.id == override_id,
        ScenarioChangeoverOverride.scenario_id == scenario_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="预案换型覆盖规则不存在")
    for field, value in data.dict(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/scenarios/{scenario_id}/overrides/{override_id}")
def delete_scenario_changeover_override(
    scenario_id: int, override_id: int, db: Session = Depends(get_db)
):
    obj = db.query(ScenarioChangeoverOverride).filter(
        ScenarioChangeoverOverride.id == override_id,
        ScenarioChangeoverOverride.scenario_id == scenario_id
    ).first()
    if not obj:
        raise HTTPException(status_code=404, detail="预案换型覆盖规则不存在")
    db.delete(obj)
    db.commit()
    return {"message": "预案换型覆盖规则已删除"}
