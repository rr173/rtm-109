import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.scheduler import insert_order_with_priority, reschedule_unlocked_orders
from app.models import WorkOrder, ScheduleEntry
from datetime import datetime
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

def main():
    db = SessionLocal()
    try:
        print("=" * 60)
        print("插单功能验证测试")
        print("=" * 60)
        
        print("\n1. 先确保数据库干净（重排所有未锁定工单）...")
        reschedule_unlocked_orders(db)
        conflicts = check_conflicts(db)
        print(f"   重排后冲突数: {len(conflicts)}")
        
        print("\n2. 查找测试用的低优先级工单...")
        orders = db.query(WorkOrder).filter(
            WorkOrder.is_locked == False,
            WorkOrder.status == "scheduled",
            WorkOrder.scenario_id.is_(None),
            WorkOrder.priority < 9
        ).all()
        
        print(f"   找到 {len(orders)} 个低优先级工单")
        
        if len(orders) < 2:
            print("   低优先级工单不足，无法测试")
            return
        
        test_order = orders[1]  # 选第二个
        old_priority = test_order.priority
        print(f"   选择测试工单: {test_order.order_no} (ID={test_order.id}, priority={old_priority})")
        
        first_entry = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == test_order.id
        ).order_by(ScheduleEntry.start_time.asc()).first()
        if first_entry:
            print(f"   首工序: 设备{first_entry.device_id}, {first_entry.start_time} ~ {first_entry.end_time}")
        
        print(f"\n3. 执行插单 (priority {old_priority} → 9)...")
        result = insert_order_with_priority(
            db, 
            order_id=test_order.id, 
            new_priority=9, 
            operator="test_user", 
            reason="测试插单"
        )
        
        print(f"   成功: {result.get('success')}")
        print(f"   消息: {result.get('message')}")
        print(f"   受影响工单: {len(result.get('affected_orders', []))} 个")
        print(f"   延迟: {result.get('delayed_count')} 个, 受阻: {result.get('blocked_count')} 个")
        
        if result.get('affected_orders'):
            print("   受影响工单列表:")
            for ao in result['affected_orders']:
                print(f"     - {ao['order_no']}: {ao['impact_type']}, 延迟{ao['delay_minutes']}分钟")
        
        print("\n4. 检查插单后是否有设备冲突...")
        conflicts = check_conflicts(db)
        print(f"   冲突数: {len(conflicts)}")
        
        if conflicts:
            print("   ❌ 存在冲突:")
            for c in conflicts:
                e1 = c['e1']
                e2 = c['e2']
                print(f"     设备{c['device_id']}:")
                print(f"       {e1.order.order_no} (pri={e1.order.priority}): {e1.start_time} ~ {e1.end_time}")
                print(f"       {e2.order.order_no} (pri={e2.order.priority}): {e2.start_time} ~ {e2.end_time}")
                print(f"       重叠: {c['overlap_minutes']:.0f} 分钟")
        else:
            print("   ✅ 没有冲突！")
        
        print("\n5. 验证工单元数据...")
        db.refresh(test_order)
        print(f"   工单当前优先级: {test_order.priority}")
        print(f"   最后插单时间: {test_order.last_insertion_at}")
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
