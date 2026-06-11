import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:9000/api"

def print_response(label, response):
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print('='*60)

def setup_test_environment():
    print("=== 设置测试环境 ===")
    
    devices = [
        {"name": "车床-F1", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "车床-F2", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "热处理炉-F1", "device_type": "热处理", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "检测台-F1", "device_type": "检测", "daily_start": "08:00", "daily_end": "20:00"},
    ]
    
    device_ids = {}
    for d in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=d)
        if r.status_code == 201:
            device_ids[d["name"]] = r.json()["id"]
            print(f"设备 {d['name']} 创建成功, ID: {device_ids[d['name']]}")
        elif r.status_code == 400 and "already exists" in r.text:
            r_list = requests.get(f"{BASE_URL}/devices/", params={"device_type": d["device_type"]})
            for dev in r_list.json():
                if dev["name"] == d["name"]:
                    device_ids[d["name"]] = dev["id"]
                    print(f"设备 {d['name']} 已存在, ID: {device_ids[d['name']]}")
    
    route_data = {
        "product_name": "故障测试产品",
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": "车床", "duration_minutes": 60, "min_gap_after": 0},
            {"step_order": 2, "step_name": "精车", "device_type": "车床", "duration_minutes": 90, "min_gap_after": 0},
            {"step_order": 3, "step_name": "热处理", "device_type": "热处理", "duration_minutes": 120, "min_gap_after": 30},
            {"step_order": 4, "step_name": "检测", "device_type": "检测", "duration_minutes": 30, "min_gap_after": 0},
        ]
    }
    
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    if r.status_code == 201:
        print("工艺路线创建成功")
    elif r.status_code == 400 and "already exists" in r.text:
        print("工艺路线已存在")
    
    return device_ids

def create_test_orders(start_time, device_ids):
    print("\n=== 创建测试工单 ===")
    
    order_ids = []
    for i in range(1, 4):
        order_data = {
            "order_no": f"FAULT-TEST-{i:03d}",
            "product_name": "故障测试产品",
            "expected_start_time": start_time.isoformat(),
            "deadline": (start_time + timedelta(hours=12)).isoformat(),
            "total_quantity": 1
        }
        r = requests.post(f"{BASE_URL}/orders/", json=order_data)
        print_response(f"创建工单 FAULT-TEST-{i:03d}", r)
        if r.status_code == 201:
            order_ids.append(r.json()["order_id"])
    
    return order_ids

def test_device_fault_report(device_ids, start_time):
    print("\n=== 测试1: 报告设备故障 ===")
    
    fault_time = start_time + timedelta(hours=2)
    expected_recovery = start_time + timedelta(hours=8)
    
    fault_data = {
        "device_id": device_ids["车床-F1"],
        "fault_time": fault_time.isoformat(),
        "expected_recovery_time": expected_recovery.isoformat(),
        "description": "主轴故障，需要更换轴承"
    }
    
    r = requests.post(f"{BASE_URL}/faults/report", json=fault_data)
    print_response("报告设备故障", r)
    
    if r.status_code == 201:
        result = r.json()
        print(f"\n故障报告成功:")
        print(f"  故障ID: {result['fault_id']}")
        print(f"  受影响工单数量: {result['affected_orders_count']}")
        print(f"  成功迁移工序: {len(result['migrated_entries'])}")
        print(f"  受阻工单: {len(result['blocked_orders'])}")
        print(f"  连锁受阻工单: {len(result['cascade_blocked_orders'])}")
        
        if result['migrated_entries']:
            print("\n  迁移详情:")
            for entry in result['migrated_entries']:
                print(f"    工单 {entry['order_no']} 工序 {entry['step_name']}: "
                      f"{entry['from_device_name']} -> {entry['to_device_name']}")
        
        if result['blocked_orders']:
            print("\n  受阻工单:")
            for blocked in result['blocked_orders']:
                print(f"    工单 {blocked['order_no']}: {blocked['blocked_reason']}")
    
    return r.json() if r.status_code == 201 else None

def test_duplicate_fault_report(device_ids, start_time):
    print("\n=== 测试2: 重复报告故障（应该失败） ===")
    
    fault_time = start_time + timedelta(hours=3)
    expected_recovery = start_time + timedelta(hours=10)
    
    fault_data = {
        "device_id": device_ids["车床-F1"],
        "fault_time": fault_time.isoformat(),
        "expected_recovery_time": expected_recovery.isoformat(),
        "description": "重复报告测试"
    }
    
    r = requests.post(f"{BASE_URL}/faults/report", json=fault_data)
    print_response("重复报告故障", r)
    
    if r.status_code == 400:
        print("✓ 正确阻止了重复故障报告")
    else:
        print("✗ 应该阻止但没有阻止")

def test_list_faults():
    print("\n=== 测试3: 查询故障列表 ===")
    
    r = requests.get(f"{BASE_URL}/faults/")
    print_response("故障列表（仅活跃）", r)
    
    r = requests.get(f"{BASE_URL}/faults/", params={"include_resolved": "true"})
    print_response("故障列表（含已解除）", r)

def test_get_device_active_fault(device_ids):
    print("\n=== 测试4: 查询设备活跃故障 ===")
    
    r = requests.get(f"{BASE_URL}/faults/device/{device_ids['车床-F1']}/active")
    print_response(f"查询车床-F1的活跃故障", r)
    
    r = requests.get(f"{BASE_URL}/faults/device/{device_ids['车床-F2']}/active")
    print_response(f"查询车床-F2的活跃故障（应该无）", r)

def test_schedule_during_fault(device_ids, start_time):
    print("\n=== 测试5: 故障期间新工单排产（应该避开故障设备） ===")
    
    order_data = {
        "order_no": "FAULT-TEST-DURING",
        "product_name": "故障测试产品",
        "expected_start_time": (start_time + timedelta(hours=4)).isoformat(),
        "deadline": (start_time + timedelta(hours=16)).isoformat(),
        "total_quantity": 1
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("故障期间创建新工单", r)
    
    if r.status_code == 201:
        result = r.json()
        schedule_entries = result.get("schedule_entries", [])
        if schedule_entries:
            for entry in schedule_entries:
                if entry["device_id"] == device_ids["车床-F1"]:
                    print(f"✗ 错误: 工单排到了故障设备 车床-F1 上!")
                else:
                    print(f"✓ 工序 {entry['step_name']} 正确排到了设备 {entry['device_id']}")

def test_resolve_fault(device_ids, start_time):
    print("\n=== 测试6: 解除设备故障 ===")
    
    actual_recovery = start_time + timedelta(hours=6)
    
    resolve_data = {
        "actual_recovery_time": actual_recovery.isoformat()
    }
    
    r = requests.post(f"{BASE_URL}/faults/{device_ids['车床-F1']}/resolve", json=resolve_data)
    print_response("解除设备故障", r)
    
    if r.status_code == 200:
        result = r.json()
        print(f"✓ 故障解除成功:")
        print(f"  故障ID: {result['fault_id']}")
        print(f"  状态: {result['status']}")
        print(f"  解除时间: {result['resolved_at']}")

def test_schedule_after_resolve(device_ids, start_time):
    print("\n=== 测试7: 故障解除后新工单排产（应该可以使用恢复的设备） ===")
    
    order_data = {
        "order_no": "FAULT-TEST-AFTER",
        "product_name": "故障测试产品",
        "expected_start_time": (start_time + timedelta(hours=7)).isoformat(),
        "deadline": (start_time + timedelta(hours=20)).isoformat(),
        "total_quantity": 1
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("故障解除后创建新工单", r)
    
    if r.status_code == 201:
        result = r.json()
        schedule_entries = result.get("schedule_entries", [])
        if schedule_entries:
            used_device_ids = [e["device_id"] for e in schedule_entries if e["device_id"] == device_ids["车床-F1"]]
            if used_device_ids:
                print(f"✓ 故障解除后，车床-F1 已重新参与排产")
            else:
                print(f"△ 工单没有排到车床-F1（可能排到了其他设备，这也是正常的）")

def test_get_fault_detail(fault_id):
    if not fault_id:
        return
    
    print(f"\n=== 测试8: 查询故障详情 (ID: {fault_id}) ===")
    
    r = requests.get(f"{BASE_URL}/faults/{fault_id}")
    print_response("故障详情", r)

def test_gantt_after_fault(start_time):
    print("\n=== 测试9: 查看甘特图确认迁移结果 ===")
    
    date_str = start_time.strftime("%Y-%m-%d")
    r = requests.get(f"{BASE_URL}/schedule/gantt", params={"date_str": date_str})
    print_response(f"甘特图 - {date_str}", r)

def test_order_status_after_fault():
    print("\n=== 测试10: 查看工单状态（受阻工单） ===")
    
    r = requests.get(f"{BASE_URL}/orders/")
    print_response("工单列表", r)
    
    if r.status_code == 200:
        orders = r.json()
        blocked_orders = [o for o in orders if o.get("is_blocked")]
        if blocked_orders:
            print(f"\n受阻工单:")
            for order in blocked_orders:
                print(f"  工单 {order['order_no']}: {order.get('blocked_reason', '无原因')}")

def main():
    print("=== 设备故障应急重排模块综合测试 ===\n")
    
    now = datetime.now()
    start_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if start_time < now:
        start_time += timedelta(days=1)
    print(f"测试开始时间: {start_time}\n")
    
    try:
        device_ids = setup_test_environment()
        order_ids = create_test_orders(start_time, device_ids)
        
        fault_result = test_device_fault_report(device_ids, start_time)
        fault_id = fault_result["fault_id"] if fault_result else None
        
        test_duplicate_fault_report(device_ids, start_time)
        test_list_faults()
        test_get_device_active_fault(device_ids)
        test_schedule_during_fault(device_ids, start_time)
        test_get_fault_detail(fault_id)
        test_gantt_after_fault(start_time)
        test_order_status_after_fault()
        test_resolve_fault(device_ids, start_time)
        test_schedule_after_resolve(device_ids, start_time)
        
        print("\n" + "="*60)
        print("=== 所有测试完成 ===")
        print("="*60)
        
    except Exception as e:
        print(f"\n测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
