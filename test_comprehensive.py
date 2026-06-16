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
        print("=" * 70)
        print("综合验证测试")
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
        
        print("\n✅ 测试1: 初始排产是否考虑已有工单（无冲突）")
        print("-" * 70)
        
        # 用 respect_locked=False 创建工单，模拟 API 行为
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
        result1 = schedule_order(db, order1, respect_locked=False)
        
        order2 = WorkOrder(
            order_no="LOW-001",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(hours=4),
            status="pending",
            is_locked=False,
            priority=3,
            total_quantity=1
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)
        result2 = schedule_order(db, order2, respect_locked=False)
        
        # 检查冲突
        entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).all()
        
        device_entries = {}
        for e in entries:
            if e.device_id not in device_entries:
                device_entries[e.device_id] = []
            device_entries[e.device_id].append(e)
        
        conflicts = 0
        for dev_id, dev_entries in device_entries.items():
            dev_entries.sort(key=lambda x: x.start_time)
            for i in range(len(dev_entries)-1):
                if dev_entries[i].end_time > dev_entries[i+1].start_time:
                    conflicts += 1
        
        print(f"   创建2个工单后冲突数: {conflicts}")
        if conflicts == 0:
            print("   ✅ 初始排产无冲突")
        else:
            print("   ❌ 初始排产有冲突")
        
        # 检查顺序
        first_step_1 = min(e.start_time for e in order1.schedule_entries)
        first_step_2 = min(e.start_time for e in order2.schedule_entries)
        
        print(f"\n   工单 HIGH-001 (pri=8) 首工序: {first_step_1.strftime('%H:%M')}")
        print(f"   工单 LOW-001 (pri=3) 首工序: {first_step_2.strftime('%H:%M')}")
        
        if first_step_1 < first_step_2:
            print("   ✅ 高优先级排在前面")
        else:
            print("   ❌ 优先级顺序不对")
        
        print("\n✅ 测试2: 全量重排后优先级顺序")
        print("-" * 70)
        
        reschedule_unlocked_orders(db)
        
        db.refresh(order1)
        db.refresh(order2)
        
        first_step_1_new = min(e.start_time for e in order1.schedule_entries) if order1.schedule_entries else None
        first_step_2_new = min(e.start_time for e in order2.schedule_entries) if order2.schedule_entries else None
        
        if first_step_1_new and first_step_2_new:
            print(f"   工单 HIGH-001 (pri=8) 首工序: {first_step_1_new.strftime('%H:%M')}")
            print(f"   工单 LOW-001 (pri=3) 首工序: {first_step_2_new.strftime('%H:%M')}")
            
            if first_step_1_new < first_step_2_new:
                print("   ✅ 重排后高优先级排在前面")
            else:
                print("   ❌ 重排后优先级顺序不对")
        else:
            print("   有些工单排产失败了")
        
        # 再检查冲突
        entries2 = db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).all()
        
        device_entries2 = {}
        for e in entries2:
            if e.device_id not in device_entries2:
                device_entries2[e.device_id] = []
            device_entries2[e.device_id].append(e)
        
        conflicts2 = 0
        for dev_id, dev_entries in device_entries2.items():
            dev_entries.sort(key=lambda x: x.start_time)
            for i in range(len(dev_entries)-1):
                if dev_entries[i].end_time > dev_entries[i+1].start_time:
                    conflicts2 += 1
        
        print(f"\n   重排后冲突数: {conflicts2}")
        if conflicts2 == 0:
            print("   ✅ 重排后无冲突")
        else:
            print("   ❌ 重排后有冲突")
        
        print("\n✅ 测试3: 截止时间超期是否标记受阻")
        print("-" * 70)
        
        # 创建一个截止时间非常短的工单
        tight_order = WorkOrder(
            order_no="TIGHT-001",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(minutes=30),  # 只有30分钟，肯定不够
            status="pending",
            is_locked=False,
            priority=5,
            total_quantity=1
        )
        db.add(tight_order)
        db.commit()
        db.refresh(tight_order)
        
        result_tight = schedule_order(db, tight_order, respect_locked=False)
        
        print(f"   工单 TIGHT-001 (30分钟截止):")
        print(f"     排产结果: {'成功' if result_tight['success'] else '失败'}")
        print(f"     status: {tight_order.status}")
        print(f"     is_blocked: {tight_order.is_blocked}")
        print(f"     blocked_reason: {tight_order.blocked_reason}")
        
        if not result_tight['success'] and tight_order.is_blocked:
            print("   ✅ 超截止时间正确标记为受阻")
        elif result_tight['success']:
            print("   ❌ 超截止时间居然排产成功了")
        else:
            print("   ❌ 排产失败但未标记为受阻")
        
        print("\n✅ 测试4: 更多工单验证优先级排序")
        print("-" * 70)
        
        # 先清理
        db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).delete(
            synchronize_session=False
        )
        db.commit()
        
        # 创建5个不同优先级的工单（乱序创建）
        test_orders = [
            ("ORDER-01", 5),
            ("ORDER-02", 2),
            ("ORDER-03", 8),
            ("ORDER-04", 3),
            ("ORDER-05", 7),
        ]
        
        created = []
        for order_no, pri in test_orders:
            o = WorkOrder(
                order_no=order_no,
                product_name="产品A",
                expected_start_time=base,
                deadline=base + timedelta(hours=10),
                status="pending",
                is_locked=False,
                priority=pri,
                total_quantity=1
            )
            db.add(o)
            db.commit()
            db.refresh(o)
            schedule_order(db, o, respect_locked=False)
            created.append(o)
        
        # 全量重排
        reschedule_unlocked_orders(db)
        
        # 获取每个工单的首工序时间
        order_times = []
        for o in created:
            db.refresh(o)
            if o.schedule_entries:
                first_start = min(e.start_time for e in o.schedule_entries)
                order_times.append((o.priority, o.order_no, first_start))
        
        order_times.sort(key=lambda x: x[2])
        
        print("   按首工序时间排序:")
        prev_priority = None
        priority_correct = True
        for pri, order_no, start in order_times:
            marker = ""
            if prev_priority is not None and pri > prev_priority:
                marker = " ⚠️ 低优先级在前"
                priority_correct = False
            print(f"     {start.strftime('%H:%M')} - {order_no} (pri={pri}){marker}")
            prev_priority = pri
        
        if priority_correct:
            print("   ✅ 优先级排序正确（高优先级在前）")
        else:
            print("   ❌ 优先级排序有问题")
        
        # 检查冲突
        all_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).all()
        
        dev_entries_map = {}
        for e in all_entries:
            if e.device_id not in dev_entries_map:
                dev_entries_map[e.device_id] = []
            dev_entries_map[e.device_id].append(e)
        
        total_conflicts = 0
        for dev_id, dev_entries in dev_entries_map.items():
            dev_entries.sort(key=lambda x: x.start_time)
            for i in range(len(dev_entries)-1):
                if dev_entries[i].end_time > dev_entries[i+1].start_time and \
                   dev_entries[i].order_id != dev_entries[i+1].order_id:
                    total_conflicts += 1
        
        print(f"\n   总冲突数: {total_conflicts}")
        if total_conflicts == 0:
            print("   ✅ 无冲突")
        else:
            print("   ❌ 有冲突")
        
        print("\n" + "=" * 70)
        print("所有测试完成")
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
