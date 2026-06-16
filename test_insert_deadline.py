import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry
from app.scheduler import (
    schedule_order,
    reschedule_unlocked_orders,
    insert_order_with_priority
)
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import joinedload

def check_conflicts(db):
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.scenario_id.is_(None)
    ).all()
    
    device_entries = defaultdict(list)
    for e in entries:
        device_entries[e.device_id].append(e)
    
    conflicts = []
    for device_id, dev_entries in device_entries.items():
        dev_entries.sort(key=lambda x: x.start_time)
        for i in range(len(dev_entries)-1):
            e1 = dev_entries[i]
            e2 = dev_entries[i+1]
            if e1.end_time > e2.start_time and e1.order_id != e2.order_id:
                conflicts.append({
                    'device_id': device_id,
                    'e1': e1,
                    'e2': e2
                })
    return conflicts

def main():
    db = SessionLocal()
    try:
        print("=" * 70)
        print("插单后截止时间受阻测试")
        print("=" * 70)
        
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        
        base = datetime(2026, 6, 17, 8, 0, 0)
        
        print("\n1. 创建3个工单...")
        print("   - HIGH-01 (pri=8): 截止时间宽松")
        print("   - MID-01 (pri=5): 截止时间紧")  
        print("   - LOW-01 (pri=2): 待插单")
        
        orders_data = [
            ("HIGH-01", 8, base + timedelta(hours=8)),
            ("MID-01", 5, base + timedelta(hours=2, minutes=15)),  # 2.25小时，第二个工单肯定超
            ("LOW-01", 2, base + timedelta(hours=8)),
        ]
        
        orders = []
        for order_no, pri, deadline in orders_data:
            o = WorkOrder(
                order_no=order_no,
                product_name="产品A",
                expected_start_time=base,
                deadline=deadline,
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
        
        reschedule_unlocked_orders(db)
        
        print("\n2. 初始排产状态:")
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).order_by(ScheduleEntry.start_time).all()
        
        for e in entries:
            print(f"   设备{e.device_id}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
                  f"{e.step_name:6s} {e.order.order_no:8s} pri={e.order.priority} "
                  f"blocked={e.order.is_blocked}")
        
        conflicts = check_conflicts(db)
        print(f"\n   冲突数: {len(conflicts)}")
        
        print("\n3. 截止时间检查:")
        for o in orders:
            db.refresh(o)
            if o.schedule_entries:
                last_end = max(e.end_time for e in o.schedule_entries)
                over = last_end > o.deadline
                print(f"   {o.order_no}: 结束={last_end.strftime('%H:%M')}, "
                      f"截止={o.deadline.strftime('%H:%M')}, 超期={over}, "
                      f"blocked={o.is_blocked}, status={o.status}")
        
        print(f"\n4. 执行插单 - 把 LOW-01 (pri=2) 提到 pri=10...")
        
        low_order = next(o for o in orders if o.order_no == "LOW-01")
        
        result = insert_order_with_priority(
            db,
            order_id=low_order.id,
            new_priority=10,
            operator="test",
            reason="测试插单"
        )
        
        print(f"\n   插单结果:")
        print(f"     成功: {result['success']}")
        print(f"     消息: {result['message']}")
        print(f"     延迟工单: {result['delayed_count']} 个")
        print(f"     受阻工单: {result['blocked_count']} 个")
        
        if result['affected_orders']:
            print(f"\n   受影响工单列表:")
            for ao in result['affected_orders']:
                print(f"     • {ao['order_no']}: {ao['impact_type']}", end="")
                if ao['impact_type'] == 'delayed':
                    print(f" - 延迟 {ao['delay_minutes']} 分钟")
                else:
                    print(f" - {ao.get('blocked_reason', '未知原因')}")
        
        print("\n5. 插单后排产状态:")
        entries2 = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).order_by(ScheduleEntry.start_time).all()
        
        for e in entries2:
            print(f"   设备{e.device_id}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
                  f"{e.step_name:6s} {e.order.order_no:8s} pri={e.order.priority} "
                  f"blocked={e.order.is_blocked}")
        
        conflicts2 = check_conflicts(db)
        print(f"\n   冲突数: {len(conflicts2)}")
        
        print("\n6. 插单后截止时间检查:")
        for o in orders:
            db.refresh(o)
            if o.schedule_entries:
                last_end = max(e.end_time for e in o.schedule_entries)
                over = last_end > o.deadline
                status_str = "✅ 正常" if not over else ("⚠️ 超期已标记受阻" if o.is_blocked else "❌ 超期未标记受阻!")
                print(f"   {o.order_no}: 结束={last_end.strftime('%H:%M')}, "
                      f"截止={o.deadline.strftime('%H:%M')}, 超期={over}, "
                      f"blocked={o.is_blocked} {status_str}")
            else:
                print(f"   {o.order_no}: 无排产, blocked={o.is_blocked}, status={o.status}")
        
        mid_order = next(o for o in orders if o.order_no == "MID-01")
        db.refresh(mid_order)
        
        if mid_order.is_blocked:
            print(f"\n   ✅ MID-01 已正确标记为受阻")
            print(f"     原因: {mid_order.blocked_reason}")
        else:
            print(f"\n   ❌ MID-01 超期但未标记为受阻!")
        
        print("\n" + "=" * 70)
        print("测试完成")
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
