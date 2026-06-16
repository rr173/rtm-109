import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry, Device
from app.scheduler import (
    schedule_order, 
    get_overlapping_orders_for_order
)
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload

def main():
    db = SessionLocal()
    try:
        print("=" * 60)
        print("调试 get_overlapping_orders_for_order")
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
        
        print("\n1. 创建2个重叠的工单（手动制造冲突）...")
        
        # 用 respect_locked=True 排两个，让它们重叠
        order1 = WorkOrder(
            order_no="ORDER-01",
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
        print(f"   工单 ORDER-01 (pri=8): {'成功' if result1['success'] else '失败'}")
        
        # 第二个工单用 respect_locked=True 排，让它和第一个重叠
        order2 = WorkOrder(
            order_no="ORDER-02",
            product_name="产品A",
            expected_start_time=base,
            deadline=base + timedelta(hours=4),
            status="pending",
            is_locked=False,
            priority=5,
            total_quantity=1
        )
        db.add(order2)
        db.commit()
        db.refresh(order2)
        result2 = schedule_order(db, order2, respect_locked=True)  # 只考虑锁定的，所以会重叠
        print(f"   工单 ORDER-02 (pri=5): {'成功' if result2['success'] else '失败'}")
        
        # 显示所有排产
        print("\n2. 设备上的所有排产:")
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.order)
        ).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).order_by(ScheduleEntry.device_id, ScheduleEntry.start_time).all()
        
        for e in entries:
            print(f"   设备{e.device_id}: {e.start_time.strftime('%H:%M')}-{e.end_time.strftime('%H:%M')} "
                  f"{e.step_name:6s} {e.order.order_no} (pri={e.order.priority}, locked={e.order.is_locked})")
        
        # 测试 get_overlapping_orders_for_order
        print("\n3. 测试 get_overlapping_orders_for_order:")
        
        # 从 order2 的角度找重叠的
        overlaps = get_overlapping_orders_for_order(db, order2.id, threshold_priority=10)
        print(f"   以 ORDER-02 为目标，找优先级 < 10 的重叠工单:")
        print(f"   找到 {len(overlaps)} 个:")
        for o in overlaps:
            print(f"     - {o.order_no} (pri={o.priority})")
        
        # 从 order1 的角度找重叠的
        overlaps2 = get_overlapping_orders_for_order(db, order1.id, threshold_priority=10)
        print(f"\n   以 ORDER-01 为目标，找优先级 < 10 的重叠工单:")
        print(f"   找到 {len(overlaps2)} 个:")
        for o in overlaps2:
            print(f"     - {o.order_no} (pri={o.priority})")
        
        # 手动检测重叠
        print("\n4. 手动检测重叠:")
        order1_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order1.id
        ).all()
        order2_entries = db.query(ScheduleEntry).filter(
            ScheduleEntry.order_id == order2.id
        ).all()
        
        for e1 in order1_entries:
            for e2 in order2_entries:
                if e1.device_id == e2.device_id:
                    e1_start = e1.changeover_start_time if e1.changeover_start_time else e1.start_time
                    e2_start = e2.changeover_start_time if e2.changeover_start_time else e2.start_time
                    overlap = e1_start < e2.end_time and e1.end_time > e2_start
                    print(f"   设备{e1.device_id}: ORDER-01 ({e1_start.strftime('%H:%M')}-{e1.end_time.strftime('%H:%M')}) "
                          f"vs ORDER-02 ({e2_start.strftime('%H:%M')}-{e2.end_time.strftime('%H:%M')}) "
                          f"重叠={overlap}")
        
        print("\n" + "=" * 60)
        print("调试完成")
        print("=" * 60)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
