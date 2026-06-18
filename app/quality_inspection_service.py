from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.models import (
    WorkOrder, SubBatch, SubBatchStepProgress, ProcessStep,
    ProcessRoute, QualityInspection, ReworkTask, ScheduleEntry,
    ConflictRecord, MaterialLock
)
from app.scheduler import (
    get_route_steps_for_order, check_materials_for_steps,
    lock_materials_for_order, release_material_locks_for_order,
    release_fixtures_for_order, _schedule_single_sub_batch
)
from app.staffing_service import release_employees_for_order
from app.outsourcing_service import delete_outsourcing_entries_for_order

MAX_REWORK_COUNT = 3


def report_inspection(
    db: Session,
    order_id: Optional[int],
    sub_batch_id: Optional[int],
    step_order: int,
    conclusion: str,
    qualified_quantity: int,
    unqualified_quantity: int,
    inspector: Optional[str] = None,
    notes: Optional[str] = None
) -> Tuple[bool, Dict]:
    if not order_id and not sub_batch_id:
        return False, {"message": "必须指定 order_id 或 sub_batch_id"}

    if conclusion not in ("qualified", "unqualified"):
        return False, {"message": "质检结论必须是 qualified 或 unqualified"}

    if conclusion == "unqualified" and unqualified_quantity <= 0:
        return False, {"message": "不合格时必须提供不合格数量"}

    if sub_batch_id:
        sub_batch = db.query(SubBatch).filter(SubBatch.id == sub_batch_id).first()
        if not sub_batch:
            return False, {"message": f"子批次 {sub_batch_id} 不存在"}
        order_id = sub_batch.order_id
    else:
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        if not order:
            return False, {"message": f"工单 {order_id} 不存在"}
        if order.is_split and order.sub_batches:
            return False, {"message": "工单已拆分，请指定 sub_batch_id"}
        sub_batch = None
        if order.sub_batches:
            sub_batch = order.sub_batches[0]
        else:
            return False, {"message": "工单没有子批次，请先上报工序进度"}

    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return False, {"message": f"工单 {order_id} 不存在"}

    steps = get_route_steps_for_order(db, order)
    if not steps:
        return False, {"message": "产品没有工艺路线"}

    step = next((s for s in steps if s.step_order == step_order), None)
    if not step:
        return False, {"message": f"工序 {step_order} 不存在"}

    if not step.requires_inspection:
        return False, {"message": f"工序 {step_order} 不需要质检"}

    progress_query = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.step_order == step_order
    )
    if sub_batch:
        progress_query = progress_query.filter(
            SubBatchStepProgress.sub_batch_id == sub_batch.id
        )
    progress = progress_query.first()

    if not progress:
        return False, {"message": f"工序 {step_order} 尚未上报完工进度，无法质检"}

    if progress.inspection_status == "qualified":
        return False, {"message": f"工序 {step_order} 已质检合格，不能重复质检"}

    if progress.inspection_status != "pending_inspection" and progress.inspection_status != "unqualified":
        return False, {"message": f"工序 {step_order} 当前状态为 {progress.inspection_status}，无法质检"}

    existing_rework_task = None
    if progress.inspection_status == "unqualified":
        existing_inspections = db.query(QualityInspection).filter(
            QualityInspection.order_id == order_id,
            QualityInspection.step_order == step_order,
            QualityInspection.conclusion == "unqualified"
        ).order_by(QualityInspection.created_at.desc()).all()
        if existing_inspections:
            latest_insp = existing_inspections[0]
            if latest_insp.rework_task_id:
                existing_rework_task = db.query(ReworkTask).filter(
                    ReworkTask.id == latest_insp.rework_task_id
                ).first()

    inspection = QualityInspection(
        order_id=order.id,
        sub_batch_id=sub_batch.id if sub_batch else None,
        step_order=step_order,
        step_id=step.id,
        conclusion=conclusion,
        qualified_quantity=qualified_quantity,
        unqualified_quantity=unqualified_quantity,
        inspector=inspector,
        inspected_at=datetime.utcnow(),
        notes=notes
    )
    db.add(inspection)
    db.flush()

    result = {
        "inspection_id": inspection.id,
        "conclusion": conclusion,
        "qualified_quantity": qualified_quantity,
        "unqualified_quantity": unqualified_quantity,
        "rework_task_created": False,
        "rework_task_id": None,
        "rework_task_status": None,
        "scrap_marked": False,
        "scrap_quantity": 0
    }

    if conclusion == "qualified":
        progress.inspection_status = "qualified"
        progress.good_quantity = qualified_quantity
        progress.scrap_quantity = unqualified_quantity
        progress.is_completed = True
        progress.actual_completion_time = progress.actual_completion_time or datetime.utcnow()

        schedule_entry = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order.id,
            ScheduleEntry.step_order == step_order
        )
        if sub_batch:
            schedule_entry = schedule_entry.filter(
                ScheduleEntry.sub_batch_id == sub_batch.id
            )
        se = schedule_entry.first()
        if se:
            se.is_completed = True
            se.actual_completion_time = progress.actual_completion_time

        if unqualified_quantity > 0:
            _create_replenishment_for_scrap(db, order, sub_batch, step_order, unqualified_quantity, steps)

        db.flush()
        _check_and_complete_sub_batch(db, order, sub_batch, steps)
        db.commit()
        return True, result

    progress.inspection_status = "unqualified"
    progress.good_quantity = qualified_quantity
    progress.scrap_quantity = 0
    progress.is_completed = False
    db.flush()

    rework_count = 1
    if existing_rework_task:
        rework_count = existing_rework_task.rework_count + 1

    if rework_count > MAX_REWORK_COUNT:
        scrap_qty = unqualified_quantity
        progress.inspection_status = "scrapped"
        progress.scrap_quantity = scrap_qty
        progress.is_completed = True
        db.flush()

        scrap_rework_task = ReworkTask(
            order_id=order.id,
            sub_batch_id=sub_batch.id if sub_batch else None,
            step_order=step_order,
            from_step_order=step_order,
            quantity=scrap_qty,
            rework_count=rework_count,
            status="scrapped",
            is_blocked=False,
            scrap_reason=f"返工次数超过{MAX_REWORK_COUNT}次上限，自动标记报废",
            completed_at=datetime.utcnow()
        )
        db.add(scrap_rework_task)
        db.flush()

        inspection.rework_task_id = scrap_rework_task.id
        db.flush()

        _create_replenishment_for_scrap(db, order, sub_batch, step_order, scrap_qty, steps)
        _check_and_complete_sub_batch(db, order, sub_batch, steps)

        result["scrap_marked"] = True
        result["scrap_quantity"] = scrap_qty
        result["rework_task_id"] = scrap_rework_task.id
        result["rework_task_status"] = "scrapped"
        db.commit()
        return True, result

    rework_task = ReworkTask(
        order_id=order.id,
        sub_batch_id=sub_batch.id if sub_batch else None,
        parent_rework_task_id=existing_rework_task.id if existing_rework_task else None,
        step_order=step_order,
        from_step_order=step_order,
        quantity=unqualified_quantity,
        rework_count=rework_count,
        status="pending"
    )
    db.add(rework_task)
    db.flush()

    inspection.rework_task_id = rework_task.id
    db.flush()

    schedule_success, schedule_msg = _schedule_rework(db, order, rework_task, steps)

    result["rework_task_created"] = True
    result["rework_task_id"] = rework_task.id
    result["rework_task_status"] = rework_task.status

    db.commit()
    return True, result


def _schedule_rework(
    db: Session,
    order: WorkOrder,
    rework_task: ReworkTask,
    steps: List[ProcessStep],
    from_step_override: Optional[int] = None
) -> Tuple[bool, Optional[str]]:
    from_step = from_step_override if from_step_override is not None else rework_task.from_step_order
    rework_task.from_step_order = from_step

    remaining_steps = [s for s in steps if s.step_order >= from_step]
    if not remaining_steps:
        rework_task.status = "failed"
        rework_task.is_blocked = True
        rework_task.blocked_reason = f"找不到从工序 {from_step} 开始的后续工序"
        db.flush()
        return False, rework_task.blocked_reason

    materials_ok, material_shortages = check_materials_for_steps(db, remaining_steps, multiplier=1)
    if not materials_ok:
        shortage_descs = [
            f"{s['material_name']}: 需要{s['needed']}, 可用{s['available']}, 缺{s['shortage']}"
            for s in material_shortages
        ]
        rework_task.status = "blocked"
        rework_task.is_blocked = True
        rework_task.blocked_reason = f"返工件物料不足: {'; '.join(shortage_descs)}"
        db.flush()
        return False, rework_task.blocked_reason

    batch_no = f"RW-{order.order_no}-{rework_task.id}"
    rework_sub_batch = SubBatch(
        order_id=order.id,
        batch_no=batch_no,
        quantity=rework_task.quantity,
        status="pending",
        is_replenishment=True,
        replenish_level=rework_task.rework_count,
        replenish_from_step=from_step
    )
    db.add(rework_sub_batch)
    db.flush()

    for step in steps:
        if step.step_order < from_step:
            pseudo_progress = SubBatchStepProgress(
                sub_batch_id=rework_sub_batch.id,
                step_order=step.step_order,
                step_name=step.step_name,
                step_id=step.id,
                good_quantity=rework_task.quantity,
                scrap_quantity=0,
                is_completed=True,
                actual_completion_time=datetime.utcnow(),
                inspection_status="not_required"
            )
            db.add(pseudo_progress)
    db.flush()

    sibling_device_entries = []
    sibling_fixture_entries = []
    sibling_outsourcing_entries = []
    sibling_employee_entries = []

    success, entries, bn_step, bn_type, bn_fixture, outsourcing_results, bn_skill, bn_skill_level = _schedule_single_sub_batch(
        db, order, rework_sub_batch, steps,
        respect_locked=True,
        sibling_device_entries=sibling_device_entries,
        sibling_fixture_entries=sibling_fixture_entries,
        sibling_outsourcing_entries=sibling_outsourcing_entries,
        sibling_employee_entries=sibling_employee_entries
    )

    if not success:
        db.delete(rework_sub_batch)
        db.flush()

        error_msg = f"返工排产失败，工序 '{bn_step}' 无法安排"
        if bn_type == "fixture":
            error_msg += f": 工装不足(类型: {bn_fixture})"
        elif bn_type == "device":
            error_msg += ": 设备产能不足"
        elif bn_type == "staff":
            skill_info = ""
            if bn_skill:
                skill_info = f"技能: {bn_skill}"
                if bn_skill_level:
                    skill_info += f", 等级要求: L{bn_skill_level}"
            error_msg += f": 人员不足({skill_info})"
        elif bn_type == "deadline":
            error_msg += ": 无法在截止时间前完成返工"

        rework_task.status = "blocked"
        rework_task.is_blocked = True
        rework_task.blocked_reason = error_msg
        db.flush()
        return False, error_msg

    for entry in entries:
        db_entry = ScheduleEntry(
            order_id=order.id,
            sub_batch_id=rework_sub_batch.id,
            step_id=entry["step_id"],
            device_id=entry["device_id"],
            fixture_id=entry["fixture_id"],
            operator_id=entry.get("operator_id"),
            step_order=entry["step_order"],
            step_name=entry["step_name"],
            start_time=entry["start_time"],
            end_time=entry["end_time"],
            fixture_turn_over_end_time=entry.get("fixture_turn_over_end_time"),
            changeover_start_time=entry.get("changeover_start_time"),
            changeover_end_time=entry.get("changeover_end_time"),
            changeover_minutes=entry.get("changeover_minutes", 0),
            changeover_type=entry.get("changeover_type"),
            prev_product_name=entry.get("prev_product_name"),
        )
        db.add(db_entry)

        if entry.get("operator_id"):
            from app.staffing_service import assign_employee_to_entry
            assign_employee_to_entry(
                db, entry["operator_id"], db_entry.id,
                entry["start_time"], entry["end_time"]
            )

    from app.outsourcing_service import create_outsourcing_schedule_entries
    for or_result in outsourcing_results:
        create_outsourcing_schedule_entries(
            db, order, rework_sub_batch,
            or_result["step"], or_result["factory"],
            or_result["nodes"], rework_task.quantity
        )

    lock_materials_for_order(db, order.id, remaining_steps, multiplier=1)

    rework_sub_batch.status = "scheduled"
    first_start = min(e["start_time"] for e in entries) if entries else None
    last_end = max(e["end_time"] for e in entries) if entries else None
    if outsourcing_results:
        for or_result in outsourcing_results:
            for node in or_result["nodes"]:
                if first_start is None or node["start_time"] < first_start:
                    first_start = node["start_time"]
                if last_end is None or node["end_time"] > last_end:
                    last_end = node["end_time"]
    rework_sub_batch.actual_start_time = first_start
    rework_sub_batch.actual_end_time = last_end

    rework_task.rework_sub_batch_id = rework_sub_batch.id
    rework_task.status = "scheduled"
    rework_task.is_blocked = False
    rework_task.blocked_reason = None
    order.total_sub_batches += 1
    db.flush()

    return True, None


def reschedule_rework(
    db: Session,
    rework_task_id: int,
    from_step_order: Optional[int] = None
) -> Tuple[bool, Dict]:
    rework_task = db.query(ReworkTask).filter(ReworkTask.id == rework_task_id).first()
    if not rework_task:
        return False, {"message": f"返工任务 {rework_task_id} 不存在"}

    if rework_task.status not in ("pending", "blocked"):
        return False, {"message": f"返工任务状态为 {rework_task.status}，无法重新排产"}

    order = db.query(WorkOrder).filter(WorkOrder.id == rework_task.order_id).first()
    if not order:
        return False, {"message": "工单不存在"}

    steps = get_route_steps_for_order(db, order)
    if not steps:
        return False, {"message": "产品没有工艺路线"}

    if from_step_order is not None:
        if from_step_order < 1 or from_step_order > len(steps):
            return False, {"message": f"退回工序序号必须在 1 到 {len(steps)} 之间"}
        if from_step_order > rework_task.step_order:
            return False, {"message": f"退回工序序号 {from_step_order} 不能大于质检工序 {rework_task.step_order}"}
        rework_task.from_step_order = from_step_order

    if rework_task.rework_sub_batch_id:
        _release_rework_sub_batch(db, rework_task.rework_sub_batch_id, order.id)
        rework_task.rework_sub_batch_id = None

    rework_task.status = "pending"
    rework_task.is_blocked = False
    rework_task.blocked_reason = None
    db.flush()

    success, error_msg = _schedule_rework(db, order, rework_task, steps)

    db.commit()
    if success:
        return True, {
            "message": "返工任务重新排产成功",
            "rework_task_id": rework_task.id,
            "status": rework_task.status
        }
    else:
        return True, {
            "message": f"返工任务重新排产受阻: {error_msg}",
            "rework_task_id": rework_task.id,
            "status": rework_task.status,
            "is_blocked": rework_task.is_blocked,
            "blocked_reason": rework_task.blocked_reason
        }


def _release_rework_sub_batch(db: Session, rework_sub_batch_id: int, order_id: int):
    rework_sb = db.query(SubBatch).filter(SubBatch.id == rework_sub_batch_id).first()
    if not rework_sb:
        return

    db.query(ScheduleEntry).filter(
        ScheduleEntry.sub_batch_id == rework_sub_batch_id
    ).delete(synchronize_session=False)

    from app.outsourcing_service import delete_outsourcing_entries_for_sub_batch
    delete_outsourcing_entries_for_sub_batch(db, rework_sub_batch_id)

    db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == rework_sub_batch_id
    ).delete(synchronize_session=False)

    db.delete(rework_sb)
    db.flush()

    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if order and order.total_sub_batches > 0:
        order.total_sub_batches -= 1


def _create_replenishment_for_scrap(
    db: Session,
    order: WorkOrder,
    sub_batch: Optional[SubBatch],
    step_order: int,
    scrap_quantity: int,
    all_steps: List[ProcessStep]
):
    if scrap_quantity <= 0:
        return

    if sub_batch and sub_batch.replenish_level >= 3:
        return

    from app.scheduler import create_replenishment_sub_batch
    try:
        create_replenishment_sub_batch(db, sub_batch, step_order, scrap_quantity, all_steps)
    except Exception:
        pass


def _check_and_complete_sub_batch(
    db: Session,
    order: WorkOrder,
    sub_batch: Optional[SubBatch],
    steps: List[ProcessStep]
):
    if not sub_batch:
        return

    progresses = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sub_batch.id
    ).all()

    all_done = True
    for step in steps:
        if step.step_order < (sub_batch.replenish_from_step or 1):
            continue
        p = next((pr for pr in progresses if pr.step_order == step.step_order), None)
        if not p or not p.is_completed:
            all_done = False
            break
        if step.requires_inspection and p.inspection_status not in ("qualified", "scrapped", "not_required"):
            all_done = False
            break

    if all_done:
        sub_batch.status = "completed"
        sub_batch.actual_end_time = datetime.utcnow()
        db.flush()

        all_sub_completed = all(
            sb.status == "completed" for sb in order.sub_batches
            if not sb.is_replenishment or sb.status != "cancelled"
        )
        if all_sub_completed and order.sub_batches:
            all_progresses = db.query(SubBatchStepProgress).filter(
                SubBatchStepProgress.sub_batch_id.in_(
                    [sb.id for sb in order.sub_batches]
                ),
                SubBatchStepProgress.is_completed == False
            ).count()
            if all_progresses == 0:
                order.status = "completed"
                db.flush()


def get_order_rework_stats(db: Session, order_id: int) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    rework_tasks = db.query(ReworkTask).filter(
        ReworkTask.order_id == order_id
    ).order_by(ReworkTask.created_at.desc()).all()

    total_rework_count = sum(rt.rework_count for rt in rework_tasks)
    total_scrap_quantity = sum(
        rt.quantity for rt in rework_tasks if rt.status == "scrapped"
    )
    current_rework_in_progress = sum(
        1 for rt in rework_tasks
        if rt.status in ("pending", "scheduled", "blocked")
    )

    inspections = db.query(QualityInspection).filter(
        QualityInspection.order_id == order_id
    ).order_by(QualityInspection.inspected_at.desc()).limit(20).all()

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "total_rework_count": total_rework_count,
        "total_scrap_quantity": total_scrap_quantity,
        "current_rework_in_progress": current_rework_in_progress,
        "rework_tasks": rework_tasks,
        "recent_inspections": inspections
    }


def release_rework_tasks_for_order(db: Session, order_id: int) -> int:
    rework_tasks = db.query(ReworkTask).filter(
        ReworkTask.order_id == order_id,
        ReworkTask.status.in_(["pending", "scheduled", "blocked"])
    ).all()

    count = 0
    for rt in rework_tasks:
        if rt.rework_sub_batch_id:
            _release_rework_sub_batch(db, rt.rework_sub_batch_id, order_id)
            rt.rework_sub_batch_id = None
        rt.status = "cancelled"
        rt.is_blocked = False
        rt.blocked_reason = None
        count += 1

    db.flush()
    return count


def get_step_inspection_status(
    db: Session,
    order_id: int,
    step_order: int,
    sub_batch_id: Optional[int] = None
) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    steps = get_route_steps_for_order(db, order)
    step = next((s for s in steps if s.step_order == step_order), None)
    if not step:
        return None

    query = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.step_order == step_order
    )
    if sub_batch_id:
        query = query.filter(SubBatchStepProgress.sub_batch_id == sub_batch_id)
    progress = query.first()

    inspections = db.query(QualityInspection).filter(
        QualityInspection.order_id == order_id,
        QualityInspection.step_order == step_order
    )
    if sub_batch_id:
        inspections = inspections.filter(QualityInspection.sub_batch_id == sub_batch_id)
    inspections = inspections.order_by(QualityInspection.inspected_at.desc()).all()

    rework_tasks = db.query(ReworkTask).filter(
        ReworkTask.order_id == order_id,
        ReworkTask.step_order == step_order
    ).order_by(ReworkTask.created_at.desc()).all()

    return {
        "order_id": order_id,
        "step_order": step_order,
        "step_name": step.step_name,
        "requires_inspection": step.requires_inspection,
        "inspection_status": progress.inspection_status if progress else "not_required",
        "inspections": inspections,
        "rework_tasks": rework_tasks
    }
