import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry, Device
from app.scheduler import (
    schedule_order, 
    reschedule_unlocked_orders,
    get_overlapping_orders_for_order,
    get_order_first_step_start_time,
    release_material_locks_for_order,
    release_fixtures_for_order,
    delete_outsourcing_entries_for_order,
    reschedule_orders_by_priority
)
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload

def print_schedule(db, title):
    print(f"\n--- {title} ---")
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.scenario_id.is_(None)
    ).order_by(ScheduleEntry.device_id, ScheduleEntry.start_time).all()
    
    for e in entries:
        print(f"  设备{e.device_id}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
              f"{e.step_name:6s} {e.order.order_no:12s} pri={e.order.priority} locked={e.order.is_locked}")

def main():
    db = SessionLocal()
    try:
        print("=" * 70)
        print("插单详细调试")
        print("=" * 70)
        
        # 清理
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        
        base = datetime(2026, 6, 17, 8, 0, 0)
        
        # 创建3个工单
        orders_data = [
            ("LOW-01", 2),
            ("MID-01", 5),
            ("HIGH-01", 8),
        ]
        
        orders = []
        for order_no, pri in orders_data:
            o = WorkOrder(
                order_no=order_no,
                product_name="产品A",
                expected_start_time=base,
                deadline=base + timedelta(hours=8),
                status="pending",
                is_locked=False,
                priority=pri,
                total_quantity=1
            )
            db.add(o)
            db.commit()
            db.refresh(o)
            schedule_order(db, o, respect_locked=False)
            orders.append(o)
        
        # 全量重排
        reschedule_unlocked_orders(db)
        
        print_schedule(db, "初始状态（全量重排后）")
        
        # 找到要插单的工单
        insert_order = next(o for o in orders if o.order_no == "LOW-01")
        new_priority = 10
        old_priority = insert_order.priority
        
        print(f"\n=== 开始插单: {insert_order.order_no} pri={old_priority} -> {new_priority} ===")
        
        # 模拟插单步骤
        order_id = insert_order.id
        
        # 步骤1: 修改优先级
        insert_order.priority = new_priority
        db.flush()
        print(f"\n步骤1: 修改优先级为 {new_priority}")
        
        # 步骤2: 删除旧排产
        old_entries = db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order_id).all()
        print(f"步骤2: 删除旧排产 ({len(old_entries)} 条)")
        
        release_material_locks_for_order(db, order_id)
        release_fixtures_for_order(db, order_id)
        delete_outsourcing_entries_for_order(db, order_id)
        
        db.query(ScheduleEntry).filter(ScheduleEntry.order_id == order_id).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.id == order_id).update(
            {"is_split": False, "total_sub_batches": 0, "is_blocked": False, "blocked_reason": None}
        )
        db.flush()
        
        print_schedule(db, "删除旧排产后")
        
        # 步骤3: 重新排产（用 respect_locked=True）
        print(f"\n步骤3: 重新排产（respect_locked=True）")
        order = db.query(WorkOrder).filter(WorkOrder.id == order_id).first()
        result = schedule_order(db, order, respect_locked=True)
        print(f"  排产结果: {'成功' if result['success'] else '失败'} - {result['message'][:80]}")
        
        print_schedule(db, "重新排产后")
        
        # 步骤4: 找重叠的工单
        print(f"\n步骤4: 查找重叠的低优先级工单（threshold_priority={new_priority}）")
        
        overlaps = get_overlapping_orders_for_order(db, order_id, threshold_priority=new_priority)
        print(f"  找到 {len(overlaps)} 个重叠工单:")
        for o in overlaps:
            first_start = get_order_first_step_start_time(db, o.id)
            print(f"    - {o.order_no} (pri={o.priority}, 首工序: {first_start})")
        
        if not overlaps:
            print("\n  ❗ 居然没有找到重叠工单！让我手动检查...")
            
            # 手动检查
            target_entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == order_id,
                ScheduleEntry.scenario_id.is_(None)
            ).all()
            
            print(f"\n  目标工单 {order.order_no} 的排产条目:")
            for te in target_entries:
                print(f"    设备{te.device_id}: {te.start_time.strftime('%H:%M')}-{te.end_time.strftime('%H:%M')}")
                
                # 查找同一设备上的其他条目
                other_entries = db.query(ScheduleEntry).options(
                    joinedload(ScheduleEntry.order)
                ).filter(
                    ScheduleEntry.device_id == te.device_id,
                    ScheduleEntry.scenario_id.is_(None),
                    ScheduleEntry.order_id != order_id
                ).all()
                
                print(f"    同一设备上的其他条目 ({len(other_entries)} 个):")
                for oe in other_entries:
                    target_start = te.changeover_start_time if te.changeover_start_time else te.start_time
                    target_end = te.end_time
                    entry_start = oe.changeover_start_time if oe.changeover_start_time else oe.start_time
                    entry_end = oe.end_time
                    
                    is_overlap = entry_start < target_end and entry_end > target_start
                    pri_ok = oe.order.priority < new_priority
                    locked_ok = not oe.order.is_locked
                    
                    print(f"      {oe.order.order_no} (pri={oe.order.priority}, locked={oe.order.is_locked}): "
                          f"{entry_start.strftime('%H:%M')}-{entry_end.strftime('%H:%M')} "
                          f"重叠={is_overlap}, 优先级OK={pri_ok}, 未锁定OK={locked_ok}")
                    
                    if is_overlap and pri_ok and locked_ok:
                        print(f"        ✓ 应该被包含在结果中！")
        
        # 步骤5: 级联重排
        if overlaps:
            print(f"\n步骤5: 级联重排 {len(overlaps)} 个工单")
            cascade_result = reschedule_orders_by_priority(db, overlaps)
            print(f"  延迟: {len(cascade_result['delayed'])} 个")
            print(f"  受阻: {len(cascade_result['blocked'])} 个")
            
            print_schedule(db, "级联重排后")
        
        print("\n" + "=" * 70)
        print("调试完成")
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
