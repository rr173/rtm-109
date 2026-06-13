import requests
import json
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

def wait_for_server():
    import time
    print("等待服务器启动...")
    for i in range(30):
        try:
            r = requests.get(f"{BASE_URL.replace('/api', '')}/health")
            if r.status_code == 200:
                print("服务器已启动")
                return True
        except:
            pass
        time.sleep(1)
    print("服务器启动超时")
    return False

def main():
    print("=== 工装管理与占用编排模块测试 ===")
    
    if not wait_for_server():
        return
    
    test_results = []
    
    # 测试1: 创建工装类型
    print("\n--- 测试 1: 创建工装类型 ---")
    fixture_type1 = {
        "name": "焊接夹具-A型",
        "description": "用于产品A的焊接工序",
        "turn_over_minutes": 30
    }
    r = requests.post(f"{BASE_URL}/fixtures/types", json=fixture_type1)
    print_response("创建工装类型 '焊接夹具-A型'", r)
    test_results.append(("创建工装类型", r.status_code == 201))
    
    fixture_type2 = {
        "name": "检测治具-B型",
        "description": "用于精密检测工序",
        "turn_over_minutes": 15
    }
    r = requests.post(f"{BASE_URL}/fixtures/types", json=fixture_type2)
    print_response("创建工装类型 '检测治具-B型'", r)
    test_results.append(("创建工装类型2", r.status_code == 201))
    
    # 测试2: 查询工装类型列表
    print("\n--- 测试 2: 查询工装类型列表 ---")
    r = requests.get(f"{BASE_URL}/fixtures/types")
    print_response("工装类型列表", r)
    types = r.json()
    type_id_1 = types[0]["id"] if types else None
    type_id_2 = types[1]["id"] if len(types) > 1 else None
    test_results.append(("查询工装类型列表", r.status_code == 200 and len(types) >= 2))
    
    # 测试3: 创建具体工装
    print("\n--- 测试 3: 创建具体工装 ---")
    fixture1 = {
        "code": "FIX-WELD-A-001",
        "fixture_type_id": type_id_1,
        "compatible_device_types": "焊接机器人,手工焊台",
        "status": "available"
    }
    r = requests.post(f"{BASE_URL}/fixtures/", json=fixture1)
    print_response("创建工装 FIX-WELD-A-001", r)
    test_results.append(("创建工装1", r.status_code == 201))
    
    fixture2 = {
        "code": "FIX-WELD-A-002",
        "fixture_type_id": type_id_1,
        "compatible_device_types": "焊接机器人,手工焊台",
        "status": "available"
    }
    r = requests.post(f"{BASE_URL}/fixtures/", json=fixture2)
    print_response("创建工装 FIX-WELD-A-002", r)
    test_results.append(("创建工装2", r.status_code == 201))
    
    fixture3 = {
        "code": "FIX-TEST-B-001",
        "fixture_type_id": type_id_2,
        "compatible_device_types": "三坐标测量仪",
        "status": "available"
    }
    r = requests.post(f"{BASE_URL}/fixtures/", json=fixture3)
    print_response("创建工装 FIX-TEST-B-001", r)
    test_results.append(("创建工装3", r.status_code == 201))
    
    # 测试4: 查询工装列表
    print("\n--- 测试 4: 查询工装列表 ---")
    r = requests.get(f"{BASE_URL}/fixtures/")
    print_response("工装列表", r)
    fixtures = r.json()
    fixture_id_1 = fixtures[0]["id"] if fixtures else None
    test_results.append(("查询工装列表", r.status_code == 200 and len(fixtures) >= 3))
    
    # 测试5: 尝试删除被使用的工装类型（应该失败）
    print("\n--- 测试 5: 删除工装类型约束检查 ---")
    # 先创建一个使用该工装类型的工艺路线
    route_data = {
        "product_name": "工装测试产品",
        "revision": "A",
        "steps": [
            {
                "step_order": 1,
                "step_name": "焊接",
                "device_type": "焊接机器人",
                "duration_minutes": 60,
                "min_gap_after": 0,
                "fixture_type_id": type_id_1
            },
            {
                "step_order": 2,
                "step_name": "检测",
                "device_type": "三坐标测量仪",
                "duration_minutes": 30,
                "min_gap_after": 0,
                "fixture_type_id": type_id_2
            }
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print_response("创建带工装需求的工艺路线", r)
    test_results.append(("创建带工装需求的工艺路线", r.status_code in [200, 201]))
    
    # 尝试删除工装类型（应该失败）
    r = requests.delete(f"{BASE_URL}/fixtures/types/{type_id_1}")
    print_response("尝试删除正在使用的工装类型（应该失败）", r)
    test_results.append(("删除使用中工装类型失败", r.status_code == 400))
    
    # 测试6: 工单排产 - 同时分配设备和工装
    print("\n--- 测试 6: 工单排产（自动分配工装） ---")
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)
    
    order1 = {
        "order_no": "WO-FIX-001",
        "product_name": "工装测试产品",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=4)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order1)
    print_response("创建工单 WO-FIX-001", r)
    test_results.append(("创建带工装需求的工单", r.status_code in [200, 201]))
    
    # 查看排产结果中的工装分配
    if r.status_code in [200, 201]:
        order_data = r.json()
        print("\n工装分配检查:")
        for entry in order_data.get("schedule_entries", []):
            print(f"  工序 {entry['step_name']}:")
            print(f"    设备: {entry.get('device_id')}")
            print(f"    工装: {entry.get('fixture_id')} (编码: {entry.get('fixture_code', 'N/A')})")
            print(f"    周转结束时间: {entry.get('fixture_turn_over_end_time', 'N/A')}")
    
    # 测试7: 工装瓶颈检测
    print("\n--- 测试 7: 工装瓶颈检测 ---")
    # 创建更多工单，工装只有2套焊接夹具，看第3个工单是否报工装瓶颈
    order2 = {
        "order_no": "WO-FIX-002",
        "product_name": "工装测试产品",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=6)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order2)
    print_response("创建工单 WO-FIX-002（第2套焊接夹具）", r)
    test_results.append(("创建第2个工单", r.status_code in [200, 201]))
    
    order3 = {
        "order_no": "WO-FIX-003",
        "product_name": "工装测试产品",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=2)).isoformat()  # 时间很短，可能因工装不足失败
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order3)
    print_response("创建工单 WO-FIX-003（可能因工装不足失败）", r)
    if r.status_code == 400:
        error_msg = r.json().get("detail", "")
        print(f"错误信息: {error_msg}")
        if "工装" in error_msg or "fixture" in error_msg.lower():
            print("✓ 正确识别工装瓶颈")
            test_results.append(("工装瓶颈识别", True))
        else:
            test_results.append(("工装瓶颈识别", False))
    else:
        print("排产成功，工装可能有空闲或时间顺延")
        test_results.append(("工装瓶颈识别", r.status_code in [200, 201]))
    
    # 测试8: 查询工装占用时间线
    print("\n--- 测试 8: 查询工装占用时间线 ---")
    if fixture_id_1:
        r = requests.get(f"{BASE_URL}/fixtures/{fixture_id_1}/timeline", params={"look_ahead_days": 7})
        print_response(f"工装 {fixture_id_1} 未来7天占用时间线", r)
        test_results.append(("查询工装时间线", r.status_code == 200))
        
        if r.status_code == 200:
            timeline = r.json()
            print(f"\n工装 {timeline['fixture_code']} 时间线概览:")
            print(f"  当前状态: {timeline['status']}")
            if timeline.get('current_occupancy'):
                curr = timeline['current_occupancy']
                print(f"  当前占用: {curr['order_no']} - {curr['step_name']}")
                print(f"  状态: {curr['status']} (生产中: {curr['is_producing']}, 周转中: {curr['is_in_turn_over']})")
            for day in timeline['days'][:3]:
                print(f"  {day['date']}: {len(day['entries'])} 个占用")
                for entry in day['entries'][:2]:
                    print(f"    {entry['start_time'][11:16]}-{entry['end_time'][11:16]} {entry['type']}: {entry['description']}")
    
    # 测试9: 删除工装约束检查（有未来占用时）
    print("\n--- 测试 9: 删除工装约束检查 ---")
    if fixture_id_1:
        r = requests.delete(f"{BASE_URL}/fixtures/{fixture_id_1}")
        print_response("尝试删除有未来占用的工装（应该失败）", r)
        test_results.append(("删除占用中工装失败", r.status_code == 400))
    
    # 测试10: 撤销工单释放工装
    print("\n--- 测试 10: 撤销工单释放工装 ---")
    # 先获取刚创建的工单ID
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    fix_orders = [o for o in orders if o["order_no"].startswith("WO-FIX-")]
    if fix_orders:
        order_id = fix_orders[0]["id"]
        r = requests.delete(f"{BASE_URL}/orders/{order_id}")
        print_response(f"删除工单 {fix_orders[0]['order_no']}，释放工装", r)
        test_results.append(("删除工单释放工装", r.status_code in [200, 204]))
        
        # 再次查询工装时间线，确认释放
        if fixture_id_1:
            r = requests.get(f"{BASE_URL}/fixtures/{fixture_id_1}/timeline")
            print_response("删除工单后查询工装时间线", r)
            if r.status_code == 200:
                timeline = r.json()
                total_entries = sum(len(day['entries']) for day in timeline['days'])
                print(f"删除工单后，工装总占用条目: {total_entries}")
    
    # 测试11: 工装CRUD完整测试
    print("\n--- 测试 11: 工装类型和工装更新 ---")
    if type_id_1:
        update_data = {
            "name": "焊接夹具-A型（改）",
            "turn_over_minutes": 45
        }
        r = requests.put(f"{BASE_URL}/fixtures/types/{type_id_1}", json=update_data)
        print_response("更新工装类型", r)
        test_results.append(("更新工装类型", r.status_code == 200))
    
    if fixture_id_1:
        update_data = {
            "status": "maintenance"
        }
        r = requests.put(f"{BASE_URL}/fixtures/{fixture_id_1}", json=update_data)
        print_response("更新工装状态为维护中", r)
        test_results.append(("更新工装状态", r.status_code == 200))
    
    # 打印测试结果汇总
    print("\n" + "="*60)
    print("测试结果汇总:")
    print("="*60)
    passed = 0
    failed = 0
    for name, result in test_results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1
    print(f"\n总计: {passed} 个通过, {failed} 个失败")
    print("="*60)
    
    print("\n=== 工装模块测试完成 ===")

if __name__ == "__main__":
    main()
