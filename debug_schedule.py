import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry, Device
from app.scheduler import schedule_order, reschedule_unlocked_orders
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload

def main():
    db = SessionLocal()
    try:
        print("=" * 60)
        print("调试测试 - 为什么初始排产有冲突？")
        print("=" * 60)
        
        # 清理
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        
        base = datetime(2026, 6, 17, 8, 0, 0)
        
        print("\n1. 查看设备上现有排产...")
        devices = db.query(Device).all()
        for dev in devices:
            entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.device_id == dev.id,
                ScheduleEntry.scenario_id.is_(None)
            ).all()
            print(f"   设备 {dev.id} ({dev.device_type}): {len(entries)} 条记录")
        
        print("\n2. 创建第一个工单 (HIGH-001, pri=8)...")
        order1 = WorkOrder(
            order_no="HIGH-001",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(hours=4),
            status="pending",
            is_locked=False,
            priority=8,
            total_quantity=1
        )
        db.add(order1)
        db.commit()
        db.refresh(order1)
        
        result1 = schedule_order(db, order1)
        print(f"   结果: {'成功' if result1['success'] else '失败'} - {result1['message'][:80]}")
        
        entries1 = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order1.id
        ).all()
        for e in entries1:
            print(f"     设备{e.device_id}: {e.start_time} - {e.end_time} {e.step_name}")
        
        print("\n3. 查看设备1上的所有排产（排完第一个工单后）...")
        entries_dev1 = db.query(ScheduleEntry).filter(
            ScheduleEntry.device_id == 1,
            ScheduleEntry.scenario_id.is_(None)
        ).all()
        print(f"   设备1有 {len(entries_dev1)} 条记录:")
        for e in entries_dev1:
            print(f"     {e.start_time} - {e.end_time} order_id={e.order_id}")
        
        print("\n4. 创建第二个工单 (LOW-001, pri=3)...")
        order2 = WorkOrder(
            order_no="LOW-001",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(hours=2, minutes=30),
            status="pending",
            is_locked=False,
            priority=3,
            total_quantity=1
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)
        
        result2 = schedule_order(db, order2)
        print(f"   结果: {'成功' if result2['success'] else '失败'} - {result2['message'][:80]}")
        
        entries2 = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order2.id
        ).all()
        for e in entries2:
            print(f"     设备{e.device_id}: {e.start_time} - {e.end_time} {e.step_name}")
        
        print("\n5. 查看设备1上的所有排产（排完第二个工单后）...")
        entries_dev1 = db.query(ScheduleEntry).filter(
            ScheduleEntry.device_id == 1,
            ScheduleEntry.scenario_id.is_(None)
        ).order_by(ScheduleEntry.start_time).all()
        print(f"   设备1有 {len(entries_dev1)} 条记录:")
        for e in entries_dev1:
            print(f"     {e.start_time.strftime('%H:%M')} - {e.end_time.strftime('%H:%M')} "
                  f"order_id={e.order_id} order_no={e.order.order_no if e.order else 'N/A'}")
        
        print("\n6. 检查是否有冲突...")
        conflicts = 0
        for dev_id in [1, 2]:
            entries = db.query(ScheduleEntry).filter(
                ScheduleEntry.device_id == dev_id,
                ScheduleEntry.scenario_id.is_(None)
            ).order_by(ScheduleEntry.start_time).all()
            for i in range(len(entries)-1):
                if entries[i].end_time > entries[i+1].start_time:
                    conflicts += 1
                    print(f"   设备{dev_id}冲突: {entries[i].order_id} vs {entries[i+1].order_id}")
        
        print(f"   总冲突数: {conflicts}")
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
