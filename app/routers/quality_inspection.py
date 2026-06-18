from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.schemas import (
    QualityInspectionReportRequest,
    QualityInspectionReportResponse,
    ReworkTaskInfo,
    QualityInspectionInfo,
    OrderReworkStatsResponse,
    ReworkScheduleRequest
)
from app.quality_inspection_service import (
    report_inspection,
    reschedule_rework,
    get_order_rework_stats,
    get_step_inspection_status
)

router = APIRouter(prefix="/quality", tags=["quality_inspection"])


@router.post("/inspection/report", response_model=QualityInspectionReportResponse)
def report_quality_inspection(request: QualityInspectionReportRequest, db: Session = Depends(get_db)):
    if not request.order_id and not request.sub_batch_id:
        raise HTTPException(status_code=400, detail="必须指定 order_id 或 sub_batch_id")

    success, result = report_inspection(
        db=db,
        order_id=request.order_id,
        sub_batch_id=request.sub_batch_id,
        step_order=request.step_order,
        conclusion=request.conclusion,
        qualified_quantity=request.qualified_quantity,
        unqualified_quantity=request.unqualified_quantity,
        inspector=request.inspector,
        notes=request.notes
    )

    if not success:
        raise HTTPException(status_code=400, detail=result.get("message", "质检上报失败"))

    return QualityInspectionReportResponse(
        success=True,
        message=result.get("message", "质检上报成功"),
        inspection_id=result.get("inspection_id"),
        conclusion=result["conclusion"],
        qualified_quantity=result["qualified_quantity"],
        unqualified_quantity=result["unqualified_quantity"],
        rework_task_created=result.get("rework_task_created", False),
        rework_task_id=result.get("rework_task_id"),
        rework_task_status=result.get("rework_task_status"),
        scrap_marked=result.get("scrap_marked", False),
        scrap_quantity=result.get("scrap_quantity", 0)
    )


@router.post("/rework/{rework_task_id}/reschedule")
def reschedule_rework_task(
    rework_task_id: int,
    request: ReworkScheduleRequest,
    db: Session = Depends(get_db)
):
    success, result = reschedule_rework(
        db=db,
        rework_task_id=rework_task_id,
        from_step_order=request.from_step_order
    )

    if not success:
        raise HTTPException(status_code=400, detail=result.get("message", "重新排产失败"))

    return result


@router.get("/orders/{order_id}/rework-stats", response_model=OrderReworkStatsResponse)
def get_rework_stats(order_id: int, db: Session = Depends(get_db)):
    stats = get_order_rework_stats(db, order_id)
    if not stats:
        raise HTTPException(status_code=404, detail="工单不存在")

    return OrderReworkStatsResponse(
        order_id=stats["order_id"],
        order_no=stats["order_no"],
        total_rework_count=stats["total_rework_count"],
        total_scrap_quantity=stats["total_scrap_quantity"],
        current_rework_in_progress=stats["current_rework_in_progress"],
        rework_tasks=[ReworkTaskInfo.from_orm(rt) for rt in stats["rework_tasks"]],
        recent_inspections=[QualityInspectionInfo.from_orm(i) for i in stats["recent_inspections"]]
    )


@router.get("/orders/{order_id}/steps/{step_order}/inspection-status")
def get_inspection_status(
    order_id: int,
    step_order: int,
    sub_batch_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    result = get_step_inspection_status(db, order_id, step_order, sub_batch_id)
    if not result:
        raise HTTPException(status_code=404, detail="未找到对应的工序信息")

    inspections_data = []
    for insp in result["inspections"]:
        inspections_data.append({
            "id": insp.id,
            "conclusion": insp.conclusion,
            "qualified_quantity": insp.qualified_quantity,
            "unqualified_quantity": insp.unqualified_quantity,
            "inspector": insp.inspector,
            "inspected_at": insp.inspected_at,
            "notes": insp.notes,
            "rework_task_id": insp.rework_task_id
        })

    rework_data = []
    for rt in result["rework_tasks"]:
        rework_data.append(ReworkTaskInfo.from_orm(rt))

    return {
        "order_id": result["order_id"],
        "step_order": result["step_order"],
        "step_name": result["step_name"],
        "requires_inspection": result["requires_inspection"],
        "inspection_status": result["inspection_status"],
        "inspections": inspections_data,
        "rework_tasks": rework_data
    }


@router.get("/rework-tasks", response_model=List[ReworkTaskInfo])
def list_rework_tasks(
    order_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from app.models import ReworkTask
    query = db.query(ReworkTask)
    if order_id:
        query = query.filter(ReworkTask.order_id == order_id)
    if status:
        query = query.filter(ReworkTask.status == status)
    tasks = query.order_by(ReworkTask.created_at.desc()).all()
    return [ReworkTaskInfo.from_orm(t) for t in tasks]


@router.get("/inspections", response_model=List[QualityInspectionInfo])
def list_inspections(
    order_id: Optional[int] = None,
    sub_batch_id: Optional[int] = None,
    conclusion: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from app.models import QualityInspection
    query = db.query(QualityInspection)
    if order_id:
        query = query.filter(QualityInspection.order_id == order_id)
    if sub_batch_id:
        query = query.filter(QualityInspection.sub_batch_id == sub_batch_id)
    if conclusion:
        query = query.filter(QualityInspection.conclusion == conclusion)
    inspections = query.order_by(QualityInspection.inspected_at.desc()).limit(100).all()
    return [QualityInspectionInfo.from_orm(i) for i in inspections]
