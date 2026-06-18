from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from app.models import (
    WorkOrder, ScheduleEntry, ScheduleGroup, ProductFamily,
    ProcessRoute, Device, ProcessStep, ChangeoverRule
)
from app.scheduler import (
    schedule_order, calculate_changeover_minutes, get_product_family_id,
    get_previous_product_on_device, release_material_locks_for_order,
    release_fixtures_for_order, delete_outsourcing_entries_for_order,
    reschedule_unlocked_orders
)
from app.outsourcing_service import delete_outsourcing_entries_for_order
from app.staffing_service import release_employees_for_order
import uuid


def _generate_group_code() -> str:
    return f"GRP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"


def get_orders_with_product_family(
    db: Session,
    order_ids: List[int]
) -> List[Dict]:
    orders = db.query(WorkOrder).options(
        joinedload(WorkOrder.schedule_entries)
    ).filter(WorkOrder.id.in_(order_ids)).all()

    result = []
    for order in orders:
        family_id = get_product_family_id(db, order.product_name)
        family = None
        if family_id:
            family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()

        route = db.query(ProcessRoute).filter(
            ProcessRoute.product_name == order.product_name
        ).first()

        device_types = []
        if route:
            device_types = list(set(step.device_type for step in route.steps))

        result.append({
            "order": order,
            "order_id": order.id,
            "order_no": order.order_no,
            "product_name": order.product_name,
            "product_family_id": family_id,
            "product_family_name": family.name if family else None,
            "deadline": order.deadline,
            "expected_start_time": order.expected_start_time,
            "priority": order.priority,
            "device_types": device_types,
        })

    return result


def group_orders_by_family_and_device(
    db: Session,
    order_infos: List[Dict]
) -> Dict[Tuple, List[Dict]]:
    groups = {}
    for info in order_infos:
        family_id = info["product_family_id"]
        if family_id is None:
            key = (None, tuple(sorted(info["device_types"])))
        else:
            key = (family_id, tuple(sorted(info["device_types"])))

        if key not in groups:
            groups[key] = []
        groups[key].append(info)

    return groups


def calculate_current_changeover_for_orders(
    db: Session,
    orders: List[WorkOrder],
    device_id: int
) -> int:
    total_changeover = 0
    sorted_orders = sorted(orders, key=lambda o: (o.priority, o.expected_start_time))

    prev_product = None
    for order in sorted_orders:
        if prev_product is None:
            prev_product = get_previous_product_on_device(
                db, device_id, order.expected_start_time
            )

        minutes, _ = calculate_changeover_minutes(
            db, device_id, prev_product, order.product_name
        )
        total_changeover += minutes
        prev_product = order.product_name

    return total_changeover


def calculate_grouped_changeover_for_orders(
    db: Session,
    orders: List[WorkOrder],
    device_id: int
) -> int:
    if not orders:
        return 0

    sorted_orders = sorted(orders, key=lambda o: (o.priority, o.expected_start_time))
    first_order = sorted_orders[0]

    prev_product = get_previous_product_on_device(
        db, device_id, first_order.expected_start_time
    )

    minutes, _ = calculate_changeover_minutes(
        db, device_id, prev_product, first_order.product_name
    )
    return minutes


def recommend_groups(
    db: Session,
    order_ids: List[int]
) -> Dict:
    if not order_ids:
        return {
            "success": False,
            "message": "未提供工单ID",
            "recommendations": [],
            "total_estimated_savings_minutes": 0
        }

    order_infos = get_orders_with_product_family(db, order_ids)

    pending_order_infos = [
        info for info in order_infos
        if info["order"].status in ("pending", "failed")
    ]

    if not pending_order_infos:
        return {
            "success": True,
            "message": "没有待排产的工单",
            "recommendations": [],
            "total_estimated_savings_minutes": 0
        }

    family_device_groups = group_orders_by_family_and_device(db, pending_order_infos)

    recommendations = []
    total_savings = 0

    for (family_id, device_types_tuple), group_infos in family_device_groups.items():
        if len(group_infos) < 2:
            continue

        family = None
        if family_id:
            family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()

        device_type_str = device_types_tuple[0] if device_types_tuple else None
        if not device_type_str:
            continue

        devices = db.query(Device).filter(
            Device.device_type == device_type_str
        ).all()

        if not devices:
            continue

        best_device = None
        best_savings = 0
        best_current = 0
        best_grouped = 0

        for device in devices:
            orders_in_group = [info["order"] for info in group_infos]
            current_co = calculate_current_changeover_for_orders(
                db, orders_in_group, device.id
            )
            grouped_co = calculate_grouped_changeover_for_orders(
                db, orders_in_group, device.id
            )
            savings = current_co - grouped_co

            if savings > best_savings:
                best_savings = savings
                best_device = device
                best_current = current_co
                best_grouped = grouped_co

        if best_device and best_savings > 0:
            order_count = len(group_infos)
            priority_score = (best_savings * order_count) / max(len(devices), 1)

            recommendations.append({
                "product_family_id": family_id,
                "product_family_name": family.name if family else None,
                "order_ids": [info["order_id"] for info in group_infos],
                "order_nos": [info["order_no"] for info in group_infos],
                "device_id": best_device.id,
                "device_name": best_device.name,
                "estimated_savings_minutes": best_savings,
                "current_changeover_minutes": best_current,
                "grouped_changeover_minutes": best_grouped,
                "priority_score": priority_score,
            })
            total_savings += best_savings

    recommendations.sort(key=lambda r: r["priority_score"], reverse=True)

    return {
        "success": True,
        "message": f"找到 {len(recommendations)} 组成组建议，预计节省 {total_savings} 分钟换型时间",
        "recommendations": recommendations,
        "total_estimated_savings_minutes": total_savings
    }


def _clear_existing_schedule_for_order(
    db: Session,
    order_id: int
):
    release_material_locks_for_order(db, order_id)
    release_fixtures_for_order(db, order_id)
    release_employees_for_order(db, order_id)
    delete_outsourcing_entries_for_order(db, order_id)
    db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id,
        ScheduleEntry.is_delivered_locked == False
    ).delete(synchronize_session=False)
    from app.models import SubBatch
    db.query(SubBatch).filter(
        SubBatch.order_id == order_id,
        SubBatch.delivered_quantity == 0
    ).delete(synchronize_session=False)
    db.flush()


def schedule_grouped_orders(
    db: Session,
    order_ids: List[int],
    force_group: bool = False,
    allow_delay: bool = True,
    scenario_id: Optional[int] = None
) -> Dict:
    if not order_ids:
        return {
            "success": False,
            "message": "未提供工单ID",
            "results": [],
            "total_scheduled_orders": 0,
            "total_failed_orders": 0,
            "total_estimated_savings_minutes": 0
        }

    order_infos = get_orders_with_product_family(db, order_ids)

    if force_group:
        family_ids = set(info["product_family_id"] for info in order_infos)
        if len(family_ids) > 1 or (len(family_ids) == 1 and None in family_ids and len(order_infos) > 1):
            has_none = None in family_ids
            family_ids.discard(None)
            if len(family_ids) > 1 or (has_none and len(order_infos) > 1):
                return {
                    "success": False,
                    "message": "强制成组失败：工单不属于同一产品族",
                    "results": [],
                    "total_scheduled_orders": 0,
                    "total_failed_orders": 0,
                    "total_estimated_savings_minutes": 0
                }

        device_type_sets = [set(info["device_types"]) for info in order_infos]
        common_device_types = set.intersection(*device_type_sets) if device_type_sets else set()
        if not common_device_types:
            return {
                "success": False,
                "message": "强制成组失败：工单没有共同的设备类型",
                "results": [],
                "total_scheduled_orders": 0,
                "total_failed_orders": 0,
                "total_estimated_savings_minutes": 0
            }

    if force_group:
        family_groups = {
            (order_infos[0]["product_family_id"], tuple(sorted(order_infos[0]["device_types"]))): order_infos
        }
    else:
        family_groups = group_orders_by_family_and_device(db, order_infos)

    results = []
    total_scheduled = 0
    total_failed = 0
    total_savings = 0

    for (family_id, device_types_tuple), group_infos in family_groups.items():
        if len(group_infos) < 2:
            for info in group_infos:
                _clear_existing_schedule_for_order(db, info["order_id"])
                result = schedule_order(db, info["order"], respect_locked=True)
                if result.get("success"):
                    total_scheduled += 1
                else:
                    total_failed += 1
            continue

        family = None
        if family_id:
            family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()

        device_type_str = device_types_tuple[0] if device_types_tuple else None
        if not device_type_str:
            for info in group_infos:
                _clear_existing_schedule_for_order(db, info["order_id"])
                result = schedule_order(db, info["order"], respect_locked=True)
                if result.get("success"):
                    total_scheduled += 1
                else:
                    total_failed += 1
            continue

        devices = db.query(Device).filter(
            Device.device_type == device_type_str
        ).all()

        best_device = None
        best_savings = 0

        for device in devices:
            orders_in_group = [info["order"] for info in group_infos]
            current_co = calculate_current_changeover_for_orders(
                db, orders_in_group, device.id
            )
            grouped_co = calculate_grouped_changeover_for_orders(
                db, orders_in_group, device.id
            )
            savings = current_co - grouped_co
            if savings > best_savings:
                best_savings = savings
                best_device = device

        if not best_device:
            best_device = devices[0] if devices else None

        group_code = _generate_group_code()
        schedule_group = ScheduleGroup(
            group_code=group_code,
            product_family_id=family_id,
            device_id=best_device.id if best_device else None,
            group_type="forced" if force_group else "auto",
            is_forced=force_group,
            status="active",
            estimated_savings_minutes=best_savings,
            scenario_id=scenario_id,
        )
        db.add(schedule_group)
        db.flush()

        sorted_group_infos = sorted(
            group_infos,
            key=lambda x: (-x["priority"], x["expected_start_time"])
        )

        scheduled_ids = []
        failed_ids = []
        first_product_name = None

        for idx, info in enumerate(sorted_group_infos):
            order = db.query(WorkOrder).filter(WorkOrder.id == info["order_id"]).first()
            if not order:
                failed_ids.append(info["order_id"])
                continue

            _clear_existing_schedule_for_order(db, info["order_id"])

            if idx == 0:
                first_product_name = order.product_name

            result = schedule_order(db, order, respect_locked=True)

            if result.get("success"):
                scheduled_ids.append(info["order_id"])
                entries = db.query(ScheduleEntry).filter(
                    ScheduleEntry.order_id == info["order_id"]
                ).all()
                for entry in entries:
                    entry.group_id = schedule_group.id
                db.flush()
            else:
                failed_ids.append(info["order_id"])

        actual_scheduled_count = len(scheduled_ids)
        if actual_scheduled_count < 2:
            remaining_entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.group_id == schedule_group.id
            ).all()
            for entry in remaining_entries:
                entry.group_id = None
            db.delete(schedule_group)
            db.flush()
            schedule_group_id = None
            group_code_result = None
        else:
            schedule_group_id = schedule_group.id
            group_code_result = group_code
            total_savings += best_savings

        total_scheduled += len(scheduled_ids)
        total_failed += len(failed_ids)

        results.append({
            "group_id": schedule_group_id,
            "group_code": group_code_result,
            "success": len(scheduled_ids) > 0,
            "message": f"成功排产 {len(scheduled_ids)} 张工单，失败 {len(failed_ids)} 张" if scheduled_ids else "组内所有工单排产失败",
            "order_ids": [info["order_id"] for info in group_infos],
            "scheduled_order_ids": scheduled_ids,
            "failed_order_ids": failed_ids,
            "estimated_savings_minutes": best_savings if schedule_group_id else 0,
            "actual_savings_minutes": best_savings if schedule_group_id else None,
        })

    db.commit()

    return {
        "success": True,
        "message": f"成组排产完成：成功 {total_scheduled} 张，失败 {total_failed} 张，预计节省 {total_savings} 分钟换型时间",
        "results": results,
        "total_scheduled_orders": total_scheduled,
        "total_failed_orders": total_failed,
        "total_estimated_savings_minutes": total_savings,
    }


def force_group_existing_orders(
    db: Session,
    order_ids: List[int],
    created_by: Optional[str] = None
) -> Dict:
    if len(order_ids) < 2:
        return {
            "success": False,
            "message": "强制成组至少需要2张工单"
        }

    order_infos = get_orders_with_product_family(db, order_ids)

    if len(order_infos) != len(order_ids):
        return {
            "success": False,
            "message": "部分工单不存在"
        }

    scheduled_entries_exist = all(
        len(info["order"].schedule_entries) > 0
        for info in order_infos
    )

    if not scheduled_entries_exist:
        return {
            "success": False,
            "message": "部分工单尚未排产，请先排产再强制成组"
        }

    family_ids = set(info["product_family_id"] for info in order_infos)
    if None in family_ids and len(order_infos) > 1:
        family_ids.discard(None)
        if len(family_ids) > 0:
            return {
                "success": False,
                "message": "存在未归属产品族的工单，无法强制成组"
            }
    if len(family_ids) > 1:
        return {
            "success": False,
            "message": "工单不属于同一产品族，无法强制成组"
        }

    entries_by_device = {}
    for info in order_infos:
        for entry in info["order"].schedule_entries:
            device_id = entry.device_id
            if device_id not in entries_by_device:
                entries_by_device[device_id] = []
            entries_by_device[device_id].append(entry)

    target_device_id = None
    max_entries = 0
    for device_id, entries in entries_by_device.items():
        unique_orders = set(e.order_id for e in entries)
        if len(unique_orders) > max_entries:
            max_entries = len(unique_orders)
            target_device_id = device_id

    if target_device_id is None:
        return {
            "success": False,
            "message": "无法确定成组目标设备"
        }

    existing_group_ids = set()
    for info in order_infos:
        for entry in info["order"].schedule_entries:
            if entry.group_id:
                existing_group_ids.add(entry.group_id)

    for gid in existing_group_ids:
        existing_group = db.query(ScheduleGroup).filter(ScheduleGroup.id == gid).first()
        if existing_group:
            other_entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.group_id == gid,
                ScheduleEntry.order_id.notin_(order_ids)
            ).all()
            if other_entries:
                new_group_code = _generate_group_code()
                new_group = ScheduleGroup(
                    group_code=new_group_code,
                    product_family_id=existing_group.product_family_id,
                    device_id=existing_group.device_id,
                    group_type=existing_group.group_type,
                    is_forced=existing_group.is_forced,
                    status="active",
                    estimated_savings_minutes=0,
                    created_by=created_by,
                )
                db.add(new_group)
                db.flush()
                for e in other_entries:
                    e.group_id = new_group.id

    group_code = _generate_group_code()
    family_id = order_infos[0]["product_family_id"]

    new_group = ScheduleGroup(
        group_code=group_code,
        product_family_id=family_id,
        device_id=target_device_id,
        group_type="forced",
        is_forced=True,
        status="active",
        estimated_savings_minutes=0,
        created_by=created_by,
    )
    db.add(new_group)
    db.flush()

    for info in order_infos:
        for entry in info["order"].schedule_entries:
            entry.group_id = new_group.id

    db.commit()
    db.refresh(new_group)

    return {
        "success": True,
        "message": f"成功将 {len(order_ids)} 张工单强制编成组 {group_code}",
        "group_id": new_group.id,
        "group_code": group_code,
    }


def ungroup_orders(
    db: Session,
    group_id: Optional[int] = None,
    order_ids: Optional[List[int]] = None
) -> Dict:
    if group_id is None and order_ids is None:
        return {
            "success": False,
            "message": "必须指定 group_id 或 order_ids"
        }

    affected_groups = set()

    if group_id:
        group = db.query(ScheduleGroup).filter(ScheduleGroup.id == group_id).first()
        if not group:
            return {
                "success": False,
                "message": f"组 ID {group_id} 不存在"
            }

        entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.group_id == group_id
        ).all()

        for entry in entries:
            entry.group_id = None

        affected_groups.add(group_id)
        db.delete(group)

    elif order_ids:
        entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id.in_(order_ids),
            ScheduleEntry.group_id.isnot(None)
        ).all()

        for entry in entries:
            if entry.group_id:
                affected_groups.add(entry.group_id)
            entry.group_id = None

        for gid in list(affected_groups):
            remaining = db.query(ScheduleEntry).filter(
                ScheduleEntry.group_id == gid
            ).count()
            if remaining < 2:
                group = db.query(ScheduleGroup).filter(ScheduleGroup.id == gid).first()
                if group:
                    remaining_entries = db.query(ScheduleEntry).filter(
                        ScheduleEntry.group_id == gid
                    ).all()
                    for e in remaining_entries:
                        e.group_id = None
                    db.delete(group)
            else:
                group = db.query(ScheduleGroup).filter(ScheduleGroup.id == gid).first()
                if group:
                    group.group_code = _generate_group_code()

    db.commit()

    return {
        "success": True,
        "message": f"已解散 {len(affected_groups)} 个成组",
        "affected_group_count": len(affected_groups),
    }


def list_groups(
    db: Session,
    device_id: Optional[int] = None,
    product_family_id: Optional[int] = None,
    status: Optional[str] = None,
    is_forced: Optional[bool] = None,
    scenario_id: Optional[int] = None
) -> Tuple[List[Dict], int]:
    query = db.query(ScheduleGroup).options(
        joinedload(ScheduleGroup.product_family),
        joinedload(ScheduleGroup.device),
        joinedload(ScheduleGroup.entries),
    )

    if device_id:
        query = query.filter(ScheduleGroup.device_id == device_id)
    if product_family_id:
        query = query.filter(ScheduleGroup.product_family_id == product_family_id)
    if status:
        query = query.filter(ScheduleGroup.status == status)
    if is_forced is not None:
        query = query.filter(ScheduleGroup.is_forced == is_forced)
    if scenario_id is not None:
        query = query.filter(ScheduleGroup.scenario_id == scenario_id)
    else:
        query = query.filter(ScheduleGroup.scenario_id.is_(None))

    groups = query.order_by(ScheduleGroup.created_at.desc()).all()

    result = []
    for group in groups:
        order_ids = list(set(e.order_id for e in group.entries if e.order_id))
        orders = db.query(WorkOrder).filter(WorkOrder.id.in_(order_ids)).all() if order_ids else []
        order_nos = [o.order_no for o in orders]

        result.append({
            "id": group.id,
            "group_code": group.group_code,
            "device_id": group.device_id,
            "device_name": group.device.name if group.device else None,
            "product_family_id": group.product_family_id,
            "product_family_name": group.product_family.name if group.product_family else None,
            "group_type": group.group_type,
            "is_forced": group.is_forced,
            "status": group.status,
            "estimated_savings_minutes": group.estimated_savings_minutes,
            "actual_savings_minutes": group.estimated_savings_minutes,
            "created_by": group.created_by,
            "created_at": group.created_at,
            "order_ids": order_ids,
            "order_nos": order_nos,
            "entry_count": len(group.entries),
        })

    return result, len(result)


def get_group_detail(
    db: Session,
    group_id: int
) -> Optional[Dict]:
    group = db.query(ScheduleGroup).options(
        joinedload(ScheduleGroup.product_family),
        joinedload(ScheduleGroup.device),
        joinedload(ScheduleGroup.entries).joinedload(ScheduleEntry.order),
        joinedload(ScheduleGroup.entries).joinedload(ScheduleEntry.sub_batch),
    ).filter(ScheduleGroup.id == group_id).first()

    if not group:
        return None

    order_ids = list(set(e.order_id for e in group.entries if e.order_id))
    orders = db.query(WorkOrder).filter(WorkOrder.id.in_(order_ids)).all() if order_ids else []
    order_nos = [o.order_no for o in orders]

    group_dict = {
        "id": group.id,
        "group_code": group.group_code,
        "device_id": group.device_id,
        "device_name": group.device.name if group.device else None,
        "product_family_id": group.product_family_id,
        "product_family_name": group.product_family.name if group.product_family else None,
        "group_type": group.group_type,
        "is_forced": group.is_forced,
        "status": group.status,
        "estimated_savings_minutes": group.estimated_savings_minutes,
        "actual_savings_minutes": group.estimated_savings_minutes,
        "created_by": group.created_by,
        "created_at": group.created_at,
        "order_ids": order_ids,
        "order_nos": order_nos,
        "entry_count": len(group.entries),
    }

    return {
        "group": group_dict,
        "entries": group.entries,
    }


def find_group_tail_for_insertion(
    db: Session,
    new_order: WorkOrder,
    device_id: int
) -> Optional[ScheduleGroup]:
    new_family_id = get_product_family_id(db, new_order.product_name)
    if new_family_id is None:
        return None

    groups = db.query(ScheduleGroup).options(
        joinedload(ScheduleGroup.entries)
    ).filter(
        ScheduleGroup.device_id == device_id,
        ScheduleGroup.product_family_id == new_family_id,
        ScheduleGroup.status == "active",
    ).all()

    if not groups:
        return None

    best_group = None
    best_end_time = None

    for group in groups:
        if not group.entries:
            continue
        max_end = max(e.end_time for e in group.entries)
        if best_end_time is None or max_end > best_end_time:
            best_end_time = max_end
            best_group = group

    return best_group


def add_order_to_group(
    db: Session,
    group_id: int,
    order_id: int
) -> bool:
    group = db.query(ScheduleGroup).filter(ScheduleGroup.id == group_id).first()
    if not group:
        return False

    entries = db.query(ScheduleEntry).filter(
        ScheduleEntry.order_id == order_id
    ).all()

    for entry in entries:
        entry.group_id = group_id

    db.flush()
    return True
