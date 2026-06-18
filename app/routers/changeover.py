from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.database import get_db
from app.models import ProductFamily as ProductFamilyModel, ChangeoverRule as ChangeoverRuleModel, Device, ProcessRoute
from app.schemas import (
    ProductFamilyCreate, ProductFamilyUpdate, ProductFamily,
    ChangeoverRuleCreate, ChangeoverRuleUpdate, ChangeoverRule,
    ChangeoverRuleListResponse
)

router = APIRouter(prefix="/changeover", tags=["changeover"])


@router.get("/product-families", response_model=List[ProductFamily])
def list_product_families(db: Session = Depends(get_db)):
    families = db.query(ProductFamilyModel).order_by(ProductFamilyModel.id).all()
    return families


@router.post("/product-families", response_model=ProductFamily, status_code=201)
def create_product_family(family: ProductFamilyCreate, db: Session = Depends(get_db)):
    existing = db.query(ProductFamilyModel).filter(ProductFamilyModel.name == family.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"产品族 '{family.name}' 已存在")
    db_family = ProductFamilyModel(name=family.name, description=family.description)
    db.add(db_family)
    db.commit()
    db.refresh(db_family)
    return db_family


@router.put("/product-families/{family_id}", response_model=ProductFamily)
def update_product_family(family_id: int, family: ProductFamilyUpdate, db: Session = Depends(get_db)):
    db_family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == family_id).first()
    if not db_family:
        raise HTTPException(status_code=404, detail="产品族不存在")
    if family.name is not None:
        existing = db.query(ProductFamilyModel).filter(ProductFamilyModel.name == family.name, ProductFamilyModel.id != family_id).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"产品族 '{family.name}' 已存在")
        db_family.name = family.name
    if family.description is not None:
        db_family.description = family.description
    db.commit()
    db.refresh(db_family)
    return db_family


@router.delete("/product-families/{family_id}", status_code=204)
def delete_product_family(family_id: int, db: Session = Depends(get_db)):
    db_family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == family_id).first()
    if not db_family:
        raise HTTPException(status_code=404, detail="产品族不存在")
    routes_with_family = db.query(ProcessRoute).filter(ProcessRoute.product_family_id == family_id).count()
    if routes_with_family > 0:
        raise HTTPException(status_code=400, detail=f"该产品族仍关联 {routes_with_family} 个工艺路线，无法删除")
    rules_with_family = db.query(ChangeoverRuleModel).filter(
        (ChangeoverRuleModel.from_product_family_id == family_id) | (ChangeoverRuleModel.to_product_family_id == family_id)
    ).count()
    if rules_with_family > 0:
        raise HTTPException(status_code=400, detail=f"该产品族仍关联 {rules_with_family} 条换型规则，无法删除")
    db.delete(db_family)
    db.commit()
    return None


@router.get("/rules", response_model=ChangeoverRuleListResponse)
def list_changeover_rules(
    device_type: Optional[str] = None,
    device_id: Optional[int] = None,
    changeover_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(ChangeoverRuleModel).options(
        joinedload(ChangeoverRuleModel.from_product_family),
        joinedload(ChangeoverRuleModel.to_product_family),
        joinedload(ChangeoverRuleModel.device)
    )
    if device_type:
        query = query.filter(ChangeoverRuleModel.device_type == device_type)
    if device_id:
        query = query.filter(ChangeoverRuleModel.device_id == device_id)
    if changeover_type:
        query = query.filter(ChangeoverRuleModel.changeover_type == changeover_type)
    rules = query.order_by(ChangeoverRuleModel.id).all()

    result = []
    for rule in rules:
        result.append(ChangeoverRule(
            id=rule.id,
            device_type=rule.device_type,
            device_id=rule.device_id,
            from_product_family_id=rule.from_product_family_id,
            to_product_family_id=rule.to_product_family_id,
            from_product_name=rule.from_product_name,
            to_product_name=rule.to_product_name,
            changeover_minutes=rule.changeover_minutes,
            changeover_type=rule.changeover_type,
            description=rule.description,
            from_product_family_name=rule.from_product_family.name if rule.from_product_family else None,
            to_product_family_name=rule.to_product_family.name if rule.to_product_family else None,
            device_name=rule.device.name if rule.device else None,
        ))

    return ChangeoverRuleListResponse(rules=result, total=len(result))


@router.post("/rules", response_model=ChangeoverRule, status_code=201)
def create_changeover_rule(rule: ChangeoverRuleCreate, db: Session = Depends(get_db)):
    if rule.device_id:
        device = db.query(Device).filter(Device.id == rule.device_id).first()
        if not device:
            raise HTTPException(status_code=400, detail=f"设备 ID {rule.device_id} 不存在")

    if rule.from_product_family_id:
        family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == rule.from_product_family_id).first()
        if not family:
            raise HTTPException(status_code=400, detail=f"产品族 ID {rule.from_product_family_id} 不存在")

    if rule.to_product_family_id:
        family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == rule.to_product_family_id).first()
        if not family:
            raise HTTPException(status_code=400, detail=f"产品族 ID {rule.to_product_family_id} 不存在")

    if rule.from_product_name and rule.to_product_name and rule.from_product_name == rule.to_product_name:
        raise HTTPException(status_code=400, detail="前后产品不能相同，同产品免换型由系统自动处理")

    has_from = (rule.from_product_name is not None and rule.from_product_name.strip() != "") or (rule.from_product_family_id is not None and rule.from_product_family_id > 0)
    has_to = (rule.to_product_name is not None and rule.to_product_name.strip() != "") or (rule.to_product_family_id is not None and rule.to_product_family_id > 0)

    if not has_from and not has_to:
        raise HTTPException(status_code=400, detail="必须至少指定来源或目标的产品名或产品族")

    db_rule = ChangeoverRuleModel(
        device_type=rule.device_type.strip() if rule.device_type else "",
        device_id=rule.device_id if rule.device_id and rule.device_id > 0 else None,
        from_product_family_id=rule.from_product_family_id if rule.from_product_family_id and rule.from_product_family_id > 0 else None,
        to_product_family_id=rule.to_product_family_id if rule.to_product_family_id and rule.to_product_family_id > 0 else None,
        from_product_name=rule.from_product_name.strip() if rule.from_product_name and rule.from_product_name.strip() else None,
        to_product_name=rule.to_product_name.strip() if rule.to_product_name and rule.to_product_name.strip() else None,
        changeover_minutes=rule.changeover_minutes,
        changeover_type=rule.changeover_type,
        description=rule.description,
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)

    return ChangeoverRule(
        id=db_rule.id,
        device_type=db_rule.device_type,
        device_id=db_rule.device_id,
        from_product_family_id=db_rule.from_product_family_id,
        to_product_family_id=db_rule.to_product_family_id,
        from_product_name=db_rule.from_product_name,
        to_product_name=db_rule.to_product_name,
        changeover_minutes=db_rule.changeover_minutes,
        changeover_type=db_rule.changeover_type,
        description=db_rule.description,
        from_product_family_name=db_rule.from_product_family.name if db_rule.from_product_family else None,
        to_product_family_name=db_rule.to_product_family.name if db_rule.to_product_family else None,
        device_name=db_rule.device.name if db_rule.device else None,
    )


@router.put("/rules/{rule_id}", response_model=ChangeoverRule)
def update_changeover_rule(rule_id: int, rule: ChangeoverRuleUpdate, db: Session = Depends(get_db)):
    db_rule = db.query(ChangeoverRuleModel).filter(ChangeoverRuleModel.id == rule_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="换型规则不存在")

    if rule.device_type is not None and rule.device_type.strip() != "":
        db_rule.device_type = rule.device_type
    if rule.device_id is not None:
        if rule.device_id > 0:
            device = db.query(Device).filter(Device.id == rule.device_id).first()
            if not device:
                raise HTTPException(status_code=400, detail=f"设备 ID {rule.device_id} 不存在")
            db_rule.device_id = rule.device_id
        else:
            db_rule.device_id = None
    if rule.from_product_family_id is not None:
        if rule.from_product_family_id > 0:
            family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == rule.from_product_family_id).first()
            if not family:
                raise HTTPException(status_code=400, detail=f"产品族 ID {rule.from_product_family_id} 不存在")
            db_rule.from_product_family_id = rule.from_product_family_id
        else:
            db_rule.from_product_family_id = None
    if rule.to_product_family_id is not None:
        if rule.to_product_family_id > 0:
            family = db.query(ProductFamilyModel).filter(ProductFamilyModel.id == rule.to_product_family_id).first()
            if not family:
                raise HTTPException(status_code=400, detail=f"产品族 ID {rule.to_product_family_id} 不存在")
            db_rule.to_product_family_id = rule.to_product_family_id
        else:
            db_rule.to_product_family_id = None
    if rule.from_product_name is not None:
        db_rule.from_product_name = rule.from_product_name.strip() if rule.from_product_name.strip() else None
    if rule.to_product_name is not None:
        db_rule.to_product_name = rule.to_product_name.strip() if rule.to_product_name.strip() else None
    if rule.changeover_minutes is not None:
        if rule.changeover_minutes < 0:
            raise HTTPException(status_code=400, detail="换型时间不能为负数")
        db_rule.changeover_minutes = rule.changeover_minutes
    if rule.changeover_type is not None and rule.changeover_type.strip() != "":
        db_rule.changeover_type = rule.changeover_type
    if rule.description is not None:
        db_rule.description = rule.description

    db.commit()
    db.refresh(db_rule)

    return ChangeoverRule(
        id=db_rule.id,
        device_type=db_rule.device_type,
        device_id=db_rule.device_id,
        from_product_family_id=db_rule.from_product_family_id,
        to_product_family_id=db_rule.to_product_family_id,
        from_product_name=db_rule.from_product_name,
        to_product_name=db_rule.to_product_name,
        changeover_minutes=db_rule.changeover_minutes,
        changeover_type=db_rule.changeover_type,
        description=db_rule.description,
        from_product_family_name=db_rule.from_product_family.name if db_rule.from_product_family else None,
        to_product_family_name=db_rule.to_product_family.name if db_rule.to_product_family else None,
        device_name=db_rule.device.name if db_rule.device else None,
    )


@router.delete("/rules/{rule_id}", status_code=204)
def delete_changeover_rule(rule_id: int, db: Session = Depends(get_db)):
    db_rule = db.query(ChangeoverRuleModel).filter(ChangeoverRuleModel.id == rule_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="换型规则不存在")
    db.delete(db_rule)
    db.commit()
    return None
