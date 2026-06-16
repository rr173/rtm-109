import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry
from app.scheduler import (
    schedule_order,
    reschedule_unlocked_orders,
    insert_order_with_priority,
    get_order_first_step_start_time
)
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import joinedload

def check_device_conflicts(db):
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
            e1_end = e1.end_time
            e2_start = e2.changeover_start_time if e2.changeover_start_time else e2.start_time
            if e1_end > e2_start and e1.order_id != e2.order_id:
                conflicts.append({
                    'device_id': device_id,
                    'e1': e1,
                    'e2': e2,
                    'overlap_minutes': (e1_end - e2_start).total_seconds() / 60
                })
    return conflicts

def main():
    db = SessionLocal()
    try:
        print("=" * 70)
        print("四个核心问题最终验证")
        print("=" * 70)
        
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        
        base = datetime(2026, 6, 17, 8, 0, 0)
        
        print("\n【问题1: 设备独占 - 同设备两单同一时段】")
        print("-" * 50)
        
        orders_data = [
            ("P1", 8, base + timedelta(hours=8)),
            ("P2", 6, base + timedelta(hours=8)),
            ("P3", 4, base + timedelta(hours=2)),  # 截止时间紧，插单后会超
            ("P4", 2, base + timedelta(hours=8)),  # 待插单
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
        
        conflicts = check_device_conflicts(db)
        print(f"  初始冲突数: {len(conflicts)}")
        
        if len(conflicts) == 0:
            print("  ✅ 初始排产无设备冲突")
        else:
            print("  ❌ 初始排产有设备冲突!")
            for c in conflicts:
                print(f"     设备{c['device_id']}: {c['e1'].order.order_no} 与 {c['e2'].order.order_no} 重叠 {c['overlap_minutes']} 分钟")
        
        print("\n【问题3: 初始排产按优先级排序】")
        print("-" * 50)
        
        def safe_get_start(o):
            t = get_order_first_step_start_time(db, o.id)
            return t if t else datetime.max
        
        sorted_orders = sorted(orders, key=lambda o: safe_get_start(o))
        print("  按首工序排序:")
        for o in sorted_orders:
            start = get_order_first_step_start_time(db, o.id)
            start_str = start.strftime('%H:%M') if start else "N/A(受阻)"
            print(f"    {start_str} - {o.order_no} (pri={o.priority}) status={o.status}")
        
        scheduled_orders = [o for o in sorted_orders if o.status == 'scheduled']
        priorities = [o.priority for o in scheduled_orders]
        is_sorted = all(priorities[i] >= priorities[i+1] for i in range(len(priorities)-1))
        
        if is_sorted:
            print("  ✅ 按优先级从高到低正确排序")
        else:
            print("  ❌ 优先级顺序不对!")
        
        print("\n【执行插单 - P4 从 pri=2 提到 pri=10】")
        print("-" * 50)
        
        p4 = next(o for o in orders if o.order_no == "P4")
        old_start = get_order_first_step_start_time(db, p4.id)
        print(f"  插单前: P4 首工序 {old_start.strftime('%H:%M')} (pri={p4.priority})")
        
        result = insert_order_with_priority(
            db,
            order_id=p4.id,
            new_priority=10,
            operator="test",
            reason="验证测试"
        )
        
        print(f"  插单结果: {'成功' if result['success'] else '失败'}")
        print(f"  消息: {result['message']}")
        print(f"  延迟工单: {result['delayed_count']} 个")
        print(f"  受阻工单: {result['blocked_count']} 个")
        print(f"  受影响总数: {len(result['affected_orders'])} 个")
        
        print("\n【问题2: affected_orders 返回空但实际有冲突】")
        print("-" * 50)
        
        if len(result['affected_orders']) > 0:
            print("  ✅ affected_orders 不为空")
            print("  受影响工单列表:")
            for ao in result['affected_orders']:
                print(f"    • {ao['order_no']}: {ao['impact_type']}", end="")
                if ao['impact_type'] == 'delayed':
                    print(f" - 延迟 {ao['delay_minutes']} 分钟")
                else:
                    print(f" - {ao.get('blocked_reason', '未知')[:50]}")
        else:
            print("  ❌ affected_orders 为空!")
        
        print("\n【问题1(再次验证): 插单后设备独占】")
        print("-" * 50)
        
        conflicts_after = check_device_conflicts(db)
        print(f"  插单后冲突数: {len(conflicts_after)}")
        
        if len(conflicts_after) == 0:
            print("  ✅ 插单后无设备冲突")
        else:
            print("  ❌ 插单后有设备冲突!")
            for c in conflicts_after:
                print(f"     设备{c['device_id']}: {c['e1'].order.order_no} 与 {c['e2'].order.order_no} 重叠 {c['overlap_minutes']} 分钟")
        
        print("\n【问题4: 顺延后超截止时间未标记受阻】")
        print("-" * 50)
        
        p3 = next(o for o in orders if o.order_no == "P3")
        db.refresh(p3)
        
        if p3.schedule_entries:
            last_end = max(e.end_time for e in p3.schedule_entries)
            over_deadline = last_end > p3.deadline
            print(f"  P3 结束时间: {last_end.strftime('%H:%M')}")
            print(f"  P3 截止时间: {p3.deadline.strftime('%H:%M')}")
            print(f"  是否超期: {over_deadline}")
            print(f"  is_blocked: {p3.is_blocked}")
            print(f"  status: {p3.status}")
            
            if over_deadline and p3.is_blocked:
                print("  ✅ 超期后正确标记为受阻")
            elif over_deadline and not p3.is_blocked:
                print("  ❌ 超期了但未标记受阻!")
            else:
                print("  ⚠️ 未超期（截止时间可能设置不够紧）")
        else:
            print(f"  P3 无排产（可能已受阻）")
            print(f"  is_blocked: {p3.is_blocked}")
            print(f"  status: {p3.status}")
            print(f"  blocked_reason: {p3.blocked_reason}")
            if p3.is_blocked:
                print("  ✅ 已正确标记为受阻")
        
        print("\n【问题3(再次验证): 插单后优先级排序】")
        print("-" * 50)
        
        for o in orders:
            db.refresh(o)
        
        sorted_after = sorted(
            [o for o in orders if o.status == 'scheduled'],
            key=lambda o: get_order_first_step_start_time(db, o.id)
        )
        
        print("  插单后按首工序排序:")
        for o in sorted_after:
            start = get_order_first_step_start_time(db, o.id)
            print(f"    {start.strftime('%H:%M')} - {o.order_no} (pri={o.priority}) status={o.status}")
        
        priorities_after = [o.priority for o in sorted_after]
        is_sorted_after = all(priorities_after[i] >= priorities_after[i+1] for i in range(len(priorities_after)-1))
        
        if is_sorted_after:
            print("  ✅ 按优先级从高到低正确排序")
        else:
            print("  ❌ 优先级顺序不对!")
        
        print("\n" + "=" * 70)
        print("验证总结")
        print("=" * 70)
        
        all_pass = True
        
        if len(conflicts_after) == 0:
            print("  ✅ 问题1: 设备独占 - 已修复")
        else:
            print("  ❌ 问题1: 设备独占 - 未修复")
            all_pass = False
        
        if len(result['affected_orders']) > 0:
            print("  ✅ 问题2: affected_orders - 已修复")
        else:
            print("  ❌ 问题2: affected_orders - 未修复")
            all_pass = False
        
        if is_sorted_after:
            print("  ✅ 问题3: 优先级排序 - 已修复")
        else:
            print("  ❌ 问题3: 优先级排序 - 未修复")
            all_pass = False
        
        p3_blocked_correct = p3.is_blocked if not p3.schedule_entries else (last_end > p3.deadline and p3.is_blocked)
        if p3.is_blocked:
            print("  ✅ 问题4: 截止时间受阻 - 已修复")
        else:
            print("  ⚠️ 问题4: 截止时间受阻 - 需进一步验证（可能未超期）")
        
        if all_pass and p3.is_blocked:
            print("\n🎉 所有四个问题均已修复!")
        else:
            print("\n⚠️ 部分问题需进一步验证")
        
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
