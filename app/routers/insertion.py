from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from app.database import get_db
from app.models import WorkOrder
from app.schemas import (
    OrderInsertionRequest,
    OrderInsertionResponse,
    AffectedOrderInfo,
    InsertionHistory,
    InsertionHistoryDetail,
    InsertionHistoryListResponse,
)
from app.scheduler import (
    insert_order_with_priority,
    get_insertion_history,
    get_insertion_history_detail,
)

router = APIRouter(prefix="/insertion", tags=["insertion"])


@router.post("/orders", response_model=OrderInsertionResponse)
def insert_order(request: OrderInsertionRequest, db: Session = Depends(get_db)):
    order = db.query(WorkOrder).filter(WorkOrder.id == request.order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="工单不存在")
    
    result = insert_order_with_priority(
        db=db,
        order_id=request.order_id,
        new_priority=request.new_priority,
        operator=request.operator,
        reason=request.reason,
    )
    
    if not result["success"]:
        error_detail = result.get("message", "插单失败")
        status_code = 400
        if result.get("blocked_by_locked"):
            status_code = 409
        raise HTTPException(status_code=status_code, detail=error_detail)
    
    affected_orders = []
    for ao in result.get("affected_orders", []):
        affected_orders.append(AffectedOrderInfo(
            order_id=ao["order_id"],
            order_no=ao["order_no"],
            impact_type=ao["impact_type"],
            delay_minutes=ao.get("delay_minutes", 0),
            blocked_reason=ao.get("blocked_reason"),
            original_start_time=ao.get("original_start_time"),
            new_start_time=ao.get("new_start_time"),
        ))
    
    return OrderInsertionResponse(
        success=result["success"],
        message=result["message"],
        order_id=result.get("order_id"),
        order_no=result.get("order_no"),
        old_priority=result.get("old_priority"),
        new_priority=result.get("new_priority"),
        affected_orders=affected_orders,
        delayed_count=result.get("delayed_count", 0),
        blocked_count=result.get("blocked_count", 0),
        blocked_by_locked=result.get("blocked_by_locked"),
    )


@router.get("/history", response_model=InsertionHistoryListResponse)
def list_insertion_history(
    start_time: Optional[str] = Query(None, description="开始时间，格式：YYYY-MM-DD HH:MM:SS"),
    end_time: Optional[str] = Query(None, description="结束时间，格式：YYYY-MM-DD HH:MM:SS"),
    order_id: Optional[int] = Query(None, description="工单ID"),
    operator: Optional[str] = Query(None, description="操作人"),
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(100, ge=1, le=500, description="返回条数"),
    db: Session = Depends(get_db),
):
    start_dt = None
    end_dt = None
    
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="start_time 格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式")
    
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time)
        except ValueError:
            raise HTTPException(status_code=400, detail="end_time 格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式")
    
    histories, total = get_insertion_history(
        db=db,
        start_time=start_dt,
        end_time=end_dt,
        order_id=order_id,
        operator=operator,
        skip=skip,
        limit=limit,
    )
    
    return InsertionHistoryListResponse(
        histories=[InsertionHistory.from_orm(h) for h in histories],
        total=total,
    )


@router.get("/history/{history_id}", response_model=InsertionHistoryDetail)
def get_insertion_history_detail_api(history_id: int, db: Session = Depends(get_db)):
    detail = get_insertion_history_detail(db, history_id)
    if not detail:
        raise HTTPException(status_code=404, detail="插单历史记录不存在")
    
    affected_orders = []
    for ao in detail.get("affected_orders", []):
        affected_orders.append(AffectedOrderInfo(
            order_id=ao["order_id"],
            order_no=ao["order_no"],
            impact_type=ao["impact_type"],
            delay_minutes=ao.get("delay_minutes", 0),
            blocked_reason=ao.get("blocked_reason"),
            original_start_time=ao.get("original_start_time"),
            new_start_time=ao.get("new_start_time"),
        ))
    
    return InsertionHistoryDetail(
        id=detail["id"],
        order_id=detail["order_id"],
        order_no=detail["order_no"],
        old_priority=detail["old_priority"],
        new_priority=detail["new_priority"],
        operator=detail.get("operator"),
        reason=detail.get("reason"),
        affected_orders_count=detail["affected_orders_count"],
        delayed_orders_count=detail["delayed_orders_count"],
        blocked_orders_count=detail["blocked_orders_count"],
        created_at=detail["created_at"],
        affected_orders=affected_orders,
    )
