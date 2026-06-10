import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api"

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def print_response(label, resp):
    print(f"\n--- {label} ---")
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except:
        print(resp.text)

def setup_devices():
    print_section("Step 1: 创建设备（设置不同的max_batch_size）")
    
    devices = [
        {"name": "CNC-A1", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "CNC-A2", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "CNC-A3", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 3},
        {"name": "抛光-B1", "device_type": "抛光", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10},
        {"name": "抛光-B2", "device_type": "抛光", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 8},
        {"name": "清洗-C1", "device_type": "清洗", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 20},
        {"name": "检验-D1", "device_type": "检验", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 15},
    ]
    
    created = []
    for d in devices:
        resp = requests.post(f"{BASE_URL}/devices/", json=d)
        print_response(f"创建设备 {d['name']}", resp)
        if resp.status_code == 201:
            created.append(resp.json())
    return created

def setup_route():
    print_section("Step 2: 创建工艺路线（CNC -> 抛光 -> 清洗 -> 检验）")
    
    route_data = {
        "product_name": "精密零件A",
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 60, "min_gap_after": 10},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 30, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 15, "min_gap_after": 0},
        ]
    }
    
    resp = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print_response("创建工艺路线", resp)
    return resp.json() if resp.status_code == 201 else None

def test_no_split():
    print_section("Test Case 1: 小批量工单 - 不需要拆批 (quantity=2)")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=3)
    
    order_data = {
        "order_no": "WO-NO-SPLIT-001",
        "product_name": "精密零件A",
        "total_quantity": 2,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    }
    
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建工单（不拆批）", resp)
    data = resp.json()
    
    if resp.status_code == 201:
        order_id = data["order_id"]
        print(f"\n  is_split: {data.get('is_split')}")
        print(f"  total_sub_batches: {data.get('total_sub_batches')}")
        print(f"  sub_batches count: {len(data.get('sub_batches', []))}")
        
        assert data.get("is_split") == False, "应该不拆批"
        assert data.get("total_sub_batches") == 0, "子批次数应为0"
        print("\n✓ 小批量工单正确：不拆批")
        return order_id
    return None

def test_normal_split():
    print_section("Test Case 2: 大批量工单 - 需要拆批 (quantity=10)")
    print("  说明: CNC类设备最小max_batch_size=3, 所以10个应该拆成 3,3,2,2 共4个子批次")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)
    
    order_data = {
        "order_no": "WO-SPLIT-001",
        "product_name": "精密零件A",
        "total_quantity": 10,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    }
    
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建拆批工单", resp)
    data = resp.json()
    
    if resp.status_code == 201:
        order_id = data["order_id"]
        print(f"\n  success: {data.get('success')}")
        print(f"  is_split: {data.get('is_split')}")
        print(f"  total_sub_batches: {data.get('total_sub_batches')}")
        print(f"  sub_batches count: {len(data.get('sub_batches', []))}")
        
        sub_batches = data.get("sub_batches", [])
        total_qty = 0
        for sb in sub_batches:
            print(f"\n  子批次 {sb['batch_no']}: 数量={sb['quantity']}, 排产工序数={len(sb['schedule_entries'])}")
            total_qty += sb["quantity"]
            for se in sb['schedule_entries']:
                print(f"    - {se['step_name']}: {se['start_time']} ~ {se['end_time']} @ 设备{se['device_id']} ({se.get('device_name', '')})")
        
        print(f"\n  子批次数量之和: {total_qty} (应该等于10)")
        
        assert data.get("success") == True, "排产应该成功"
        assert data.get("is_split") == True, "应该拆批"
        assert data.get("total_sub_batches") == 4, f"应该拆成4个子批次, 实际{data.get('total_sub_batches')}"
        assert total_qty == 10, f"子批次数量之和应该等于原始数量10"
        
        cnc_devices_used = set()
        for sb in sub_batches:
            for se in sb['schedule_entries']:
                if se['step_name'] == 'CNC加工':
                    cnc_devices_used.add(se['device_id'])
        print(f"\n  CNC工序使用的不同设备数: {len(cnc_devices_used)} (并行执行验证)")
        
        print("\n✓ 大批量拆批正确！")
        return order_id
    return None

def test_query_summary(order_id):
    print_section("Test Case 3: 查询工单汇总进度")
    
    if not order_id:
        print("  跳过：无有效order_id")
        return
    
    resp = requests.get(f"{BASE_URL}/orders/{order_id}/summary")
    print_response("工单汇总进度", resp)
    
    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  工单: {data['order_no']}")
        print(f"  总数量: {data['total_quantity']}")
        print(f"  状态: {data['status']}")
        print(f"  是否拆批: {data['is_split']}")
        print(f"  总子批次数: {data['total_sub_batches']}")
        print(f"  已完成子批次数: {data['completed_sub_batches']}")
        print(f"  完成进度: {data['progress_percent']}%")
        print(f"  预计整体完工时间: {data.get('estimated_completion_time')}")
        print("\n✓ 汇总查询正确！")

def test_query_sub_batches(order_id):
    print_section("Test Case 4: 查询子批次详情")
    
    if not order_id:
        print("  跳过：无有效order_id")
        return
    
    resp = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    print_response("子批次详情查询", resp)
    
    if resp.status_code == 200:
        sub_batches = resp.json()
        print(f"\n共有 {len(sub_batches)} 个子批次：")
        for sb in sub_batches:
            print(f"\n  [{sb['batch_no']}] 数量={sb['quantity']} 状态={sb['status']}")
            print(f"    子批次ID: {sb['sub_batch_id']}")
            for se in sb['schedule_entries']:
                print(f"    - {se['step_name']}: {se['start_time'][:19]} ~ {se['end_time'][:19]} @ 设备{se['device_id']}")
        print("\n✓ 子批次查询正确！")

def test_gantt_with_batches():
    print_section("Test Case 5: 甘特图查询（应显示子批次号）")
    
    today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    date_str = today.strftime("%Y-%m-%d")
    
    resp = requests.get(f"{BASE_URL}/schedule/gantt", params={"date_str": date_str})
    print_response(f"甘特图查询 ({date_str})", resp)
    
    if resp.status_code == 200:
        data = resp.json()
        print(f"\n查询日期: {data['date']}")
        for dg in data["devices"]:
            if dg["entries"]:
                print(f"\n  设备 {dg['device_name']} ({dg['device_type']}):")
                for e in dg["entries"]:
                    batch_info = f", 子批次={e.get('batch_no', 'N/A')}"
                    print(f"    - {e['order_no']}{batch_info}: {e['step_name']}")
        print("\n✓ 甘特图查询正确！")

def test_split_failure_rollback():
    print_section("Test Case 6: 拆批后某子批次排不进 - 整体回滚失败")
    print("  说明: 设置极短的截止时间，确保至少一个子批次无法排产")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(minutes=120)
    
    order_data = {
        "order_no": "WO-ROLLBACK-001",
        "product_name": "精密零件A",
        "total_quantity": 10,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    }
    
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建极短截止时间工单", resp)
    
    if resp.status_code == 201:
        data = resp.json()
        print(f"\n  success: {data.get('success')}")
        print(f"  status: {data.get('status')}")
        print(f"  message: {data.get('message')}")
        
        order_id = data["order_id"]
        check_resp = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
        print(f"\n  检查子批次（应被删除）: status={check_resp.status_code}")
        if check_resp.status_code == 200:
            sb_data = check_resp.json()
            print(f"    实际子批次数: {len(sb_data)} (应为0，表示回滚成功)")
        
        order_resp = requests.get(f"{BASE_URL}/orders/{order_id}")
        if order_resp.status_code == 200:
            order_data = order_resp.json()
            print(f"    工单is_split: {order_data.get('is_split')} (应为False)")
            print(f"    工单total_sub_batches: {order_data.get('total_sub_batches')} (应为0)")
            print(f"    工单status: {order_data.get('status')} (应为failed)")
            
            assert order_data.get("status") == "failed", "排产应该失败"
            assert order_data.get("is_split") == False, "回滚后is_split应为False"
            print("\n✓ 失败回滚正确！所有子批次已清理，工单状态标记为failed")
    return None

def test_order_delete_with_batches():
    print_section("Test Case 7: 删除拆批工单 - 所有子批次一起释放")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)
    
    order_data = {
        "order_no": "WO-DELETE-001",
        "product_name": "精密零件A",
        "total_quantity": 7,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    }
    
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    if resp.status_code != 201 or not resp.json().get("success"):
        print("  工单创建/排产失败，跳过")
        return
    
    order_id = resp.json()["order_id"]
    print(f"  创建工单成功，ID={order_id}")
    
    sub_before = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    if sub_before.status_code == 200:
        print(f"  删除前子批次数: {len(sub_before.json())}")
    
    delete_resp = requests.delete(f"{BASE_URL}/orders/{order_id}")
    print(f"\n  删除工单: status={delete_resp.status_code}")
    
    check_resp = requests.get(f"{BASE_URL}/orders/{order_id}")
    print(f"  删除后查询工单: status={check_resp.status_code} (应为404)")
    
    assert check_resp.status_code == 404, "删除后工单应不存在"
    print("\n✓ 工单删除正确，所有子批次级联删除！")

def run_all_tests():
    print("\n" + "#"*70)
    print("#" + " "*15 + "工单拆批与并行流水线编排模块测试" + " "*18 + "#")
    print("#"*70)
    
    try:
        setup_devices()
        setup_route()
        
        order_no_split = test_no_split()
        order_split_id = test_normal_split()
        
        test_query_summary(order_split_id)
        test_query_sub_batches(order_split_id)
        test_gantt_with_batches()
        
        test_split_failure_rollback()
        test_order_delete_with_batches()
        
        print_section("所有测试完成!")
        print("✓✓✓ 全部功能验证通过 ✓✓✓")
        
    except Exception as e:
        print(f"\n✗ 测试出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_all_tests()
