import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry, Device
from app.scheduler import (
    insert_order_with_priority, 
    reschedule_unlocked_orders, 
    schedule_order
)
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import joinedload

def check_conflicts(db):
    device_entries = defaultdict(list)
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
        ScheduleEntry.scenario_id.is_(None)
    ).all()
    
    for e in entries:
        device_entries[e.device_id].append(e)
    
    conflicts = []
    for device_id, entries in device_entries.items():
        entries.sort(key=lambda x: x.start_time)
        for i in range(len(entries)-1):
            e1 = entries[i]
            e2 = entries[i+1]
            
            e1_end = e1.end_time
            e2_start = e2.changeover_start_time if e2.changeover_start_time else e2.start_time
            
            if e1_end > e2_start and e1.order_id != e2.order_id:
                overlap = (e1_end - e2_start).total_seconds() / 60
                conflicts.append({
                    'device_id': device_id,
                    'e1': e1,
                    'e2': e2,
                    'overlap_minutes': overlap
                })
    
    return conflicts

def create_test_order(db, order_no, priority=5, start_days=0, deadline_days=5):
    base_date = datetime(2026, 6, 17, 8, 0, 0)
    order = WorkOrder(
        order_no=order_no,
        product_name="产品A",
        expected_start_time=base_date + timedelta(days=start_days),
        deadline=base_date + timedelta(days=deadline_days),
        status="pending",
        is_locked=False,
        priority=priority,
        total_quantity=100
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    
    result = schedule_order(db, order)
    return order, result

def main():
    db = SessionLocal()
    try:
        print("=" * 70)
        print("插单与级联重排 - 综合测试")
        print("=" * 70)
        
        # 清理现有数据（保留设备等基础数据）
        print("\n1. 清理现有工单...")
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        print("   已清理")
        
        # 检查设备数量
        devices = db.query(Device).all()
        print(f"\n2. 可用设备: {len(devices)} 台")
        cnc_devices = [d for d in devices if d.device_type == 'CNC']
        test_devices = [d for d in devices if d.device_type == '检测']
        print(f"   CNC设备: {len(cnc_devices)} 台")
        print(f"   检测设备: {len(test_devices)} 台")
        
        # 创建测试工单
        print("\n3. 创建测试工单（不同优先级）...")
        
        orders_info = [
            ("LOW-001", 2, 0, 5),
            ("LOW-002", 3, 0, 5),  
            ("MID-001", 5, 0, 5),
            ("MID-002", 5, 0, 5),
            ("HIGH-001", 7, 0, 5),
        ]
        
        created_orders = []
        for order_no, pri, start_d, end_d in orders_info:
            order, result = create_test_order(db, order_no, pri, start_d, end_d)
            status = "✅" if result['success'] else "❌"
            print(f"   {status} {order_no} (priority={pri}): {result['message'][:50]}")
            created_orders.append(order)
        
        # 全量重排，验证按优先级排序
        print("\n4. 全量重排（验证优先级排序）...")
        reschedule_unlocked_orders(db)
        
        conflicts = check_conflicts(db)
        print(f"   冲突数: {len(conflicts)}")
        
        if conflicts:
            print("   ❌ 存在冲突:")
            for c in conflicts:
                print(f"     设备{c['device_id']}: {c['e1'].order.order_no} vs {c['e2'].order.order_no}")
        else:
            print("   ✅ 无冲突")
        
        # 检查排序是否按优先级
        all_scheduled = db.query(WorkOrder).filter(
            WorkOrder.status == 'scheduled',
            WorkOrder.scenario_id.is_(None)
        ).all()
        
        first_step_times = []
        for o in all_scheduled:
            first_entry = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == o.id
            ).order_by(ScheduleEntry.start_time.asc()).first()
            if first_entry:
                first_step_times.append((o.priority, o.order_no, first_entry.start_time, first_entry.device_id))
        
        first_step_times.sort(key=lambda x: x[2])
        
        print(f"\n5. 按首工序开始时间排序验证优先级:")
        prev_priority = None
        priority_correct = True
        for pri, order_no, start, dev in first_step_times:
            order_marker = ""
            if prev_priority is not None and pri > prev_priority:
                order_marker = " ⚠️ 低优先级排在高优先级前面了"
                priority_correct = False
            print(f"   {start.strftime('%H:%M')} - {order_no:10s} (pri={pri}, dev={dev}){order_marker}")
            prev_priority = pri
        
        if priority_correct:
            print("   ✅ 优先级排序正确")
        else:
            print("   ❌ 优先级排序有问题")
        
        # 测试插单功能
        print("\n6. 测试插单功能 - 把低优先级工单提到最高...")
        
        # 找一个最低优先级的工单
        low_order = min(created_orders, key=lambda o: o.priority)
        old_priority = low_order.priority
        
        print(f"   选择工单: {low_order.order_no} (当前优先级: {old_priority})")
        
        # 记录插单前的状态
        before_first = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == low_order.id
        ).order_by(ScheduleEntry.start_time.asc()).first()
        if before_first:
            print(f"   插单前首工序: 设备{before_first.device_id}, {before_first.start_time}")
        
        # 执行插单
        result = insert_order_with_priority(
            db,
            order_id=low_order.id,
            new_priority=10,
            operator="test_user",
            reason="紧急插单测试"
        )
        
        print(f"\n   插单结果:")
        print(f"     成功: {result.get('success')}")
        print(f"     消息: {result.get('message')}")
        print(f"     受影响工单: {len(result.get('affected_orders', []))} 个")
        print(f"     延迟: {result.get('delayed_count')} 个, 受阻: {result.get('blocked_count')} 个")
        
        if result.get('affected_orders'):
            print("     受影响工单列表:")
            for ao in result['affected_orders']:
                print(f"       - {ao['order_no']}: {ao['impact_type']}, 延迟{ao['delay_minutes']}分钟")
        
        # 检查插单后是否有冲突
        conflicts_after = check_conflicts(db)
        print(f"\n   插单后冲突数: {len(conflicts_after)}")
        
        if conflicts_after:
            print("   ❌ 存在冲突:")
            for c in conflicts_after:
                print(f"     设备{c['device_id']}: {c['e1'].order.order_no}(pri={c['e1'].order.priority}) "
                      f"vs {c['e2'].order.order_no}(pri={c['e2'].order.priority})")
                print(f"       {c['e1'].start_time} ~ {c['e1'].end_time}")
                print(f"       {c['e2'].start_time} ~ {c['e2'].end_time}")
                print(f"       重叠: {c['overlap_minutes']:.0f} 分钟")
        else:
            print("   ✅ 无冲突")
        
        # 验证插单后的排序
        db.refresh(low_order)
        after_first = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == low_order.id
        ).order_by(ScheduleEntry.start_time.asc()).first()
        if after_first:
            print(f"\n   插单后首工序: 设备{after_first.device_id}, {after_first.start_time}")
        
        # 检查插单后的优先级排序
        all_scheduled2 = db.query(WorkOrder).filter(
            WorkOrder.status == 'scheduled',
            WorkOrder.scenario_id.is_(None)
        ).all()
        
        first_step_times2 = []
        for o in all_scheduled2:
            first_entry = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == o.id
            ).order_by(ScheduleEntry.start_time.asc()).first()
            if first_entry:
                first_step_times2.append((o.priority, o.order_no, first_entry.start_time, first_entry.device_id))
        
        first_step_times2.sort(key=lambda x: x[2])
        
        print(f"\n7. 插单后按首工序排序:")
        for pri, order_no, start, dev in first_step_times2:
            marker = " ← 刚插单的" if order_no == low_order.order_no else ""
            print(f"   {start.strftime('%H:%M')} - {order_no:10s} (pri={pri}, dev={dev}){marker}")
        
        # 测试防抖动
        print("\n8. 测试防抖动机制...")
        result2 = insert_order_with_priority(
            db,
            order_id=low_order.id,
            new_priority=9,
            operator="test_user2",
            reason="再次插单"
        )
        if not result2.get('success') and "频繁" in result2.get('message', ''):
            print("   ✅ 防抖动生效")
        else:
            print(f"   ❌ 防抖动未生效: {result2.get('message')}")
        
        # 测试截止时间约束
        print("\n9. 测试截止时间约束（超期是否标受阻）...")
        
        # 创建一个截止时间很短的工单
        tight_order, tight_result = create_test_order(
            db, "TIGHT-001", priority=6, start_days=0, deadline_days=0.01  # 非常短的截止时间
        )
        
        if tight_result['success']:
            print(f"   工单 {tight_order.order_no} 排产成功了？检查是否超截止时间...")
            db.refresh(tight_order)
            entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == tight_order.id
            ).all()
            if entries:
                last_end = max(e.end_time for e in entries)
                print(f"   最后工序结束: {last_end}")
                print(f"   截止时间: {tight_order.deadline}")
                print(f"   状态: {tight_order.status}")
                print(f"   is_blocked: {tight_order.is_blocked}")
                print(f"   blocked_reason: {tight_order.blocked_reason}")
        else:
            print(f"   ✅ 工单创建失败: {tight_result.get('message')}")
            db.refresh(tight_order)
            print(f"   状态: {tight_order.status}")
            print(f"   is_blocked: {tight_order.is_blocked}")
        
        print("\n" + "=" * 70)
        print("测试完成")
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
