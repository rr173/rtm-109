import requests
import json

BASE_URL = "http://localhost:9000/api"

def main():
    print("=== 测试插单功能 ===")
    
    print("\n1. 查看所有工单:")
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    scheduled_orders = [o for o in orders if o["status"] == "scheduled"]
    print(f"  总工单: {len(orders)}, 已排产: {len(scheduled_orders)}")
    
    for o in scheduled_orders[:5]:
        print(f"    ID={o['id']}, NO={o['order_no']}, priority={o['priority']}, locked={o['is_locked']}")
    
    if len(scheduled_orders) >= 2:
        order1 = scheduled_orders[0]
        order2 = scheduled_orders[1]
        
        print(f"\n2. 选择两个工单进行测试:")
        print(f"  工单A: {order1['order_no']} (ID={order1['id']}, priority={order1['priority']})")
        print(f"  工单B: {order2['order_no']} (ID={order2['id']}, priority={order2['priority']})")
        
        r1 = requests.get(f"{BASE_URL}/orders/{order1['id']}")
        o1 = r1.json()
        r2 = requests.get(f"{BASE_URL}/orders/{order2['id']}")
        o2 = r2.json()
        
        if o1["schedule_entries"]:
            first1 = min(o1["schedule_entries"], key=lambda e: e["start_time"])
            print(f"  工单A首工序: {first1['step_name']} at {first1['start_time']} ~ {first1['end_time']} (device {first1['device_id']}")
        
        if o2["schedule_entries"]:
            first2 = min(o2["schedule_entries"], key=lambda e: e["start_time"])
            print(f"  工单B首工序: {first2['step_name']} at {first2['start_time']} ~ {first2['end_time']} (device {first2['device_id']})")
        
        print(f"\n3. 对工单B执行插单 (priority 5→9):")
        insertion_req = {
            "order_id": order2["id"],
            "new_priority": 9,
            "operator": "test_user",
            "reason": "紧急插单测试"
        }
        r_insert = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_req)
        print(f"  状态码: {r_insert.status_code}")
        result = r_insert.json()
        print(f"  结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
        if r_insert.status_code == 200:
            print(f"\n4. 插单成功！查看结果:")
            print(f"  工单B新优先级: {result.get('new_priority')}")
            print(f"  受影响工单数量: {len(result.get('affected_orders', []))}")
            print(f"  延迟工单数量: {result.get('delayed_count')}")
            print(f"  受阻工单数量: {result.get('blocked_count')}")
            
            affected = result.get("affected_orders", [])
            for a in affected:
                print(f"    - {a['order_no']}: {a['impact_type']}, 延迟{a['delay_minutes']}分钟")
    
    print("\n5. 查看插单历史:")
    r_history = requests.get(f"{BASE_URL}/insertion/history", params={"limit": 5})
    history = r_history.json()
    print(f"  总记录数: {history['total']}")
    for h in history["histories"][:3]:
        print(f"    ID={h['id']}, {h['order_no']}: {h['old_priority']}→{h['new_priority']}, at {h['created_at']}")
    
    if history["histories"]:
        latest_id = history["histories"][0]["id"]
        print(f"\n6. 查看最新插单详情 (ID={latest_id}):")
        r_detail = requests.get(f"{BASE_URL}/insertion/history/{latest_id}")
        detail = r_detail.json()
        print(f"  {json.dumps(detail, indent=2, ensure_ascii=False)}")
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()
