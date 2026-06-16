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
        print("截止时间与受阻标记测试")
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
        
        # 创建2个工单
        # 每工单2工序：车削60min + 检测30min = 90min/工单
        # 2工单串行需要 180min = 3小时
        
        print("\n1. 创建2个工单...")
        
        # 工单1：高优先级，截止时间宽松
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
        print(f"   工单 HIGH-001 (pri=8): {'✅ 成功' if result1['success'] else '❌ 失败'} - {result1['message'][:60]}")
        print(f"     status={order1.status}, is_blocked={order1.is_blocked}")
        
        # 工单2：低优先级，截止时间紧（2小时，串行的话第二个工单会超）
        order2 = WorkOrder(
            order_no="LOW-001",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(hours=2, minutes=30),  # 2.5小时，应该刚好不够第二个
            status="pending",
            is_locked=False,
            priority=3,
            total_quantity=1
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)
        
        result2 = schedule_order(db, order2)
        print(f"   工单 LOW-001 (pri=3): {'✅ 成功' if result2['success'] else '❌ 失败'} - {result2['message'][:60]}")
        print(f"     status={order2.status}, is_blocked={order2.is_blocked}")
        if order2.is_blocked:
            print(f"     blocked_reason: {order2.blocked_reason}")
        
        # 检查冲突
        print("\n2. 检查设备冲突...")
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(ScheduleEntry.scenario_id.is_(None)).all()
        
        device_entries = {}
        for e in entries:
            if e.device_id not in device_entries:
                device_entries[e.device_id] = []
            device_entries[e.device_id].append(e)
        
        conflicts = 0
        for dev_id, dev_entries in device_entries.items():
            dev_entries.sort(key=lambda x: x.start_time)
            print(f"   设备 {dev_id}:")
            for i, e in enumerate(dev_entries):
                prev_end = dev_entries[i-1].end_time if i > 0 else None
                overlap_mark = ""
                if i > 0 and prev_end and prev_end > e.start_time:
                    overlap_mark = " ⚠️ 冲突!"
                    conflicts += 1
                print(f"     {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
                      f"{e.step_name:6s} {e.order.order_no} (pri={e.order.priority}){overlap_mark}")
        
        print(f"\n   冲突数: {conflicts}")
        
        # 全量重排
        print("\n3. 全量重排（按优先级）...")
        reschedule_unlocked_orders(db)
        
        # 重排后检查
        db.refresh(order1)
        db.refresh(order2)
        
        print(f"   工单 HIGH-001: status={order1.status}, is_blocked={order1.is_blocked}")
        print(f"   工单 LOW-001: status={order2.status}, is_blocked={order2.is_blocked}")
        if order2.is_blocked:
            print(f"     blocked_reason: {order2.blocked_reason}")
        
        # 再次检查时间
        entries2 = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(ScheduleEntry.scenario_id.is_(None)).all()
        
        device_entries2 = {}
        for e in entries2:
            if e.device_id not in device_entries2:
                device_entries2[e.device_id] = []
            device_entries2[e.device_id].append(e)
        
        print(f"\n   重排后时间安排:")
        for dev_id, dev_entries in device_entries2.items():
            dev_entries.sort(key=lambda x: x.start_time)
            print(f"   设备 {dev_id}:")
            for e in dev_entries:
                print(f"     {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
                      f"{e.step_name:6s} {e.order.order_no} (pri={e.order.priority})")
        
        # 检查截止时间
        print("\n4. 截止时间检查...")
        for order in [order1, order2]:
            if order.status == 'scheduled' and order.schedule_entries:
                last_end = max(e.end_time for e in order.schedule_entries)
                over = last_end > order.deadline
                print(f"   {order.order_no}: 结束={last_end}, 截止={order.deadline}, 超期={over}")
                if over and not order.is_blocked:
                    print(f"     ❗ 超期了但没标受阻!")
        
        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
