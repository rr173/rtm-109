from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import hashlib
import json
import threading
import traceback

from app.database import get_db
from app.models import (
    WorkOrder, ScheduleEntry, OptimizationTask, OptimizationTrajectory,
    SubBatch, SubBatchStepProgress, MaterialLock, ConflictRecord
)
from app.schemas import (
    OptimizationObjective, OptimizationTaskStatus,
    OptimizationSubmitRequest, OptimizationTaskResponse,
    OptimizationTaskDetailResponse, OptimizationTaskListResponse,
    OptimizationApplyRequest, OptimizationApplyResponse,
    OptimizationMetrics, OptimizationImprovement,
    OptimizationTrajectoryPoint
)
from app.optimization_service import (
    OptimizationSearch, SimScheduleEntry,
    register_task, mark_task_started, cancel_task,
    is_task_cancelled, get_task_remaining_seconds, cleanup_task,
    serialize_entries, deserialize_entries, entries_to_schedule_schema,
    compute_metrics, compute_objective_value
)
from app.outsourcing_service import delete_outsourcing_entries_for_order

router = APIRouter(prefix="/optimization", tags=["optimization"])


def _parse_order_ids(order_ids_str: str) -> List[int]:
    try:
        return [int(x.strip()) for x in order_ids_str.split(",") if x.strip()]
    except Exception:
        return []


def _format_order_ids(order_ids: List[int]) -> str:
    return ",".join(str(x) for x in order_ids)


def _compute_baseline_hash(db: Session, order_ids: List[int]) -> str:
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(
        ScheduleEntry.device_id, ScheduleEntry.start_time
    ).all()

    content_parts = []
    for e in entries:
        content_parts.append(
            f"{e.device_id}|{e.start_time.isoformat()}|{e.end_time.isoformat()}|{e.order_id}|{e.step_id}"
        )
    content = "||".join(content_parts)
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _task_to_response(task: OptimizationTask) -> OptimizationTaskResponse:
    order_ids = _parse_order_ids(task.order_ids)
    remaining = None
    if task.status == OptimizationTaskStatus.RUNNING:
        remaining = get_task_remaining_seconds(task.id, task.max_duration_seconds)

    return OptimizationTaskResponse(
        id=task.id,
        order_ids=order_ids,
        objective=task.objective,
        max_duration_seconds=task.max_duration_seconds,
        status=task.status,
        explored_count=task.explored_count,
        current_best_value=task.current_best_value,
        baseline_value=task.baseline_value,
        started_at=task.started_at,
        finished_at=task.finished_at,
        cancelled_at=task.cancelled_at,
        created_at=task.created_at,
        is_applied=task.is_applied,
        applied_at=task.applied_at,
        remaining_seconds=remaining,
        error_message=task.error_message
    )


def _compute_improvements(
    baseline_metrics: OptimizationMetrics,
    optimized_metrics: OptimizationMetrics
) -> List[OptimizationImprovement]:
    improvements = []

    metric_pairs = [
        ("makespan_minutes", baseline_metrics.makespan_minutes, optimized_metrics.makespan_minutes),
        ("total_changeover_minutes", baseline_metrics.total_changeover_minutes, optimized_metrics.total_changeover_minutes),
        ("total_idle_minutes", baseline_metrics.total_idle_minutes, optimized_metrics.total_idle_minutes),
    ]

    for name, base_val, opt_val in metric_pairs:
        if base_val > 0:
            improvement_pct = round((base_val - opt_val) / base_val * 100, 2)
        else:
            improvement_pct = 0.0 if opt_val == 0 else -100.0
        improvements.append(OptimizationImprovement(
            metric_name=name,
            baseline_value=base_val,
            optimized_value=opt_val,
            improvement_percent=improvement_pct
        ))

    return improvements


def _run_optimization_task(db: Session, task_id: int):
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        task = session.query(OptimizationTask).filter(OptimizationTask.id == task_id).first()
        if not task:
            return

        task.status = OptimizationTaskStatus.RUNNING
        task.started_at = datetime.now()
        mark_task_started(task_id)
        session.commit()

        order_ids = _parse_order_ids(task.order_ids)

        baseline_value_set = False

        def progress_callback(iteration: int, value: int, entries, is_best: bool):
            nonlocal baseline_value_set
            try:
                t = session.query(OptimizationTask).filter(OptimizationTask.id == task_id).first()
                if t:
                    t.explored_count += 1
                    if is_best or t.current_best_value is None or value < t.current_best_value:
                        t.current_best_value = value
                    if is_best:
                        t.result_schedule_json = serialize_entries(entries)

                    if iteration == 0 and not baseline_value_set:
                        t.baseline_value = value
                        t.baseline_schedule_json = serialize_entries(entries)
                        baseline_value_set = True

                if iteration == 0 or is_best or (iteration > 0 and iteration % 20 == 0):
                    traj = OptimizationTrajectory(
                        task_id=task_id,
                        iteration=iteration,
                        objective_value=value,
                        is_best=is_best
                    )
                    session.add(traj)

                if iteration % 10 == 0 or is_best or iteration == 0:
                    session.commit()
            except Exception:
                pass

        def cancel_check() -> bool:
            return is_task_cancelled(task_id)

        try:
            search = OptimizationSearch(
                session,
                order_ids,
                task.objective,
                task.max_duration_seconds,
                progress_callback=progress_callback,
                cancel_check=cancel_check
            )

            best_entries, baseline_entries, best_value, baseline_value = search.run()

            task.baseline_value = baseline_value
            task.baseline_schedule_json = serialize_entries(baseline_entries)
            task.result_schedule_json = serialize_entries(best_entries)
            task.current_best_value = best_value

            if is_task_cancelled(task_id):
                task.status = OptimizationTaskStatus.CANCELLED
                task.cancelled_at = datetime.now()
            else:
                task.status = OptimizationTaskStatus.COMPLETED
                task.finished_at = datetime.now()

            session.commit()

        except Exception as e:
            task.status = OptimizationTaskStatus.FAILED
            task.error_message = f"{type(e).__name__}: {str(e)}"
            task.finished_at = datetime.now()
            session.commit()
            traceback.print_exc()

    finally:
        cleanup_task(task_id)
        session.close()


@router.post("/tasks", response_model=OptimizationTaskResponse)
def submit_optimization_task(
    request: OptimizationSubmitRequest,
    db: Session = Depends(get_db)
):
    if request.objective not in OptimizationObjective.ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid objective. Must be one of: {OptimizationObjective.ALLOWED}"
        )

    if not request.order_ids:
        raise HTTPException(status_code=400, detail="order_ids cannot be empty")

    for oid in request.order_ids:
        order = db.query(WorkOrder).filter(WorkOrder.id == oid).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Work order {oid} not found")

    baseline_hash = _compute_baseline_hash(db, request.order_ids)

    task = OptimizationTask(
        order_ids=_format_order_ids(request.order_ids),
        objective=request.objective,
        max_duration_seconds=request.max_duration_seconds,
        status=OptimizationTaskStatus.PENDING,
        created_by=request.created_by,
        baseline_hash=baseline_hash,
        baseline_timestamp=datetime.now()
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    register_task(task.id)

    thread = threading.Thread(
        target=_run_optimization_task,
        args=(db, task.id),
        daemon=True
    )
    thread.start()

    return _task_to_response(task)


@router.get("/tasks", response_model=OptimizationTaskListResponse)
def list_optimization_tasks(
    status: Optional[str] = Query(None, description="Filter by task status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    query = db.query(OptimizationTask)

    if status:
        if status not in OptimizationTaskStatus.ALLOWED:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {OptimizationTaskStatus.ALLOWED}"
            )
        query = query.filter(OptimizationTask.status == status)

    tasks = query.order_by(OptimizationTask.created_at.desc()).offset(skip).limit(limit).all()
    total = query.count()

    return OptimizationTaskListResponse(
        tasks=[_task_to_response(t) for t in tasks],
        total=total
    )


@router.get("/tasks/{task_id}", response_model=OptimizationTaskDetailResponse)
def get_optimization_task_detail(
    task_id: int,
    db: Session = Depends(get_db)
):
    task = db.query(OptimizationTask).options(
        joinedload(OptimizationTask.trajectories)
    ).filter(OptimizationTask.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    order_ids = _parse_order_ids(task.order_ids)
    remaining = None
    if task.status == OptimizationTaskStatus.RUNNING:
        remaining = get_task_remaining_seconds(task.id, task.max_duration_seconds)

    result_entries = deserialize_entries(task.result_schedule_json)
    baseline_entries = deserialize_entries(task.baseline_schedule_json)

    metrics = None
    baseline_metrics = None
    improvements = []

    if result_entries:
        metrics = compute_metrics(result_entries)
    if baseline_entries:
        baseline_metrics = compute_metrics(baseline_entries)
    if metrics and baseline_metrics:
        improvements = _compute_improvements(baseline_metrics, metrics)

    trajectories = [
        OptimizationTrajectoryPoint(
            iteration=t.iteration,
            objective_value=t.objective_value,
            is_best=t.is_best,
            recorded_at=t.recorded_at
        )
        for t in sorted(task.trajectories, key=lambda x: x.iteration)
    ]

    return OptimizationTaskDetailResponse(
        id=task.id,
        order_ids=order_ids,
        objective=task.objective,
        max_duration_seconds=task.max_duration_seconds,
        status=task.status,
        explored_count=task.explored_count,
        current_best_value=task.current_best_value,
        baseline_value=task.baseline_value,
        started_at=task.started_at,
        finished_at=task.finished_at,
        cancelled_at=task.cancelled_at,
        created_at=task.created_at,
        is_applied=task.is_applied,
        applied_at=task.applied_at,
        remaining_seconds=remaining,
        error_message=task.error_message,
        result_schedule=entries_to_schedule_schema(result_entries),
        baseline_schedule=entries_to_schedule_schema(baseline_entries),
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        improvements=improvements,
        trajectories=trajectories
    )


@router.post("/tasks/{task_id}/cancel", response_model=OptimizationTaskResponse)
def cancel_optimization_task(
    task_id: int,
    operator: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    task = db.query(OptimizationTask).filter(OptimizationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in [OptimizationTaskStatus.PENDING, OptimizationTaskStatus.RUNNING]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel task in status: {task.status}"
        )

    cancelled = cancel_task(task_id)
    if cancelled:
        task.status = OptimizationTaskStatus.CANCELLED
        task.cancelled_at = datetime.now()
        task.cancelled_by = operator
        db.commit()
        db.refresh(task)

    return _task_to_response(task)


def _check_baseline_conflict(
    db: Session,
    task: OptimizationTask
) -> Tuple[bool, Optional[str]]:
    current_hash = _compute_baseline_hash(db, _parse_order_ids(task.order_ids))
    if current_hash != task.baseline_hash:
        return True, "正式排产数据已被其他操作修改，请重新运行寻优后再尝试应用"
    return False, None


@router.post("/tasks/{task_id}/apply", response_model=OptimizationApplyResponse)
def apply_optimization_result(
    task_id: int,
    request: OptimizationApplyRequest,
    db: Session = Depends(get_db)
):
    from app.scheduler import (
        schedule_order_with_split,
        release_material_locks_for_order,
        release_fixtures_for_order,
        release_sub_batches_for_order,
        reschedule_unlocked_orders
    )
    from app.staffing_service import release_employees_for_order

    task = db.query(OptimizationTask).filter(OptimizationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != OptimizationTaskStatus.COMPLETED:
        return OptimizationApplyResponse(
            success=False,
            message=f"Only completed tasks can be applied. Current status: {task.status}",
            applied=False
        )

    if task.is_applied:
        return OptimizationApplyResponse(
            success=False,
            message="This optimization result has already been applied",
            applied=False
        )

    has_conflict, conflict_reason = _check_baseline_conflict(db, task)
    if has_conflict:
        return OptimizationApplyResponse(
            success=False,
            message="Cannot apply: baseline schedule has changed",
            applied=False,
            conflict_reason=conflict_reason
        )

    result_entries = deserialize_entries(task.result_schedule_json)
    if not result_entries:
        return OptimizationApplyResponse(
            success=False,
            message="No valid schedule result to apply",
            applied=False
        )

    order_ids = _parse_order_ids(task.order_ids)

    first_start_by_order: Dict[int, datetime] = {}
    for e in result_entries:
        if e.order_id not in first_start_by_order or e.start_time < first_start_by_order[e.order_id]:
            first_start_by_order[e.order_id] = e.start_time

    ordered_order_ids = sorted(order_ids, key=lambda oid: first_start_by_order.get(oid, datetime.max))

    try:
        for oid in order_ids:
            order = db.query(WorkOrder).filter(WorkOrder.id == oid).first()
            if not order:
                continue
            if order.is_locked:
                return OptimizationApplyResponse(
                    success=False,
                    message=f"工单 {order.order_no} 已锁定，无法重新排产",
                    applied=False
                )

        from app.models import BatchDeliveryRecord
        delivered_orders = db.query(BatchDeliveryRecord).filter(
            BatchDeliveryRecord.order_id.in_(order_ids),
            BatchDeliveryRecord.scenario_id.is_(None)
        ).all()
        if delivered_orders:
            delivered_order_nos = set()
            for r in delivered_orders:
                o = db.query(WorkOrder).filter(WorkOrder.id == r.order_id).first()
                if o:
                    delivered_order_nos.add(o.order_no)
            return OptimizationApplyResponse(
                success=False,
                message=f"工单 {', '.join(delivered_order_nos)} 已有批次交付记录，无法应用优化结果",
                applied=False
            )

        for oid in order_ids:
            release_material_locks_for_order(db, oid)
            release_fixtures_for_order(db, oid)
            release_employees_for_order(db, oid)
            release_sub_batches_for_order(db, oid)
            delete_outsourcing_entries_for_order(db, oid)
            db.query(SubBatchStepProgress).filter(
                SubBatchStepProgress.sub_batch_id.in_(
                    db.query(SubBatch.id).filter(SubBatch.order_id == oid)
                )
            ).delete(synchronize_session=False)
            db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == oid,
                ScheduleEntry.is_delivered_locked == False
            ).delete(synchronize_session=False)
            db.query(SubBatch).filter(
                SubBatch.order_id == oid,
                SubBatch.delivered_quantity == 0
            ).delete(synchronize_session=False)
            db.query(ConflictRecord).filter(ConflictRecord.order_id == oid).delete(
                synchronize_session=False
            )

            order = db.query(WorkOrder).filter(WorkOrder.id == oid).first()
            if order:
                order.is_split = False
                order.total_sub_batches = 0
                order.bottleneck_step = None
                order.is_blocked = False
                order.blocked_reason = None
                order.status = "pending"

        db.flush()

        failed_order = None
        failed_message = None
        for oid in ordered_order_ids:
            order = db.query(WorkOrder).filter(WorkOrder.id == oid).first()
            if not order:
                continue

            result = schedule_order_with_split(db, order, respect_locked=True)

            if not result.get("success"):
                failed_order = order.order_no
                failed_message = result.get("message", "排产失败")
                break

        if failed_order:
            db.rollback()
            return OptimizationApplyResponse(
                success=False,
                message=f"应用优化结果时工单 '{failed_order}' 排产失败: {failed_message}。已回滚所有更改。",
                applied=False
            )

        reschedule_unlocked_orders(db)

        task.is_applied = True
        task.applied_at = datetime.now()
        task.applied_by = request.operator

        db.commit()

        return OptimizationApplyResponse(
            success=True,
            message="Optimization result applied successfully",
            applied=True
        )

    except Exception as e:
        db.rollback()
        return OptimizationApplyResponse(
            success=False,
            message=f"Failed to apply: {type(e).__name__}: {str(e)}",
            applied=False
        )
