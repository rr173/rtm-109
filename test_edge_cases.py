import requests
import json

BASE_URL = "http://localhost:9000/api"

def main():
    print("=== 测试锁定工单冲突检测 ===")
    
    # 先找出所有锁定的工单
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    
    locked_orders = [o for o in orders if o['is_locked'] and o['status'] == 'scheduled']
    print(f"\n锁定工单: {len(locked_orders)} 个")
    for o in locked_orders:
        if o['schedule_entries']:
            first = min(o['schedule_entries'], key=lambda e: e['start_time'])
            print(f"  {o['order_no']} (ID={o['id']}): 设备{first['device_id']}, "
                  f"{first['start_time']} ~ {first['end_time']}")
    
    # 找一个有锁定工单的设备，然后找一个可以移动到该设备同一时间的工单
    # 实际上，让我们直接测试：找一个锁定工单的设备，然后尝试用另一个工单插单
    # 看看系统是否能检测到冲突
    
    # 让我们用设备19上的锁定工单WO-001来测试
    device_id = 19
    locked_order_no = 'WO-001'
    
    print(f"\n尝试测试设备{device_id}上的锁定冲突...")
    
    # 找一个优先级低于10的工单，我们把它提升到最高优先级，并尝试插单
    # 看看是否会与锁定工单冲突
    
    # 实际上，当前的实现是：先检查与锁定工单的冲突，如果冲突则插单失败
    # 让我们直接验证这个逻辑
    
    # 找一个不在设备19上的工单
    test_order = None
    for o in orders:
        if not o['is_locked'] and o['status'] == 'scheduled' and o['id'] != 1:
            if o['schedule_entries']:
                first = min(o['schedule_entries'], key=lambda e: e['start_time'])
                if first['device_id'] != device_id:
                    test_order = o
                    break
    
    if test_order:
        print(f"选择测试工单: {test_order['order_no']} (ID={test_order['id']})")
        print(f"当前首工序设备: {min(test_order['schedule_entries'], key=lambda e: e['start_time'])['device_id']}")
        
        # 我们无法强制指定工单到哪个设备，所以这个测试可能不太好做
        # 让我们换个思路：直接检查代码逻辑是否正确
    
    # 让我们检查一下插单接口的错误响应
    print("\n=== 测试各种边界情况 ===")
    
    # 测试优先级相同的情况
    print("\n1. 测试优先级相同（无需插单）:")
    r = requests.post(f"{BASE_URL}/insertion/orders", json={
        "order_id": 1,
        "new_priority": 8,  # 已经是8了
        "operator": "test"
    })
    print(f"   状态码: {r.status_code}")
    print(f"   响应: {r.text}")
    
    # 测试降低优先级
    print("\n2. 测试降低优先级:")
    r = requests.post(f"{BASE_URL}/insertion/orders", json={
        "order_id": 2,
        "new_priority": 3,  # 比当前的9低
        "operator": "test"
    })
    print(f"   状态码: {r.status_code}")
    print(f"   响应: {r.text}")
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()
