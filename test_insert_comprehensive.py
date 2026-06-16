import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry, Device
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
        print("插单功能测试")
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
        
        print("\n1. 创建5个不同优先级的工单...")
        
        test_orders = [
            ("P-LOW-01", 2),
            ("P-LOW-02", 3),
            ("P-MID-01", 5),
            ("P-MID-02", 6),
            ("P-HIGH-01", 8),
        ]
        
        created = []
        for order_no, pri in test_orders:
            o = WorkOrder(
                order_no=order_no,
                product_name="产品A",
                expected_start_time=base,
                deadline=base + timedelta(hours=12),
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
        
        # 显示当前状态
        print("\n2. 当前排产状态（按首工序排序）:")
        
        order_times = []
        for o in created:
            db.refresh(o)
            if o.schedule_entries:
                first_start = min(e.start_time for e in o.schedule_entries)
                last_end = max(e.end_time for e in o.schedule_entries)
                order_times.append((o.priority, o.order_no, o.id, first_start, last_end, o.status, o.is_blocked))
        
        order_times.sort(key=lambda x: x[3])
        for pri, order_no, oid, start, end, status, blocked in order_times:
            print(f"   {start.strftime('%H:%M')} - {end.strftime('%H:%M')}  {order_no:12s}  pri={pri}  {status}  blocked={blocked}")
        
        # 检查冲突
        conflicts = check_conflicts(db)
        print(f"\n   冲突数: {len(conflicts)}")
        if conflicts:
            for c in conflicts:
                print(f"   ❌ 设备{c['device_id']}: {c['e1'].order.order_no} vs {c['e2'].order.order_no}")
        else:
            print("   ✅ 无冲突")
        
        # 插单：把最低优先级的提到最高
        print("\n3. 执行插单 - 把 P-LOW-01 (pri=2) 提到 pri=10...")
        
        low_order = next(o for o in created if o.order_no == "P-LOW-01")
        
        # 记录插单前的位置
        before_first = None
        for pri, order_no, oid, start, end, _, _ in order_times:
            if oid == low_order.id:
                before_first = start
                break
        
        print(f"   插单前首工序时间: {before_first.strftime('%H:%M') if before_first else 'N/A'}")
        
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
        print(f"     消息: {result.get('message')[:100]}")
        print(f"     延迟工单: {result.get('delayed_count')} 个")
        print(f"     受阻工单: {result.get('blocked_count')} 个")
        print(f"     受影响工单总数: {len(result.get('affected_orders', []))} 个")
        
        if result.get('affected_orders'):
            print("\n     受影响工单列表:")
            for ao in result['affected_orders']:
                impact = ao.get('impact_type', 'unknown')
                delay = ao.get('delay_minutes', 0)
                reason = ao.get('blocked_reason', '')
                if impact == 'delayed':
                    print(f"       • {ao['order_no']}: 延迟 {delay} 分钟")
                elif impact == 'blocked':
                    print(f"       • {ao['order_no']}: 受阻 - {reason[:50]}")
                else:
                    print(f"       • {ao['order_no']}: {impact}")
        
        # 检查插单后是否有冲突
        conflicts_after = check_conflicts(db)
        print(f"\n   插单后冲突数: {len(conflicts_after)}")
        if conflicts_after:
            print("   ❌ 存在冲突:")
            for c in conflicts_after:
                e1 = c['e1']
                e2 = c['e2']
                print(f"     设备{c['device_id']}:")
                print(f"       {e1.start_time.strftime('%H:%M')}-{e1.end_time.strftime('%H:%M')} "
                      f"{e1.order.order_no} (pri={e1.order.priority})")
                print(f"       {e2.start_time.strftime('%H:%M')}-{e2.end_time.strftime('%H:%M')} "
                      f"{e2.order.order_no} (pri={e2.order.priority})")
        else:
            print("   ✅ 无冲突")
        
        # 显示插单后的排序
        db.refresh(low_order)
        
        print("\n4. 插单后排产状态:")
        
        all_orders = db.query(WorkOrder).filter(
            WorkOrder.scenario_id.is_(None)
        ).all()
        
        order_times2 = []
        for o in all_orders:
            if o.schedule_entries:
                first_start = min(e.start_time for e in o.schedule_entries)
                last_end = max(e.end_time for e in o.schedule_entries)
                order_times2.append((o.priority, o.order_no, first_start, last_end, o.status, o.is_blocked))
            elif o.status == 'failed':
                order_times2.append((o.priority, o.order_no, None, None, o.status, o.is_blocked))
        
        order_times2.sort(key=lambda x: x[2] if x[2] else datetime.max)
        
        for pri, order_no, start, end, status, blocked in order_times2:
            start_str = start.strftime('%H:%M') if start else 'N/A'
            end_str = end.strftime('%H:%M') if end else 'N/A'
            marker = " ← 刚插单的" if order_no == "P-LOW-01" else ""
            blocked_str = " ⛔ 受阻" if blocked else ""
            print(f"   {start_str} - {end_str}  {order_no:12s}  pri={pri}  {status}{blocked_str}{marker}")
        
        # 验证插单后的工单是否在最前面
        if order_times2 and order_times2[0][1] == "P-LOW-01":
            print("\n   ✅ 插单后工单排在最前面")
        else:
            print("\n   ❌ 插单后工单不在最前面")
        
        # 验证受影响工单数量是否合理
        if result.get('success') and len(result.get('affected_orders', [])) > 0:
            print("   ✅ 受影响工单列表不为空")
        elif result.get('success'):
            print("   ❌ 受影响工单列表为空（但插单成功了）")
        
        print("\n" + "=" * 70)
        print("插单测试完成")
        print("=" * 70)
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
