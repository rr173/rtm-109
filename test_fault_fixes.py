import json
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import Device, ProcessRoute, WorkOrder, ScheduleEntry
from app.scheduler import report_device_fault, resolve_device_fault
from sqlalchemy.orm import joinedload

Base.metadata.create_all(bind=engine)

client = TestClient(app)

def print_header(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)

def setup_clean_data():
    db = SessionLocal()
    try:
        print_header("清理并准备测试数据")
        
        db.query(ScheduleEntry).delete()
        db.query(WorkOrder).delete()
        db.query(ProcessRoute).delete()
        from app.models import ProcessStep, SubBatch, DeviceFault, ConflictRecord, MaintenancePlan
        db.query(DeviceFault).delete()
        db.query(ConflictRecord).delete()
        db.query(SubBatch).delete()
        db.query(ProcessStep).delete()
        db.query(MaintenancePlan).delete()
        db.query(Device).delete()
        db.commit()
        print("✓ 旧数据已清理")
        
        devices = [
            {"name": "车床-1", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
            {"name": "车床-2", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
            {"name": "热处理-1", "device_type": "热处理", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
            {"name": "检测-1", "device_type": "检测", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        ]
        dev_map = {}
        for d in devices:
            dev = Device(**d)
            db.add(dev)
            db.flush()
            dev_map[d["name"]] = dev.id
        db.commit()
        print(f"✓ 创建设备: {list(dev_map.keys())}")
        return dev_map
    finally:
        db.close()

def create_route_and_orders(dev_map, start_time):
    db = SessionLocal()
    try:
        print_header("创建工艺路线和工单")
        
        from app.models import ProcessStep
        route = ProcessRoute(product_name="测试产品A")
        db.add(route)
        db.flush()
        
        steps = [
            ProcessStep(route_id=route.id, step_order=1, step_name="粗车", device_type="车床", duration_minutes=60, min_gap_after=0),
            ProcessStep(route_id=route.id, step_order=2, step_name="精车", device_type="车床", duration_minutes=60, min_gap_after=0),
            ProcessStep(route_id=route.id, step_order=3, step_name="热处理", device_type="热处理", duration_minutes=120, min_gap_after=30),
            ProcessStep(route_id=route.id, step_order=4, step_name="检测", device_type="检测", duration_minutes=30, min_gap_after=0),
        ]
        db.add_all(steps)
        db.commit()
        print("✓ 工艺路线创建成功 (4道工序)")
        
        order_ids = []
        for i in range(1, 4):
            order = WorkOrder(
                order_no=f"TEST-FAULT-{i:03d}",
                product_name="测试产品A",
                expected_start_time=start_time,
                deadline=start_time + timedelta(hours=15),
                total_quantity=1
            )
            db.add(order)
            db.flush()
            order_ids.append(order.id)
        db.commit()
        print(f"✓ 创建 {len(order_ids)} 个测试工单: {[f'TEST-FAULT-{i:03d}' for i in range(1,4)]}")
        
        return order_ids
    finally:
        db.close()

def schedule_orders():
    print_header("执行排产（逐个创建工单触发自动排产）")
    db = SessionLocal()
    try:
        pending_orders = db.query(WorkOrder).filter(
            WorkOrder.order_no.like("TEST-FAULT-%")
        ).all()
        
        scheduled = []
        failed = []
        
        for order in pending_orders:
            order_data = {
                "order_no": order.order_no,
                "product_name": order.product_name,
                "expected_start_time": order.expected_start_time.isoformat(),
                "deadline": order.deadline.isoformat(),
                "total_quantity": order.total_quantity
            }
            db.delete(order)
        db.commit()
        
        for order in pending_orders:
            order_data = {
                "order_no": order.order_no,
                "product_name": order.product_name,
                "expected_start_time": order.expected_start_time.isoformat(),
                "deadline": order.deadline.isoformat(),
                "total_quantity": order.total_quantity
            }
            r = client.post("/api/orders/", json=order_data)
            if r.status_code == 201:
                result = r.json()
                scheduled.append(result)
                print(f"  ✓ 工单 {order.order_no}: {len(result.get('schedule_entries', []))} 道工序")
            else:
                failed.append(order)
                print(f"  ✗ 工单 {order.order_no}: {r.status_code} {r.text}")
        
        print(f"✓ 排产完成: 成功 {len(scheduled)} 个, 失败 {len(failed)} 个")
        return {"scheduled": scheduled, "failed": failed}
    finally:
        db.close()

def inspect_schedule(start_time, dev_map):
    db = SessionLocal()
    try:
        print_header("检查排产结果（迁移前）")
        
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.device),
            joinedload(ScheduleEntry.order),
        ).order_by(ScheduleEntry.start_time).all()
        
        print(f"共有 {len(entries)} 条排产记录:")
        dev_counts = {}
        for e in entries:
            dev_counts[e.device.name] = dev_counts.get(e.device.name, 0) + 1
            offset_hours = (e.start_time - start_time).total_seconds() / 3600
            offset_end = (e.end_time - start_time).total_seconds() / 3600
            print(f"  [{e.device.name:10s}] 工单{e.order.order_no:15s} "
                  f"工序{e.step_order}-{e.step_name:6s} "
                  f"T+{offset_hours:5.1f}h ~ T+{offset_end:5.1f}h "
                  f"(完成:{e.is_completed})")
        print(f"\n设备负载分布: {dev_counts}")
        return entries
    finally:
        db.close()

def test_report_fault_and_check(dev_map, start_time):
    db = SessionLocal()
    try:
        print_header("测试1: 报告车床-1故障 (T+2h 发生, T+10h 恢复)")
        
        fault_time = start_time + timedelta(hours=2)
        expected_recovery = start_time + timedelta(hours=10)
        
        result = report_device_fault(
            db,
            device_id=dev_map["车床-1"],
            expected_recovery_time=expected_recovery,
            fault_time=fault_time,
            description="测试故障: 主轴断裂"
        )
        
        print(f"结果: {result['success']} - {result['message']}")
        print(f"故障ID: {result.get('fault_id')}")
        print(f"受影响工单数: {result.get('affected_orders_count')}")
        print(f"成功迁移工序: {len(result.get('migrated_entries', []))}")
        print(f"受阻工单: {len(result.get('blocked_orders', []))}")
        print(f"连锁受阻: {len(result.get('cascade_blocked_orders', []))}")
        
        if result.get("migrated_entries"):
            print("\n--- 迁移详情 ---")
            for m in result["migrated_entries"]:
                offset_from_orig = (m["original_start_time"] - start_time).total_seconds() / 3600
                offset_to_orig = (m["original_end_time"] - start_time).total_seconds() / 3600
                offset_from_new = (m["new_start_time"] - start_time).total_seconds() / 3600
                offset_to_new = (m["new_end_time"] - start_time).total_seconds() / 3600
                print(f"  工单{m['order_no']:15s} 工序{m['step_order']}-{m['step_name']:6s}: "
                      f"{m['from_device_name']} → {m['to_device_name']}")
                print(f"    原时间: T+{offset_from_orig:.1f}h ~ T+{offset_to_orig:.1f}h")
                print(f"    新时间: T+{offset_from_new:.1f}h ~ T+{offset_to_new:.1f}h")
        
        if result.get("blocked_orders"):
            print("\n--- 受阻工单 ---")
            for b in result["blocked_orders"]:
                print(f"  工单{b['order_no']}: {b['blocked_reason']}")
        
        return result, fault_time
    finally:
        db.close()

def check_sequence_and_cleanup(dev_map, start_time):
    db = SessionLocal()
    try:
        print_header("检查2: 顺序约束验证 + 旧记录清理验证")
        
        cheyi_id = dev_map["车床-1"]
        entries = db.query(ScheduleEntry).options(
            joinedload(ScheduleEntry.device),
            joinedload(ScheduleEntry.order),
        ).order_by(ScheduleEntry.order_id, ScheduleEntry.sub_batch_id, ScheduleEntry.step_order).all()
        
        print(f"当前共有 {len(entries)} 条排产记录")
        
        old_on_cheyi = [e for e in entries if e.device_id == cheyi_id and not e.is_completed and e.start_time >= start_time + timedelta(hours=2)]
        print(f"\n[问题3检查] 车床-1上故障时间后的未完成工序: {len(old_on_cheyi)} 条")
        if len(old_on_cheyi) == 0:
            print("  ✓ PASS: 故障设备上的旧记录已完全清理!")
        else:
            print("  ✗ FAIL: 还有残留的旧记录:")
            for e in old_on_cheyi:
                offset_h = (e.start_time - start_time).total_seconds()/3600
                print(f"     {e.order.order_no} 工序{e.step_order}-{e.step_name} T+{offset_h:.1f}h")
        
        print("\n[问题2检查] 每个工单/子批次的工序顺序约束:")
        from collections import defaultdict
        groups = defaultdict(list)
        for e in entries:
            key = (e.order_id, e.sub_batch_id)
            groups[key].append(e)
        
        all_pass = True
        for (order_id, sub_batch_id), group in sorted(groups.items()):
            group.sort(key=lambda x: x.step_order)
            order_no = group[0].order.order_no
            sub_info = f"子批次{sub_batch_id}" if sub_batch_id else "主子批次"
            print(f"\n  工单{order_no} ({sub_info}):")
            
            for i in range(1, len(group)):
                prev = group[i-1]
                curr = group[i]
                gap = (curr.start_time - prev.end_time).total_seconds() / 60
                prev_end_h = (prev.end_time - start_time).total_seconds()/3600
                curr_start_h = (curr.start_time - start_time).total_seconds()/3600
                
                if gap >= 0:
                    print(f"    工序{prev.step_order}→{curr.step_order}: "
                          f"结束T+{prev_end_h:.1f}h → 开始T+{curr_start_h:.1f}h, "
                          f"间隔 {gap:.0f}分钟 ✓")
                else:
                    print(f"    工序{prev.step_order}→{curr.step_order}: "
                          f"结束T+{prev_end_h:.1f}h → 开始T+{curr_start_h:.1f}h, "
                          f"重叠 {-gap:.0f}分钟 ✗")
                    all_pass = False
        
        if all_pass:
            print("\n  ✓ PASS: 所有工单的工序顺序约束都满足!")
        else:
            print("\n  ✗ FAIL: 存在工序重叠问题!")
        
        return all_pass and len(old_on_cheyi) == 0
    finally:
        db.close()

def test_duplicate_fault(dev_map):
    db = SessionLocal()
    try:
        print_header("检查3: 重复故障报告防护")
        
        result = report_device_fault(
            db,
            device_id=dev_map["车床-1"],
            expected_recovery_time=datetime.now() + timedelta(hours=5),
            description="重复故障测试"
        )
        print(f"重复报告结果: success={result['success']}")
        if not result['success']:
            print(f"  错误信息: {result['message']}")
            print("  ✓ PASS: 正确阻止了重复报告!")
            return True
        else:
            print("  ✗ FAIL: 应该阻止重复报告但没有!")
            return False
    finally:
        db.close()

def test_new_order_scheduling_during_fault(dev_map, start_time):
    print_header("检查4: 故障期间新工单不能排到故障设备")
    
    r = client.post("/api/orders/", json={
        "order_no": "DURING-FAULT-001",
        "product_name": "测试产品A",
        "expected_start_time": (start_time + timedelta(hours=3)).isoformat(),
        "deadline": (start_time + timedelta(hours=20)).isoformat(),
        "total_quantity": 1
    })
    assert r.status_code == 201, f"创建工单失败: {r.status_code}"
    result = r.json()
    entries = result.get("schedule_entries", [])
    
    print(f"新工单 DURING-FAULT-001 创建了 {len(entries)} 道工序")
    fault_dev_id = dev_map["车床-1"]
    used_faulty = [e for e in entries if e["device_id"] == fault_dev_id]
    
    if not used_faulty:
        print("  ✓ PASS: 新工单没有排到故障设备车床-1上!")
        for e in entries:
            dev_name = next((n for n, i in dev_map.items() if i == e["device_id"]), f"#{e['device_id']}")
            print(f"    工序{e['step_order']}-{e['step_name']}: {dev_name}")
        return True
    else:
        print(f"  ✗ FAIL: 有 {len(used_faulty)} 道工序排到了故障设备上!")
        return False

def test_resolve_fault(dev_map, start_time):
    db = SessionLocal()
    try:
        print_header("检查5: 解除故障")
        
        actual_recovery = start_time + timedelta(hours=8)
        result = resolve_device_fault(db, dev_map["车床-1"], actual_recovery)
        print(f"解除故障结果: success={result['success']}")
        if result['success']:
            print(f"  状态: {result['status']}")
            print("  ✓ PASS: 故障成功解除!")
            return True
        else:
            print(f"  错误: {result['message']}")
            return False
    finally:
        db.close()

def test_order_blocked_status():
    db = SessionLocal()
    try:
        print_header("检查6: 受阻工单状态")
        blocked = db.query(WorkOrder).filter(WorkOrder.is_blocked == True).all()
        print(f"受阻工单总数: {len(blocked)}")
        for o in blocked:
            print(f"  - {o.order_no}: {o.blocked_reason}")
        return True
    finally:
        db.close()

def main():
    print("\n" + "█"*70)
    print("  设备故障模块 Bug 修复验证测试")
    print("█"*70)
    
    start_time = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start_time < datetime.now():
        start_time += timedelta(days=1)
    print(f"\n测试基准时间 (T=0): {start_time}\n")
    
    try:
        dev_map = setup_clean_data()
        order_ids = create_route_and_orders(dev_map, start_time)
        
        schedule_result = schedule_orders()
        entries_before = inspect_schedule(start_time, dev_map)
        
        fault_result, fault_time = test_report_fault_and_check(dev_map, start_time)
        
        if not fault_result["success"]:
            print("\n✗✗✗ 故障报告本身失败，终止测试")
            return
        
        checks_ok = True
        checks_ok &= check_sequence_and_cleanup(dev_map, start_time)
        checks_ok &= test_duplicate_fault(dev_map)
        checks_ok &= test_new_order_scheduling_during_fault(dev_map, start_time)
        checks_ok &= test_resolve_fault(dev_map, start_time)
        checks_ok &= test_order_blocked_status()
        
        print("\n" + "="*70)
        if checks_ok:
            print("  ✓✓✓ 所有检查通过! Bugs已修复 ✓✓✓")
        else:
            print("  ✗✗✗ 部分检查未通过! 请查看上方详情")
        print("="*70)
        
    except AssertionError as e:
        print(f"\n✗ 断言失败: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
