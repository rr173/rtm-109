from datetime import datetime, timedelta, date, time
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
import hashlib
import json

from app.models import (
    Scenario, ScenarioAuditLog, ScenarioMaintenanceOverride,
    ScenarioDeviceOverride, ScenarioFixtureOverride,
    WorkOrder, SubBatch, ScheduleEntry, SubBatchStepProgress,
    ConflictRecord, MaterialLock, DeviceFault, OutsourcingScheduleEntry,
    Device, MaintenancePlan, FixtureType, Fixture, ProcessRoute, ProcessStep
)
from app.scheduler import (
    schedule_order, reschedule_unlocked_orders,
    release_material_locks_for_order, release_fixtures_for_order,
    find_earliest_slot_with_siblings, select_best_device_and_fixture,
    get_maintenance_windows_in_range
)


SCENARIO_FILTER_TABLES = {
    "work_orders": WorkOrder,
    "sub_batches": SubBatch,
    "schedule_entries": ScheduleEntry,
    "sub_batch_step_progress": SubBatchStepProgress,
    "conflict_records": ConflictRecord,
    "material_locks": MaterialLock,
    "device_faults": DeviceFault,
}


def _log_action(db: Session, scenario_id: int, action: str, operator: Optional[str] = None, details: Optional[str] = None):
    log = ScenarioAuditLog(
        scenario_id=scenario_id,
        action=action,
        operator=operator,
        details=details
    )
    db.add(log)
    db.flush()


def compute_baseline_hash(db: Session) -> Tuple[str, datetime]:
    timestamp = datetime.utcnow()
    data_parts = []

    orders = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).order_by(WorkOrder.id).all()
    for o in orders:
        data_parts.append(f"order:{o.id}:{o.status}:{o.is_locked}:{o.updated_at if hasattr(o, 'updated_at') else o.id}")

    entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).order_by(ScheduleEntry.id).all()
    for e in entries:
        data_parts.append(f"entry:{e.id}:{e.start_time.isoformat()}:{e.end_time.isoformat()}:{e.device_id}")

    faults = db.query(DeviceFault).filter(
        DeviceFault.scenario_id.is_(None),
        DeviceFault.status == "active"
    ).order_by(DeviceFault.id).all()
    for f in faults:
        data_parts.append(f"fault:{f.id}:{f.status}:{f.expected_recovery_time.isoformat()}")

    raw = "|".join(data_parts)
    hash_obj = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hash_obj, timestamp


def verify_baseline(db: Session, scenario: Scenario) -> Tuple[bool, str]:
    current_hash, _ = compute_baseline_hash(db)
    if not scenario.baseline_hash:
        return False, "预案基线未设置"
    if current_hash != scenario.baseline_hash:
        return False, "正式计划已被修改，基线发生变化，请重新创建预案"
    return True, "基线校验通过"


def create_scenario(db: Session, name: str, description: Optional[str] = None,
                    created_by: Optional[str] = None) -> Scenario:
    scenario = Scenario(
        name=name,
        description=description,
        status="draft",
        created_by=created_by
    )
    db.add(scenario)
    db.flush()
    db.refresh(scenario)

    baseline_hash, baseline_ts = compute_baseline_hash(db)
    scenario.baseline_hash = baseline_hash
    scenario.baseline_timestamp = baseline_ts
    db.flush()

    _copy_production_to_scenario(db, scenario.id)

    _log_action(db, scenario.id, "create", created_by, f"创建预案: {name}")
    db.commit()
    db.refresh(scenario)
    return scenario


def _copy_production_to_scenario(db: Session, scenario_id: int):
    order_id_map = {}
    sub_batch_id_map = {}
    schedule_entry_id_map = {}

    prod_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).all()
    for po in prod_orders:
        new_order = WorkOrder(
            order_no=po.order_no,
            product_name=po.product_name,
            expected_start_time=po.expected_start_time,
            deadline=po.deadline,
            status=po.status,
            is_locked=po.is_locked,
            bottleneck_step=po.bottleneck_step,
            total_quantity=po.total_quantity,
            is_split=po.is_split,
            total_sub_batches=po.total_sub_batches,
            is_blocked=po.is_blocked,
            blocked_reason=po.blocked_reason,
            scenario_id=scenario_id,
            source_order_id=po.id
        )
        db.add(new_order)
        db.flush()
        order_id_map[po.id] = new_order.id

    prod_sub_batches = db.query(SubBatch).filter(SubBatch.scenario_id.is_(None)).all()
    for psb in prod_sub_batches:
        new_sb = SubBatch(
            order_id=order_id_map.get(psb.order_id, psb.order_id),
            batch_no=psb.batch_no,
            quantity=psb.quantity,
            status=psb.status,
            actual_start_time=psb.actual_start_time,
            actual_end_time=psb.actual_end_time,
            parent_sub_batch_id=None,
            is_replenishment=psb.is_replenishment,
            replenish_level=psb.replenish_level,
            replenish_from_step=psb.replenish_from_step,
            scenario_id=scenario_id,
            source_sub_batch_id=psb.id
        )
        db.add(new_sb)
        db.flush()
        sub_batch_id_map[psb.id] = new_sb.id

    for psb in prod_sub_batches:
        if psb.parent_sub_batch_id and psb.parent_sub_batch_id in sub_batch_id_map:
            new_sb = db.query(SubBatch).filter(SubBatch.id == sub_batch_id_map[psb.id]).first()
            if new_sb:
                new_sb.parent_sub_batch_id = sub_batch_id_map[psb.parent_sub_batch_id]

    prod_entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).all()
    for pe in prod_entries:
        new_entry = ScheduleEntry(
            order_id=order_id_map.get(pe.order_id, pe.order_id),
            sub_batch_id=sub_batch_id_map.get(pe.sub_batch_id, pe.sub_batch_id),
            step_id=pe.step_id,
            device_id=pe.device_id,
            fixture_id=pe.fixture_id,
            step_order=pe.step_order,
            step_name=pe.step_name,
            start_time=pe.start_time,
            end_time=pe.end_time,
            is_completed=pe.is_completed,
            actual_completion_time=pe.actual_completion_time,
            migrated_from_device_id=pe.migrated_from_device_id,
            is_migrated=pe.is_migrated,
            fixture_turn_over_end_time=pe.fixture_turn_over_end_time,
            scenario_id=scenario_id,
            source_schedule_entry_id=pe.id
        )
        db.add(new_entry)
        db.flush()
        schedule_entry_id_map[pe.id] = new_entry.id

    prod_progress = db.query(SubBatchStepProgress).filter(SubBatchStepProgress.scenario_id.is_(None)).all()
    for pp in prod_progress:
        new_p = SubBatchStepProgress(
            sub_batch_id=sub_batch_id_map.get(pp.sub_batch_id, pp.sub_batch_id),
            step_order=pp.step_order,
            step_name=pp.step_name,
            step_id=pp.step_id,
            is_completed=pp.is_completed,
            actual_completion_time=pp.actual_completion_time,
            good_quantity=pp.good_quantity,
            scrap_quantity=pp.scrap_quantity,
            reported_at=pp.reported_at,
            scenario_id=scenario_id
        )
        db.add(new_p)

    prod_conflicts = db.query(ConflictRecord).filter(ConflictRecord.scenario_id.is_(None)).all()
    for pc in prod_conflicts:
        new_c = ConflictRecord(
            order_id=order_id_map.get(pc.order_id, pc.order_id),
            conflict_type=pc.conflict_type,
            description=pc.description,
            detected_at=pc.detected_at,
            scenario_id=scenario_id
        )
        db.add(new_c)

    prod_locks = db.query(MaterialLock).filter(MaterialLock.scenario_id.is_(None)).all()
    for pl in prod_locks:
        new_l = MaterialLock(
            order_id=order_id_map.get(pl.order_id, pl.order_id),
            step_id=pl.step_id,
            material_id=pl.material_id,
            quantity=pl.quantity,
            created_at=pl.created_at,
            scenario_id=scenario_id
        )
        db.add(new_l)

    prod_faults = db.query(DeviceFault).filter(DeviceFault.scenario_id.is_(None)).all()
    for pf in prod_faults:
        new_f = DeviceFault(
            device_id=pf.device_id,
            fault_time=pf.fault_time,
            expected_recovery_time=pf.expected_recovery_time,
            actual_recovery_time=pf.actual_recovery_time,
            status=pf.status,
            description=pf.description,
            created_at=pf.created_at,
            resolved_at=pf.resolved_at,
            scenario_id=scenario_id
        )
        db.add(new_f)

    db.flush()


def list_scenarios(db: Session, status: Optional[str] = None) -> List[Scenario]:
    query = db.query(Scenario)
    if status:
        query = query.filter(Scenario.status == status)
    return query.order_by(Scenario.created_at.desc()).all()


def get_scenario(db: Session, scenario_id: int) -> Optional[Scenario]:
    return db.query(Scenario).filter(Scenario.id == scenario_id).first()


def delete_scenario(db: Session, scenario_id: int, operator: Optional[str] = None) -> bool:
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return False

    db.query(SubBatchStepProgress).filter(SubBatchStepProgress.scenario_id == scenario_id).delete(
        synchronize_session=False
    )
    db.query(MaterialLock).filter(MaterialLock.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(ConflictRecord).filter(ConflictRecord.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(OutsourcingScheduleEntry).filter(OutsourcingScheduleEntry.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(SubBatch).filter(SubBatch.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(WorkOrder).filter(WorkOrder.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(DeviceFault).filter(DeviceFault.scenario_id == scenario_id).delete(synchronize_session=False)
    db.query(ScenarioMaintenanceOverride).filter(
        ScenarioMaintenanceOverride.scenario_id == scenario_id
    ).delete(synchronize_session=False)
    db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id
    ).delete(synchronize_session=False)
    db.query(ScenarioFixtureOverride).filter(
        ScenarioFixtureOverride.scenario_id == scenario_id
    ).delete(synchronize_session=False)
    db.query(ScenarioAuditLog).filter(
        ScenarioAuditLog.scenario_id == scenario_id
    ).delete(synchronize_session=False)

    _log_action(db, scenario_id, "delete", operator, f"删除预案: {scenario.name}")
    db.delete(scenario)
    db.commit()
    return True


def add_urgent_order_to_scenario(db: Session, scenario_id: int, order_data: Dict,
                                 operator: Optional[str] = None) -> Tuple[bool, Dict]:
    scenario = get_scenario(db, scenario_id)
    if not scenario or scenario.status != "draft":
        return False, {"message": "预案不存在或非草稿状态"}

    route = db.query(ProcessRoute).filter(ProcessRoute.product_name == order_data["product_name"]).first()
    if not route:
        return False, {"message": f"产品 '{order_data['product_name']}' 无工艺路线"}

    existing = db.query(WorkOrder).filter(
        WorkOrder.scenario_id == scenario_id,
        WorkOrder.order_no == order_data["order_no"]
    ).first()
    if existing:
        return False, {"message": f"工单号 '{order_data['order_no']}' 已存在"}

    db_order = WorkOrder(
        order_no=order_data["order_no"],
        product_name=order_data["product_name"],
        expected_start_time=order_data["expected_start_time"],
        deadline=order_data["deadline"],
        status="pending",
        is_locked=True,
        total_quantity=order_data.get("total_quantity", 1),
        scenario_id=scenario_id
    )
    db.add(db_order)
    db.flush()

    from app.scenario_scheduler import scenario_schedule_order
    result = scenario_schedule_order(db, db_order, scenario_id, respect_locked=True)

    if result.get("success"):
        from app.scenario_scheduler import scenario_reschedule_unlocked_orders
        scenario_reschedule_unlocked_orders(db, scenario_id, exclude_order_id=db_order.id)

    db.commit()
    _log_action(
        db, scenario_id, "add_urgent_order", operator,
        f"插入急单: {order_data['order_no']}, 产品: {order_data['product_name']}, 结果: {result.get('success')}"
    )
    return True, result


def set_device_unavailable(db: Session, scenario_id: int, device_id: int,
                           effective_from: Optional[datetime] = None, effective_to: Optional[datetime] = None,
                           reason: Optional[str] = None,
                           operator: Optional[str] = None) -> Tuple[bool, Dict]:
    scenario = get_scenario(db, scenario_id)
    if not scenario or scenario.status != "draft":
        return False, {"message": "预案不存在或非草稿状态"}

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        return False, {"message": "设备不存在"}

    if effective_from is None:
        effective_from = datetime.utcnow()

    override = ScenarioDeviceOverride(
        scenario_id=scenario_id,
        device_id=device_id,
        override_type="disable",
        effective_from=effective_from,
        effective_to=effective_to,
        reason=reason
    )
    db.add(override)
    db.flush()

    affected = _reassign_schedule_for_disabled_device(db, scenario_id, device_id, effective_from, effective_to)

    db.commit()
    _log_action(
        db, scenario_id, "disable_device", operator,
        f"撤掉设备: {device.name}, 时间段: {effective_from} ~ {effective_to}, 影响排产条目: {affected}"
    )
    return True, {"affected_entries": affected}


def _reassign_schedule_for_disabled_device(db: Session, scenario_id: int, device_id: int,
                                           effective_from: Optional[datetime] = None,
                                           effective_to: Optional[datetime] = None) -> int:
    if effective_from is None:
        effective_from = datetime.utcnow()
    query = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order),
        joinedload(ScheduleEntry.sub_batch)
    ).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.device_id == device_id,
        ScheduleEntry.end_time > effective_from
    )
    if effective_to is not None:
        query = query.filter(ScheduleEntry.start_time < effective_to)
    affected_entries = query.all()

    affected_order_ids = set()
    for entry in affected_entries:
        if entry.order_id:
            affected_order_ids.add(entry.order_id)

    for order_id in affected_order_ids:
        order = db.query(WorkOrder).filter(
            WorkOrder.id == order_id,
            WorkOrder.scenario_id == scenario_id
        ).first()
        if order and not order.is_locked:
            old_entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == order_id,
                ScheduleEntry.scenario_id == scenario_id
            ).all()
            for e in old_entries:
                db.delete(e)

            db.query(MaterialLock).filter(
                MaterialLock.order_id == order_id,
                MaterialLock.scenario_id == scenario_id
            ).delete(synchronize_session=False)

            order.status = "pending"
            order.bottleneck_step = None
            db.flush()

            from app.scenario_scheduler import scenario_schedule_order
            scenario_schedule_order(db, order, scenario_id, respect_locked=False)

    db.flush()
    return len(affected_entries)


def extend_maintenance_window(db: Session, scenario_id: int, maintenance_plan_id: int,
                              new_start_time: Optional[str] = None, new_end_time: Optional[str] = None,
                              new_day_of_week: Optional[int] = None,
                              description: Optional[str] = None,
                              operator: Optional[str] = None) -> Tuple[bool, Dict]:
    scenario = get_scenario(db, scenario_id)
    if not scenario or scenario.status != "draft":
        return False, {"message": "预案不存在或非草稿状态"}

    plan = db.query(MaintenancePlan).filter(MaintenancePlan.id == maintenance_plan_id).first()
    if not plan:
        return False, {"message": "维护计划不存在"}

    override = ScenarioMaintenanceOverride(
        scenario_id=scenario_id,
        maintenance_plan_id=maintenance_plan_id,
        device_id=plan.device_id,
        override_type="extend",
        new_start_time=new_start_time,
        new_end_time=new_end_time,
        new_day_of_week=new_day_of_week,
        description=description
    )
    db.add(override)
    db.flush()

    affected = _reschedule_for_maintenance_change(db, scenario_id, plan.device_id)

    db.commit()
    _log_action(
        db, scenario_id, "extend_maintenance", operator,
        f"调整维护窗口: 设备ID={plan.device_id}, 新时段: {new_start_time}-{new_end_time}, 影响: {affected}"
    )
    return True, {"affected_entries": affected}


def _reschedule_for_maintenance_change(db: Session, scenario_id: int, device_id: int) -> int:
    entries_on_device = db.query(ScheduleEntry).filter(
        ScheduleEntry.scenario_id == scenario_id,
        ScheduleEntry.device_id == device_id
    ).all()

    affected_order_ids = set()
    for entry in entries_on_device:
        if entry.order_id:
            order = db.query(WorkOrder).filter(
                WorkOrder.id == entry.order_id,
                WorkOrder.scenario_id == scenario_id
            ).first()
            if order and not order.is_locked:
                affected_order_ids.add(order.id)

    for order_id in affected_order_ids:
        order = db.query(WorkOrder).filter(
            WorkOrder.id == order_id,
            WorkOrder.scenario_id == scenario_id
        ).first()
        if order:
            db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == order_id,
                ScheduleEntry.scenario_id == scenario_id
            ).delete(synchronize_session=False)
            db.query(MaterialLock).filter(
                MaterialLock.order_id == order_id,
                MaterialLock.scenario_id == scenario_id
            ).delete(synchronize_session=False)

            order.status = "pending"
            order.bottleneck_step = None
            db.flush()

            from app.scenario_scheduler import scenario_schedule_order
            scenario_schedule_order(db, order, scenario_id, respect_locked=False)

    db.flush()
    return len(affected_order_ids)


def adjust_fixture_quantity(db: Session, scenario_id: int, fixture_type_id: int = 0,
                            quantity_change: int = 0, reason: Optional[str] = None,
                            operator: Optional[str] = None) -> Tuple[bool, Dict]:
    scenario = get_scenario(db, scenario_id)
    if not scenario or scenario.status != "draft":
        return False, {"message": "预案不存在或非草稿状态"}

    fixture_type = db.query(FixtureType).filter(FixtureType.id == fixture_type_id).first()
    if not fixture_type:
        return False, {"message": "工装类型不存在"}

    temp_fixtures = []
    if quantity_change > 0:
        existing_fixtures = db.query(Fixture).filter(Fixture.fixture_type_id == fixture_type_id).all()
        device_types = set()
        for f in existing_fixtures:
            for dt in f.compatible_device_types.split(","):
                device_types.add(dt.strip())
        compatible_types_str = ",".join(sorted(device_types)) if device_types else "通用"

        for i in range(quantity_change):
            override = ScenarioFixtureOverride(
                scenario_id=scenario_id,
                fixture_type_id=fixture_type_id,
                override_type="add",
                quantity_change=1,
                temp_fixture_code=f"TEMP-S{scenario_id}-{fixture_type_id}-{i+1}",
                temp_status="available",
                reason=reason
            )
            db.add(override)
            temp_fixtures.append(override.temp_fixture_code)
    elif quantity_change < 0:
        override = ScenarioFixtureOverride(
            scenario_id=scenario_id,
            fixture_type_id=fixture_type_id,
            override_type="reduce",
            quantity_change=quantity_change,
            reason=reason
        )
        db.add(override)

    db.flush()

    from app.scenario_scheduler import scenario_reschedule_unlocked_orders
    scenario_reschedule_unlocked_orders(db, scenario_id)

    db.commit()
    _log_action(
        db, scenario_id, "adjust_fixture", operator,
        f"调整工装数量: 类型={fixture_type.name}, 变化={quantity_change}, 临时工装: {temp_fixtures}"
    )
    return True, {"temp_fixtures": temp_fixtures, "quantity_change": quantity_change}


def get_scenario_gantt(db: Session, scenario_id: int, date_str: str) -> Dict:
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return {"error": "日期格式错误"}

    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return {"error": "预案不存在"}

    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)

    devices = db.query(Device).order_by(Device.id).all()

    device_overrides = db.query(ScenarioDeviceOverride).filter(
        ScenarioDeviceOverride.scenario_id == scenario_id,
        ScenarioDeviceOverride.override_type == "disable"
    ).all()
    disabled_device_ids = set()
    for ov in device_overrides:
        if ov.effective_from and ov.effective_from < day_end:
            if ov.effective_to is None or ov.effective_to > day_start:
                disabled_device_ids.add(ov.device_id)

    maintenance_overrides = db.query(ScenarioMaintenanceOverride).filter(
        ScenarioMaintenanceOverride.scenario_id == scenario_id
    ).all()

    device_gantts = []
    for device in devices:
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order),
            joinedload(ScheduleEntry.sub_batch)
        ).filter(
            ScheduleEntry.scenario_id == scenario_id,
            ScheduleEntry.device_id == device.id,
            ScheduleEntry.start_time < day_end,
            ScheduleEntry.end_time > day_start
        ).order_by(ScheduleEntry.start_time).all()

        gantt_entries = []
        for entry in entries:
            order = entry.order
            if entry.changeover_start_time and entry.changeover_end_time and (entry.changeover_minutes or 0) > 0:
                gantt_entries.append({
                    "id": entry.id * 10000,
                    "order_no": order.order_no if order else "unknown",
                    "batch_no": entry.sub_batch.batch_no if entry.sub_batch else None,
                    "step_name": f"换型({entry.changeover_type or ''})",
                    "start_time": entry.changeover_start_time,
                    "end_time": entry.changeover_end_time,
                    "is_locked": order.is_locked if order else False,
                    "entry_type": "changeover",
                    "changeover_start_time": entry.changeover_start_time,
                    "changeover_end_time": entry.changeover_end_time,
                    "changeover_minutes": entry.changeover_minutes,
                    "changeover_type": entry.changeover_type,
                    "prev_product_name": entry.prev_product_name,
                })
            gantt_entries.append({
                "id": entry.id,
                "order_no": order.order_no if order else "unknown",
                "batch_no": entry.sub_batch.batch_no if entry.sub_batch else None,
                "step_name": entry.step_name,
                "start_time": entry.start_time,
                "end_time": entry.end_time,
                "is_locked": order.is_locked if order else False,
                "entry_type": "production",
                "changeover_start_time": entry.changeover_start_time,
                "changeover_end_time": entry.changeover_end_time,
                "changeover_minutes": entry.changeover_minutes or 0,
                "changeover_type": entry.changeover_type,
                "prev_product_name": entry.prev_product_name,
            })

        if device.id in disabled_device_ids:
            gantt_entries.append({
                "id": -1,
                "order_no": "[设备不可用]",
                "batch_no": None,
                "step_name": "设备停用(预案)",
                "start_time": day_start,
                "end_time": day_end,
                "is_locked": True,
            })

        device_gantts.append({
            "device_id": device.id,
            "device_name": device.name,
            "device_type": device.device_type,
            "entries": gantt_entries,
        })

    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario.name,
        "date": date_str,
        "devices": device_gantts,
    }


def get_scenario_conflicts(db: Session, scenario_id: int) -> Dict:
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return {"error": "预案不存在"}

    conflicts = db.query(ConflictRecord).filter(
        ConflictRecord.scenario_id == scenario_id
    ).order_by(ConflictRecord.detected_at.desc()).all()

    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario.name,
        "conflicts": [
            {
                "id": c.id,
                "order_id": c.order_id,
                "conflict_type": c.conflict_type,
                "description": c.description,
                "detected_at": c.detected_at
            }
            for c in conflicts
        ],
        "total": len(conflicts)
    }


def compute_scenario_diff(db: Session, scenario_id: int) -> Dict:
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return {"error": "预案不存在"}

    baseline_ok, _ = verify_baseline(db, scenario)

    prod_entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(ScheduleEntry.scenario_id.is_(None)).all()

    prod_order_ends = {}
    prod_device_loads = {}
    for e in prod_entries:
        oid = e.order_id
        if oid:
            if oid not in prod_order_ends or e.end_time > prod_order_ends[oid]:
                prod_order_ends[oid] = e.end_time
        did = e.device_id
        if did not in prod_device_loads:
            prod_device_loads[did] = 0
        prod_device_loads[did] += int((e.end_time - e.start_time).total_seconds() / 60)

    scenario_entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(ScheduleEntry.scenario_id == scenario_id).all()

    scen_order_map = {}
    scen_order_ends = {}
    scen_device_loads = {}
    for e in scenario_entries:
        oid = e.order_id
        if oid:
            order = e.order
            if order and order.source_order_id:
                scen_order_map[oid] = order.source_order_id
            if oid not in scen_order_ends or e.end_time > scen_order_ends[oid]:
                scen_order_ends[oid] = e.end_time
        did = e.device_id
        if did not in scen_device_loads:
            scen_device_loads[did] = 0
        scen_device_loads[did] += int((e.end_time - e.start_time).total_seconds() / 60)

    delayed_orders = []
    all_source_ids = set(prod_order_ends.keys())
    for scen_oid, src_oid in scen_order_map.items():
        all_source_ids.add(src_oid)

    for src_oid in all_source_ids:
        scen_oid = None
        for soid, so in scen_order_map.items():
            if so == src_oid:
                scen_oid = soid
                break
        if not scen_oid:
            continue

        prod_end = prod_order_ends.get(src_oid)
        scen_end = scen_order_ends.get(scen_oid)
        if not prod_end or not scen_end:
            continue

        delay = int((scen_end - prod_end).total_seconds() / 60)
        if delay > 0:
            order = db.query(WorkOrder).filter(WorkOrder.id == scen_oid).first()
            last_entry = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == scen_oid,
                ScheduleEntry.scenario_id == scenario_id
            ).order_by(ScheduleEntry.step_order.desc()).first()
            delayed_orders.append({
                "order_id": src_oid,
                "order_no": order.order_no if order else f"Order-{src_oid}",
                "original_end_time": prod_end,
                "scenario_end_time": scen_end,
                "delay_minutes": delay,
                "affected_step": last_entry.step_name if last_entry else None
            })

    delayed_orders.sort(key=lambda x: -x["delay_minutes"])

    device_load_changes = []
    all_devices = db.query(Device).all()
    for d in all_devices:
        did = d.id
        orig = prod_device_loads.get(did, 0)
        scen = scen_device_loads.get(did, 0)
        change = scen - orig
        if change != 0:
            pct = (change / orig * 100) if orig > 0 else 100.0 if scen > 0 else 0.0
            device_load_changes.append({
                "device_id": did,
                "device_name": d.name,
                "original_scheduled_minutes": orig,
                "scenario_scheduled_minutes": scen,
                "load_change_minutes": change,
                "load_change_percent": round(pct, 2)
            })
    device_load_changes.sort(key=lambda x: -abs(x["load_change_minutes"]))

    overdue_orders = []
    prod_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).all()
    src_to_deadline = {}
    src_to_orderno = {}
    for po in prod_orders:
        src_to_deadline[po.id] = po.deadline
        src_to_orderno[po.id] = po.order_no

    for scen_oid, src_oid in scen_order_map.items():
        deadline = src_to_deadline.get(src_oid)
        if not deadline:
            continue
        prod_end = prod_order_ends.get(src_oid)
        scen_end = scen_order_ends.get(scen_oid)

        orig_overdue = bool(prod_end and prod_end > deadline)
        scen_overdue = bool(scen_end and scen_end > deadline)
        orig_ov_min = int((prod_end - deadline).total_seconds() / 60) if (prod_end and prod_end > deadline) else 0
        scen_ov_min = int((scen_end - deadline).total_seconds() / 60) if (scen_end and scen_end > deadline) else 0
        change = scen_ov_min - orig_ov_min

        if orig_overdue or scen_overdue or change != 0:
            overdue_orders.append({
                "order_id": src_oid,
                "order_no": src_to_orderno.get(src_oid, f"Order-{src_oid}"),
                "deadline": deadline,
                "original_end_time": prod_end,
                "scenario_end_time": scen_end,
                "originally_overdue": orig_overdue,
                "scenario_overdue": scen_overdue,
                "original_overdue_minutes": orig_ov_min,
                "scenario_overdue_minutes": scen_ov_min,
                "overdue_change": change
            })
    overdue_orders.sort(key=lambda x: -x["overdue_change"])

    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario.name,
        "baseline_unchanged": baseline_ok,
        "delayed_orders": delayed_orders,
        "device_load_changes": device_load_changes,
        "overdue_orders": overdue_orders,
        "total_delayed": len(delayed_orders),
        "total_devices_changed": len(device_load_changes),
        "total_overdue_changed": len(overdue_orders)
    }


def check_publish_constraints(db: Session, scenario_id: int) -> Dict:
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return {"error": "预案不存在"}

    baseline_ok, baseline_msg = verify_baseline(db, scenario)

    conflicts = db.query(ConflictRecord).filter(ConflictRecord.scenario_id == scenario_id).all()

    violations = []
    if not baseline_ok:
        violations.append(baseline_msg)

    failed_orders = db.query(WorkOrder).filter(
        WorkOrder.scenario_id == scenario_id,
        WorkOrder.status == "failed"
    ).all()
    for fo in failed_orders:
        violations.append(f"工单 '{fo.order_no}' 排产失败: {fo.bottleneck_step or '未知原因'}")

    conflict_types = {}
    for c in conflicts:
        ct = c.conflict_type
        if ct not in conflict_types:
            conflict_types[ct] = 0
        conflict_types[ct] += 1
    for ct, cnt in conflict_types.items():
        if ct in ("material_shortage", "scheduling_failed"):
            violations.append(f"存在 {cnt} 条{ct}类型冲突未解决")

    can_publish = len(violations) == 0

    return {
        "can_publish": can_publish,
        "baseline_matches": baseline_ok,
        "baseline_message": baseline_msg,
        "constraint_violations": violations,
        "active_conflicts_count": len(conflicts)
    }


def publish_scenario(db: Session, scenario_id: int,
                     operator: Optional[str] = None) -> Tuple[bool, Dict]:
    scenario = get_scenario(db, scenario_id)
    if not scenario:
        return False, {"message": "预案不存在"}

    if scenario.status == "published":
        return False, {"message": "预案已发布，不能重复发布"}

    constraints = check_publish_constraints(db, scenario_id)
    if "error" in constraints:
        return False, {"message": constraints["error"]}

    if not constraints["can_publish"]:
        return False, {
            "message": "发布约束不满足",
            "constraints": constraints
        }

    db.query(SubBatchStepProgress).filter(SubBatchStepProgress.scenario_id.is_(None)).delete(
        synchronize_session=False
    )
    db.query(MaterialLock).filter(MaterialLock.scenario_id.is_(None)).delete(synchronize_session=False)
    db.query(ConflictRecord).filter(ConflictRecord.scenario_id.is_(None)).delete(synchronize_session=False)
    db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(synchronize_session=False)
    db.query(SubBatch).filter(SubBatch.scenario_id.is_(None)).delete(synchronize_session=False)
    db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(synchronize_session=False)
    db.query(DeviceFault).filter(DeviceFault.scenario_id.is_(None)).delete(synchronize_session=False)
    db.flush()

    _move_scenario_to_production(db, scenario_id)

    scenario.status = "published"
    scenario.published_at = datetime.utcnow()
    scenario.published_by = operator

    new_baseline_hash, new_baseline_ts = compute_baseline_hash(db)
    scenario.baseline_hash = new_baseline_hash
    scenario.baseline_timestamp = new_baseline_ts

    _log_action(db, scenario_id, "publish", operator, f"发布预案: {scenario.name}")
    db.commit()
    db.refresh(scenario)

    return True, {
        "message": "预案发布成功，正式计划已切换",
        "scenario_id": scenario_id,
        "published_at": scenario.published_at,
        "constraints": constraints
    }


def _move_scenario_to_production(db: Session, scenario_id: int):
    order_id_map = {}
    sub_batch_id_map = {}

    scen_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id == scenario_id).all()
    for so in scen_orders:
        new_order = WorkOrder(
            order_no=so.order_no,
            product_name=so.product_name,
            expected_start_time=so.expected_start_time,
            deadline=so.deadline,
            status=so.status,
            is_locked=so.is_locked,
            bottleneck_step=so.bottleneck_step,
            total_quantity=so.total_quantity,
            is_split=so.is_split,
            total_sub_batches=so.total_sub_batches,
            is_blocked=so.is_blocked,
            blocked_reason=so.blocked_reason,
            scenario_id=None,
            source_order_id=None
        )
        db.add(new_order)
        db.flush()
        order_id_map[so.id] = new_order.id

    scen_sbs = db.query(SubBatch).filter(SubBatch.scenario_id == scenario_id).all()
    for ssb in scen_sbs:
        new_sb = SubBatch(
            order_id=order_id_map.get(ssb.order_id, ssb.order_id),
            batch_no=ssb.batch_no,
            quantity=ssb.quantity,
            status=ssb.status,
            actual_start_time=ssb.actual_start_time,
            actual_end_time=ssb.actual_end_time,
            parent_sub_batch_id=None,
            is_replenishment=ssb.is_replenishment,
            replenish_level=ssb.replenish_level,
            replenish_from_step=ssb.replenish_from_step,
            scenario_id=None,
            source_sub_batch_id=None
        )
        db.add(new_sb)
        db.flush()
        sub_batch_id_map[ssb.id] = new_sb.id

    for ssb in scen_sbs:
        if ssb.parent_sub_batch_id and ssb.parent_sub_batch_id in sub_batch_id_map:
            new_sb = db.query(SubBatch).filter(SubBatch.id == sub_batch_id_map[ssb.id]).first()
            if new_sb:
                new_sb.parent_sub_batch_id = sub_batch_id_map[ssb.parent_sub_batch_id]

    scen_entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == scenario_id).all()
    for se in scen_entries:
        new_entry = ScheduleEntry(
            order_id=order_id_map.get(se.order_id, se.order_id),
            sub_batch_id=sub_batch_id_map.get(se.sub_batch_id, se.sub_batch_id),
            step_id=se.step_id,
            device_id=se.device_id,
            fixture_id=se.fixture_id,
            step_order=se.step_order,
            step_name=se.step_name,
            start_time=se.start_time,
            end_time=se.end_time,
            is_completed=se.is_completed,
            actual_completion_time=se.actual_completion_time,
            migrated_from_device_id=se.migrated_from_device_id,
            is_migrated=se.is_migrated,
            fixture_turn_over_end_time=se.fixture_turn_over_end_time,
            scenario_id=None,
            source_schedule_entry_id=None
        )
        db.add(new_entry)

    scen_progress = db.query(SubBatchStepProgress).filter(SubBatchStepProgress.scenario_id == scenario_id).all()
    for sp in scen_progress:
        new_p = SubBatchStepProgress(
            sub_batch_id=sub_batch_id_map.get(sp.sub_batch_id, sp.sub_batch_id),
            step_order=sp.step_order,
            step_name=sp.step_name,
            step_id=sp.step_id,
            is_completed=sp.is_completed,
            actual_completion_time=sp.actual_completion_time,
            good_quantity=sp.good_quantity,
            scrap_quantity=sp.scrap_quantity,
            reported_at=sp.reported_at,
            scenario_id=None
        )
        db.add(new_p)

    scen_conflicts = db.query(ConflictRecord).filter(ConflictRecord.scenario_id == scenario_id).all()
    for sc in scen_conflicts:
        new_c = ConflictRecord(
            order_id=order_id_map.get(sc.order_id, sc.order_id),
            conflict_type=sc.conflict_type,
            description=sc.description,
            detected_at=sc.detected_at,
            scenario_id=None
        )
        db.add(new_c)

    scen_locks = db.query(MaterialLock).filter(MaterialLock.scenario_id == scenario_id).all()
    for sl in scen_locks:
        new_l = MaterialLock(
            order_id=order_id_map.get(sl.order_id, sl.order_id),
            step_id=sl.step_id,
            material_id=sl.material_id,
            quantity=sl.quantity,
            created_at=sl.created_at,
            scenario_id=None
        )
        db.add(new_l)

    scen_faults = db.query(DeviceFault).filter(DeviceFault.scenario_id == scenario_id).all()
    for sf in scen_faults:
        new_f = DeviceFault(
            device_id=sf.device_id,
            fault_time=sf.fault_time,
            expected_recovery_time=sf.expected_recovery_time,
            actual_recovery_time=sf.actual_recovery_time,
            status=sf.status,
            description=sf.description,
            created_at=sf.created_at,
            resolved_at=sf.resolved_at,
            scenario_id=None
        )
        db.add(new_f)

    db.flush()


def get_scenario_audit_logs(db: Session, scenario_id: int) -> Dict:
    logs = db.query(ScenarioAuditLog).filter(
        ScenarioAuditLog.scenario_id == scenario_id
    ).order_by(ScenarioAuditLog.created_at.desc()).all()

    return {
        "scenario_id": scenario_id,
        "logs": [
            {
                "id": l.id,
                "action": l.action,
                "operator": l.operator,
                "details": l.details,
                "created_at": l.created_at
            }
            for l in logs
        ],
        "total": len(logs)
    }
