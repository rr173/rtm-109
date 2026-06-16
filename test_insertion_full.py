import requests
import json

BASE_URL = "http://localhost:9000/api"

def pprint(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))

def main():
    print("=" * 60)
    print("工单优先级动态插单与级联重排模块 - 功能测试")
    print("=" * 60)
    
    # 1. 查看插单历史
    print("\n1. 查看插单历史记录")
    r = requests.get(f"{BASE_URL}/insertion/history", params={"limit": 10})
    history = r.json()
    print(f"   总记录数: {history['total']}")
    for h in history['histories']:
        print(f"   - ID={h['id']}: {h['order_no']} {h['old_priority']}→{h['new_priority']} "
              f"by {h.get('operator', 'unknown')} at {h['created_at']}")
    
    # 2. 查看最新插单详情
    if history['histories']:
        latest_id = history['histories'][0]['id']
        print(f"\n2. 查看最新插单详情 (ID={latest_id})")
        r = requests.get(f"{BASE_URL}/insertion/history/{latest_id}")
        detail = r.json()
        print(f"   工单: {detail['order_no']}")
        print(f"   原优先级: {detail['old_priority']} → 新优先级: {detail['new_priority']}")
        print(f"   操作人: {detail.get('operator', 'N/A')}")
        print(f"   原因: {detail.get('reason', 'N/A')}")
        print(f"   受影响工单: {detail['affected_orders_count']} "
              f"(延迟{detail['delayed_orders_count']}, 受阻{detail['blocked_orders_count']})")
        if detail.get('affected_orders'):
            print("   受影响工单列表:")
            for ao in detail['affected_orders']:
                print(f"     - {ao['affected_order_no']}: {ao['impact_type']} "
                      f"延迟{ao['delay_minutes']}分钟")
    
    # 3. 测试防抖动机制
    print("\n3. 测试防抖动机制（10分钟间隔限制）")
    print("   对工单1再次插单（提升到10）...")
    r = requests.post(f"{BASE_URL}/insertion/orders", json={
        "order_id": 1,
        "new_priority": 10,
        "operator": "test",
        "reason": "再次插单测试"
    })
    result = r.json()
    if r.status_code == 429 or 'detail' in result:
        print(f"   ✓ 防抖动生效: {result.get('detail', str(result))}")
    else:
        print(f"   结果: {result}")
    
    # 4. 测试无效优先级
    print("\n4. 测试无效优先级（超出1-10范围）")
    r = requests.post(f"{BASE_URL}/insertion/orders", json={
        "order_id": 3,
        "new_priority": 15,
        "operator": "test"
    })
    print(f"   状态码: {r.status_code}")
    if r.status_code != 200:
        print(f"   错误信息: {r.text[:100]}")
    
    # 5. 测试不存在的工单
    print("\n5. 测试不存在的工单")
    r = requests.post(f"{BASE_URL}/insertion/orders", json={
        "order_id": 99999,
        "new_priority": 8,
        "operator": "test"
    })
    print(f"   状态码: {r.status_code}")
    print(f"   响应: {r.text[:100]}")
    
    # 6. 按时间范围查询插单历史
    print("\n6. 按时间范围查询插单历史")
    r = requests.get(f"{BASE_URL}/insertion/history", params={
        "start_time": "2020-01-01T00:00:00",
        "end_time": "2030-12-31T23:59:59",
        "limit": 5
    })
    result = r.json()
    print(f"   查询结果: {result['total']} 条记录")
    
    # 7. 查看所有工单的当前优先级
    print("\n7. 查看部分工单的当前优先级")
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    for o in orders[:10]:
        status_icon = "🔒" if o['is_locked'] else "  "
        print(f"   {status_icon} ID={o['id']:2d} {o['order_no']:20s} priority={o['priority']:2d} status={o['status']}")
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
