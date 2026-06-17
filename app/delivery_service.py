from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

from app.models import (
    WorkOrder, SubBatch, ScheduleEntry, DeliveryPlan,
    BatchDeliveryRecord, MaterialLock, ConflictRecord,
    SubBatchStepProgress, ProcessRoute
)


def set_delivery_plan(
    db: Session,
    order_id: int,
    plans: List[Dict]
) -> Tuple[bool, Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return False, {"message": f"工单 {order_id} 不存在"}

    existing_delivered = db.query(BatchDeliveryRecord).filter(
        BatchDeliveryRecord.order_id == order_id,
        BatchDeliveryRecord.scenario_id.is_(None)
    ).count()
    if existing_delivered > 0:
        return False, {"message": "已有交付记录，不能修改交付计划"}

    total_planned = sum(p["planned_quantity"] for p in plans)
    if total_planned > order.total_quantity:
        return False, {
            "message": f"交付计划总数量({total_planned})超过工单总数量({order.total_quantity})"
        }

    indices = [p["plan_index"] for p in plans]
    if len(indices) != len(set(indices)):
        return False, {"message": "交付计划序号不能重复"}
    if sorted(indices) != list(range(1, len(indices) + 1)):
        return False, {"message": "交付计划序号必须从1开始且连续"}

    db.query(DeliveryPlan).filter(
        DeliveryPlan.order_id == order_id,
        DeliveryPlan.scenario_id.is_(None)
    ).delete(synchronize_session=False)
    db.flush()

    created_plans = []
    for p in plans:
        dp = DeliveryPlan(
            order_id=order_id,
            plan_index=p["plan_index"],
            planned_quantity=p["planned_quantity"],
            expected_delivery_date=p["expected_delivery_date"],
            status="pending"
        )
        db.add(dp)
        db.flush()
        created_plans.append(dp)

    if order.status in ["scheduled", "in_progress", "completed"]:
        has_split = order.is_split and len(order.sub_batches) > 0
        if has_split:
            for sb in order.sub_batches:
                sb.delivery_plan_id = None
            db.flush()

            plan_idx_map = {dp.plan_index: dp.id for dp in created_plans}
            remaining_plan_qty = {dp.id: dp.planned_quantity for dp in created_plans}

            sorted_subs = sorted(
                [sb for sb in order.sub_batches if not sb.is_replenishment],
                key=lambda x: (x.actual_start_time or datetime.max)
            )

            for sb in sorted_subs:
                for plan in created_plans:
                    if remaining_plan_qty[plan.id] > 0:
                        assign_qty = min(sb.quantity, remaining_plan_qty[plan.id])
                        if assign_qty > 0:
                            sb.delivery_plan_id = plan.id
                            remaining_plan_qty[plan.id] -= assign_qty
                        break

    db.commit()
    db.commit()

    return True, {
        "message": f"成功设置{len(created_plans)}批交付计划",
        "order_id": order_id,
        "order_no": order.order_no,
        "plans_count": len(created_plans),
        "total_planned_quantity": total_planned
    }


def get_delivery_plans(db: Session, order_id: int) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    plans = db.query(DeliveryPlan).options(
        joinedload(DeliveryPlan.sub_batches),
        joinedload(DeliveryPlan.delivery_records)
    ).filter(
        DeliveryPlan.order_id == order_id,
        DeliveryPlan.scenario_id.is_(None)
    ).order_by(DeliveryPlan.plan_index).all()

    result_plans = []
    total_planned = 0
    total_delivered = 0

    for plan in plans:
        delivered_qty = sum(
            r.actual_quantity for r in plan.delivery_records
            if r.status in ["delivered", "accepted"]
        )
        sub_batch_ids = [sb.id for sb in plan.sub_batches]

        estimated_completion = None
        end_times = [sb.actual_end_time for sb in plan.sub_batches if sb.actual_end_time]
        if end_times:
            estimated_completion = max(end_times)

        if delivered_qty >= plan.planned_quantity and plan.planned_quantity > 0:
            plan_status = "delivered"
        elif delivered_qty > 0:
            plan_status = "partially_delivered"
        else:
            plan_status = plan.status

        result_plans.append({
            "id": plan.id,
            "order_id": plan.order_id,
            "plan_index": plan.plan_index,
            "planned_quantity": plan.planned_quantity,
            "expected_delivery_date": plan.expected_delivery_date,
            "status": plan_status,
            "actual_delivered_quantity": delivered_qty,
            "sub_batch_ids": sub_batch_ids,
            "estimated_completion_time": estimated_completion
        })

        total_planned += plan.planned_quantity
        total_delivered += delivered_qty

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "total_quantity": order.total_quantity,
        "total_planned_quantity": total_planned,
        "total_delivered_quantity": total_delivered,
        "plans": result_plans
    }


def execute_batch_delivery(
    db: Session,
    delivery_plan_id: int,
    actual_quantity: int,
    delivered_at: Optional[datetime] = None,
    accepted_by: Optional[str] = None,
    remarks: Optional[str] = None
) -> Tuple[bool, Dict]:
    plan = db.query(DeliveryPlan).options(
        joinedload(DeliveryPlan.order),
        joinedload(DeliveryPlan.sub_batches),
        joinedload(DeliveryPlan.delivery_records)
    ).filter(DeliveryPlan.id == delivery_plan_id).first()

    if not plan:
        return False, {"message": f"交付计划 {delivery_plan_id} 不存在"}

    order = plan.order
    if not order:
        return False, {"message": "关联工单不存在"}

    already_delivered = sum(
        r.actual_quantity for r in plan.delivery_records
        if r.status in ["delivered", "accepted"]
    )
    remaining = plan.planned_quantity - already_delivered
    if remaining <= 0:
        return False, {"message": f"交付计划第{plan.plan_index}批已全部交付完毕"}

    completed_sbs = []
    good_qty_total = 0
    for sb in plan.sub_batches:
        if sb.status == "completed":
            completed_sbs.append(sb)
            last_progress = db.query(SubBatchStepProgress).filter(
                SubBatchStepProgress.sub_batch_id == sb.id
            ).order_by(SubBatchStepProgress.step_order.desc()).first()
            if last_progress:
                good_qty_total += last_progress.good_quantity
            else:
                good_qty_total += sb.quantity

    available_good_qty = good_qty_total - already_delivered
    if actual_quantity > available_good_qty:
        return False, {
            "message": f"实际交付数量({actual_quantity})超过可用良品数({available_good_qty})"
        }
    if actual_quantity > remaining:
        return False, {
            "message": f"实际交付数量({actual_quantity})超过本批剩余待交付量({remaining})"
        }

    if delivered_at is None:
        delivered_at = datetime.utcnow()

    status = "delivered"
    accepted_at = None
    if accepted_by:
        status = "accepted"
        accepted_at = delivered_at

    record = BatchDeliveryRecord(
        order_id=order.id,
        delivery_plan_id=plan.id,
        actual_quantity=actual_quantity,
        delivered_at=delivered_at,
        accepted_by=accepted_by,
        accepted_at=accepted_at,
        status=status,
        remarks=remarks
    )
    db.add(record)
    db.flush()

    for sb in plan.sub_batches:
        if sb.status == "completed" or sb.status == "in_progress":
            for se in sb.schedule_entries:
                if not se.is_delivered_locked:
                    se.is_delivered_locked = True

    consumed_locks = 0
    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order.product_name).first()
    if route:
        total_units = order.total_quantity
        steps = sorted(route.steps, key=lambda s: s.step_order)
        if total_units > 0:
            for step in steps:
                per_unit_mat = {}
                for mr in step.material_requirements:
                    per_unit_mat[mr.material_id] = mr.quantity

                for mat_id, per_unit in per_unit_mat.items():
                    should_lock_total = per_unit * actual_quantity
                    existing_locks = db.query(MaterialLock).filter(
                        MaterialLock.order_id == order.id,
                        MaterialLock.step_id == step.id,
                        MaterialLock.material_id == mat_id,
                        MaterialLock.scenario_id.is_(None)
                    ).all()

                    total_available = sum(l.quantity for l in existing_locks)
                    to_consume = min(should_lock_total, total_available)

                    remaining_to_consume = to_consume
                    for lock in existing_locks:
                        if remaining_to_consume <= 0:
                            break
                        take = min(lock.quantity, remaining_to_consume)
                        lock.quantity -= take
                        remaining_to_consume -= take
                        consumed_locks += take

                    for lock in list(existing_locks):
                        if lock.quantity <= 0:
                            db.delete(lock)

    new_delivered_total = already_delivered + actual_quantity
    if new_delivered_total >= plan.planned_quantity:
        plan.status = "delivered"

    db.commit()
    db.refresh(plan)
    db.refresh(record)

    return True, {
        "message": f"第{plan.plan_index}批交付成功，实际交付{actual_quantity}件",
        "delivery_record": {
            "id": record.id,
            "order_id": record.order_id,
            "delivery_plan_id": record.delivery_plan_id,
            "actual_quantity": record.actual_quantity,
            "delivered_at": record.delivered_at,
            "accepted_by": record.accepted_by,
            "accepted_at": record.accepted_at,
            "status": record.status,
            "remarks": record.remarks
        },
        "plan_status": plan.status,
        "remaining_quantity": plan.planned_quantity - new_delivered_total
    }


def get_delivery_progress(db: Session, order_id: int) -> Optional[Dict]:
    order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
    if not order:
        return None

    plans = db.query(DeliveryPlan).options(
        joinedload(DeliveryPlan.sub_batches),
        joinedload(DeliveryPlan.delivery_records)
    ).filter(
        DeliveryPlan.order_id == order_id,
        DeliveryPlan.scenario_id.is_(None)
    ).order_by(DeliveryPlan.plan_index).all()

    if not plans:
        return {
            "order_id": order.id,
            "order_no": order.order_no,
            "total_quantity": order.total_quantity,
            "total_planned_batches": 0,
            "delivered_batches": 0,
            "partially_delivered_batches": 0,
            "total_planned_quantity": 0,
            "total_delivered_quantity": 0,
            "delivery_percent": 0.0,
            "next_batch_plan_index": None,
            "next_batch_planned_quantity": None,
            "next_batch_expected_date": None,
            "next_batch_estimated_delivery": None,
            "next_batch_can_meet_deadline": None,
            "batches_detail": []
        }

    total_planned = sum(p.planned_quantity for p in plans)
    delivered_batches = 0
    partially_delivered_batches = 0
    total_delivered = 0
    batches_detail = []
    next_batch = None

    for plan in plans:
        plan_delivered = sum(
            r.actual_quantity for r in plan.delivery_records
            if r.status in ["delivered", "accepted"]
        )
        total_delivered += plan_delivered

        estimated_completion = None
        end_times = [sb.actual_end_time for sb in plan.sub_batches if sb.actual_end_time]
        if end_times:
            estimated_completion = max(end_times)

        in_progress_sbs = [sb for sb in plan.sub_batches if sb.status == "in_progress"]
        pending_sbs = [sb for sb in plan.sub_batches if sb.status == "pending"]
        completed_sbs = [sb for sb in plan.sub_batches if sb.status == "completed"]

        if not estimated_completion and (in_progress_sbs or pending_sbs):
            route = db.query(ProcessRoute).filter(
                ProcessRoute.product_name == order.product_name
            ).first()
            if route:
                total_step_duration = sum(s.duration_minutes for s in route.steps)
                min_start = min(
                    [sb.actual_start_time for sb in in_progress_sbs if sb.actual_start_time] or
                    [datetime.utcnow()]
                )
                estimated_completion = min_start + timedelta(minutes=total_step_duration)

        can_meet = None
        if estimated_completion and plan.status != "delivered":
            can_meet = estimated_completion <= plan.expected_delivery_date

        if plan_delivered >= plan.planned_quantity and plan.planned_quantity > 0:
            plan_status = "delivered"
            delivered_batches += 1
        elif plan_delivered > 0:
            plan_status = "partially_delivered"
            partially_delivered_batches += 1
        else:
            plan_status = "pending"
            if next_batch is None:
                next_batch = plan

        batches_detail.append({
            "delivery_plan_id": plan.id,
            "plan_index": plan.plan_index,
            "planned_quantity": plan.planned_quantity,
            "actual_delivered_quantity": plan_delivered,
            "remaining_quantity": plan.planned_quantity - plan_delivered,
            "expected_delivery_date": plan.expected_delivery_date,
            "estimated_completion_time": estimated_completion,
            "status": plan_status,
            "can_meet_deadline": can_meet,
            "sub_batches_count": len(plan.sub_batches),
            "completed_sub_batches": len(completed_sbs),
            "in_progress_sub_batches": len(in_progress_sbs),
            "pending_sub_batches": len(pending_sbs)
        })

    delivery_percent = round((total_delivered / total_planned * 100), 2) if total_planned > 0 else 0.0

    next_idx = None
    next_qty = None
    next_date = None
    next_est = None
    next_can_meet = None
    if next_batch:
        next_idx = next_batch.plan_index
        next_qty = next_batch.planned_quantity
        next_date = next_batch.expected_delivery_date
        nxt_bd = next((bd for bd in batches_detail if bd["plan_index"] == next_batch.plan_index), None)
        if nxt_bd:
            next_est = nxt_bd["estimated_completion_time"]
            next_can_meet = nxt_bd["can_meet_deadline"]

    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "total_quantity": order.total_quantity,
        "total_planned_batches": len(plans),
        "delivered_batches": delivered_batches,
        "partially_delivered_batches": partially_delivered_batches,
        "total_planned_quantity": total_planned,
        "total_delivered_quantity": total_delivered,
        "delivery_percent": delivery_percent,
        "next_batch_plan_index": next_idx,
        "next_batch_planned_quantity": next_qty,
        "next_batch_expected_date": next_date,
        "next_batch_estimated_delivery": next_est,
        "next_batch_can_meet_deadline": next_can_meet,
        "batches_detail": batches_detail
    }


def get_delivery_conflicts(
    db: Session,
    order_id: Optional[int] = None
) -> List[Dict]:
    conflicts = db.query(ConflictRecord).filter(
        ConflictRecord.conflict_type == "delivery_plan_delay",
        ConflictRecord.scenario_id.is_(None)
    )
    if order_id:
        conflicts = conflicts.filter(ConflictRecord.order_id == order_id)
    conflicts = conflicts.order_by(ConflictRecord.detected_at.desc()).all()

    results = []
    for c in conflicts:
        order = db.query(WorkOrder).filter(WorkOrder.id == c.order_id).first()
        order_no = order.order_no if order else None

        plan_id = None
        plan_index = None
        planned_qty = None
        expected_date = None
        estimated_date = None
        delay_minutes = 0
        delay_human = ""

        desc = c.description
        try:
            if "第" in desc and "批延期" in desc:
                idx_part = desc.split("第")[1].split("批延期")[0]
                plan_index = int(idx_part)

                plan = db.query(DeliveryPlan).filter(
                    DeliveryPlan.order_id == c.order_id,
                    DeliveryPlan.plan_index == plan_index
                ).first()
                if plan:
                    plan_id = plan.id
                    planned_qty = plan.planned_quantity
                    expected_date = plan.expected_delivery_date

                    sub_batches = db.query(SubBatch).filter(
                        SubBatch.delivery_plan_id == plan.id
                    ).all()
                    end_times = [sb.actual_end_time for sb in sub_batches if sb.actual_end_time]
                    if end_times:
                        estimated_date = max(end_times)

                        if estimated_date and expected_date:
                            secs = (estimated_date - expected_date).total_seconds()
                            delay_minutes = int(secs / 60)
                            h = delay_minutes // 60
                            d = h // 24
                            if d > 0:
                                delay_human = f"{d}天{h % 24}小时"
                            elif h > 0:
                                delay_human = f"{h}小时{delay_minutes % 60}分钟"
                            else:
                                delay_human = f"{delay_minutes}分钟"
        except Exception:
            pass

        results.append({
            "order_id": c.order_id,
            "order_no": order_no,
            "delivery_plan_id": plan_id,
            "plan_index": plan_index,
            "planned_quantity": planned_qty,
            "expected_delivery_date": expected_date,
            "estimated_completion_time": estimated_date,
            "delay_minutes": delay_minutes,
            "delay_human": delay_human or desc
        })

    return results


def cancel_order_with_delivery(
    db: Session,
    order_id: int
) -> Tuple[bool, Dict]:
    from app.scheduler import (
        release_material_locks_for_order, release_fixtures_for_order,
        release_sub_batches_for_order, reschedule_unlocked_orders
    )
    from app.outsourcing_service import delete_outsourcing_entries_for_order

    order = db.query(WorkOrder).options(
        joinedload(WorkOrder.sub_batches),
        joinedload(WorkOrder.schedule_entries),
        joinedload(WorkOrder.delivery_records),
        joinedload(WorkOrder.delivery_plans)
    ).filter(WorkOrder.id == order_id).first()

    if not order:
        return False, {"message": f"工单 {order_id} 不存在"}

    delivered_records = [r for r in order.delivery_records if r.status in ["delivered", "accepted"]]
    total_delivered_qty = sum(r.actual_quantity for r in delivered_records)

    if total_delivered_qty > 0:
        delivered_plan_ids = set(r.delivery_plan_id for r in delivered_records)

        delivered_sb_ids = set()
        for plan in order.delivery_plans:
            if plan.id in delivered_plan_ids:
                delivered_sb_ids.update(sb.id for sb in plan.sub_batches)

        delivered_se_ids = set()
        for se in order.schedule_entries:
            if se.sub_batch_id in delivered_sb_ids or se.is_delivered_locked:
                delivered_se_ids.add(se.id)

        non_delivered_sbs = [sb for sb in order.sub_batches if sb.id not in delivered_sb_ids]
        non_delivered_ses = [se for se in order.schedule_entries if se.id not in delivered_se_ids]

        for sb in non_delivered_sbs:
            if sb.status != "completed" or sb.delivered_quantity == 0:
                sb.status = "cancelled"

        for se in non_delivered_ses:
            db.delete(se)

        order.status = "partially_cancelled"
        order.is_blocked = True
        order.blocked_reason = f"工单部分撤销：已交付{total_delivered_qty}件，剩余部分已取消"
        db.commit()

        reschedule_unlocked_orders(db)

        return True, {
            "message": f"工单部分撤销成功：已保留已交付{len(delivered_records)}批({total_delivered_qty}件)，其余部分已取消",
            "total_delivered_quantity": total_delivered_qty,
            "delivered_batches": len(delivered_plan_ids),
            "cancelled_remaining": True,
            "order_status": order.status
        }

    else:
        release_material_locks_for_order(db, order_id)
        release_fixtures_for_order(db, order_id)
        release_sub_batches_for_order(db, order_id)
        delete_outsourcing_entries_for_order(db, order_id)
        db.delete(order)
        db.commit()

        reschedule_unlocked_orders(db)

        return True, {
            "message": "工单撤销成功(无交付记录，整单删除)",
            "total_delivered_quantity": 0,
            "delivered_batches": 0,
            "cancelled_remaining": True,
            "order_status": "deleted"
        }
