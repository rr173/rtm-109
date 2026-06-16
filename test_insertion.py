import requests
import json
import time
from datetime import datetime, timedelta

BASE_URL = "http://localhost:9000/api"

def print_response(label, response):
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print('='*60)

def setup_test_data():
    print("=== 准备测试数据 ===")
    
    devices = [
        {"name": "车床-01", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        {"name": "车床-02", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        {"name": "铣床-01", "device_type": "铣床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
    ]
    
    for dev in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=dev)
        if r.status_code == 200:
            print(f"创建设备: {dev['name']} 成功")
        elif r.status_code == 400 and "already exists" in r.text:
            print(f"设备 {dev['name']} 已存在")
        else:
            print(f"创建设备 {dev['name']} 失败: {r.status_code} {r.text}")
    
    route = {
        "product_name": "测试产品A",
        "product_family_id": None,
        "steps": [
            {
                "step_order": 1,
                "step_name": "车削",
                "device_type": "车床",
                "duration_minutes": 120,
                "min_gap_after": 0,
                "fixture_type_id": None,
                "is_outsource": False,
                "material_requirements": []
            },
            {
                "step_order": 2,
                "step_name": "铣削",
                "device_type": "铣床",
                "duration_minutes": 90,
                "min_gap_after": 0,
                "fixture_type_id": None,
                "is_outsource": False,
                "material_requirements": []
            }
        ]
    }
    
    r = requests.post(f"{BASE_URL}/routes/", json=route)
    if r.status_code == 201:
        print("创建工艺路线成功")
    elif r.status_code == 400 and "already exists" in r.text:
        print("工艺路线已存在")
    else:
        print(f"创建工艺路线失败: {r.status_code} {r.text}")

def test_priority_scheduling():
    print("\n=== 测试 1: 优先级排产（高优先级优先） ===")
    
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)
    
    print(f"基准时间: {start}")
    
    order_low = {
        "order_no": "LOW-PRIO-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=8)).isoformat(),
        "total_quantity": 1,
        "priority": 3
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_low)
    print_response("创建低优先级工单 (priority=3)", r)
    
    order_high = {
        "order_no": "HIGH-PRIO-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=8)).isoformat(),
        "total_quantity": 1,
        "priority": 8
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_high)
    print_response("创建高优先级工单 (priority=8)", r)
    
    print("\n--- 查看两个工单的第一道工序时间 ---")
    r_low = requests.get(f"{BASE_URL}/orders/")
    orders = r_low.json()
    
    low_order = next((o for o in orders if o["order_no"] == "LOW-PRIO-001"), None)
    high_order = next((o for o in orders if o["order_no"] == "HIGH-PRIO-001"), None)
    
    if low_order and low_order["schedule_entries"]:
        low_first = min(low_order["schedule_entries"], key=lambda e: e["start_time"])
        print(f"低优先级工单 第一道工序开始时间: {low_first['start_time']}")
    
    if high_order and high_order["schedule_entries"]:
        high_first = min(high_order["schedule_entries"], key=lambda e: e["start_time"])
        print(f"高优先级工单 第一道工序开始时间: {high_first['start_time']}")
    
    if low_order and high_order and low_order["schedule_entries"] and high_order["schedule_entries"]:
        low_start = min(e["start_time"] for e in low_order["schedule_entries"])
        high_start = min(e["start_time"] for e in high_order["schedule_entries"])
        if high_start < low_start:
            print("✓ 测试通过: 高优先级工单排在了低优先级工单前面")
        else:
            print("✗ 测试失败: 高优先级工单没有排在前面")

def test_order_insertion():
    print("\n=== 测试 2: 插单功能 ===")
    
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)
    
    order_normal = {
        "order_no": "NORMAL-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=10)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_normal)
    print_response("创建普通工单 (priority=5)", r)
    normal_order_id = r.json().get("order_id")
    
    order_urgent = {
        "order_no": "URGENT-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=10)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_urgent)
    print_response("创建待插单工单 (priority=5)", r)
    urgent_order_id = r.json().get("order_id")
    
    if urgent_order_id:
        r_before = requests.get(f"{BASE_URL}/orders/{urgent_order_id}")
        before = r_before.json()
        if before["schedule_entries"]:
            first_step = min(before["schedule_entries"], key=lambda e: e["start_time"])
            print(f"\n插单前第一道工序开始时间: {first_step['start_time']}")
        
        insertion_request = {
            "order_id": urgent_order_id,
            "new_priority": 9,
            "operator": "张三",
            "reason": "客户催单，紧急插单"
        }
        
        r_insert = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_request)
        print_response("执行插单 (提升优先级到9)", r_insert)
        
        if r_insert.status_code == 200:
            result = r_insert.json()
            print(f"\n插单结果:")
            print(f"  受影响工单数量: {len(result['affected_orders'])}")
            print(f"  延迟工单数量: {result['delayed_count']}")
            print(f"  受阻工单数量: {result['blocked_count']}")
            
            for affected in result["affected_orders"]:
                print(f"  - {affected['order_no']}: {affected['impact_type']}, 延迟 {affected['delay_minutes']} 分钟")

def test_locked_order_conflict():
    print("\n=== 测试 3: 锁定工单冲突检测 ===")
    
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)
    
    order_locked = {
        "order_no": "LOCKED-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=8)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_locked)
    locked_order_id = r.json().get("order_id")
    print(f"创建锁定工单 ID: {locked_order_id}")
    
    if locked_order_id:
        r_lock = requests.post(f"{BASE_URL}/orders/{locked_order_id}/lock")
        print_response("锁定工单", r_lock)
        
        order_insert = {
            "order_no": "INSERT-TRY-001",
            "product_name": "测试产品A",
            "expected_start_time": start.isoformat(),
            "deadline": (start + timedelta(hours=6)).isoformat(),
            "total_quantity": 1,
            "priority": 3
        }
        
        r = requests.post(f"{BASE_URL}/orders/", json=order_insert)
        insert_order_id = r.json().get("order_id")
        print(f"创建待插单工单 ID: {insert_order_id}")
        
        if insert_order_id:
            insertion_request = {
                "order_id": insert_order_id,
                "new_priority": 10,
                "operator": "李四",
                "reason": "测试锁定冲突"
            }
            
            r_insert = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_request)
            print_response("尝试插入（应该被锁定工单挡住）", r_insert)
            
            if r_insert.status_code == 409:
                print("✓ 测试通过: 锁定工单成功阻挡了插单")

def test_insertion_throttle():
    print("\n=== 测试 4: 插单防抖动（10分钟间隔） ===")
    
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)
    
    order_throttle = {
        "order_no": "THROTTLE-001",
        "product_name": "测试产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=12)).isoformat(),
        "total_quantity": 1,
        "priority": 4
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_throttle)
    throttle_order_id = r.json().get("order_id")
    print(f"创建测试工单 ID: {throttle_order_id}")
    
    if throttle_order_id:
        insertion_request1 = {
            "order_id": throttle_order_id,
            "new_priority": 7,
            "operator": "王五",
            "reason": "第一次插单"
        }
        
        r1 = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_request1)
        print_response("第一次插单 (priority 4→7)", r1)
        
        insertion_request2 = {
            "order_id": throttle_order_id,
            "new_priority": 8,
            "operator": "王五",
            "reason": "第二次插单"
        }
        
        r2 = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_request2)
        print_response("第二次插单 (应该被限制)", r2)
        
        if r2.status_code == 400 and "频繁" in r2.text:
            print("✓ 测试通过: 防抖动机制生效")

def test_insertion_history():
    print("\n=== 测试 5: 插单历史记录查询 ===")
    
    r = requests.get(f"{BASE_URL}/insertion/history", params={"limit": 10})
    print_response("查询插单历史（最近10条）", r)
    
    if r.status_code == 200:
        data = r.json()
        print(f"共 {data['total']} 条插单记录")
        if data["histories"]:
            latest = data["histories"][0]
            print(f"\n最新一条记录 ID: {latest['id']}")
            
            r_detail = requests.get(f"{BASE_URL}/insertion/history/{latest['id']}")
            print_response("查看插单详情", r_detail)

def main():
    print("="*60)
    print("工单优先级动态插单与级联重排模块 - 功能测试")
    print("="*60)
    
    try:
        setup_test_data()
    except Exception as e:
        print(f"准备测试数据时出错: {e}")
    
    try:
        test_priority_scheduling()
    except Exception as e:
        print(f"测试优先级排产时出错: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_order_insertion()
    except Exception as e:
        print(f"测试插单功能时出错: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_locked_order_conflict()
    except Exception as e:
        print(f"测试锁定工单冲突时出错: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_insertion_throttle()
    except Exception as e:
        print(f"测试防抖动时出错: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        test_insertion_history()
    except Exception as e:
        print(f"测试插单历史时出错: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("所有测试完成")
    print("="*60)

if __name__ == "__main__":
    main()
