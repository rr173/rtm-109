import requests
import json

BASE_URL = "http://localhost:9000/api"

def main():
    print("=" * 60)
    print("级联重排与锁定冲突测试")
    print("=" * 60)
    
    # 先查看设备2上的工单详情
    print("\n1. 设备2上的工单详情:")
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    
    device2_orders = []
    for o in orders:
        if o['status'] == 'scheduled' and o['schedule_entries']:
            first = min(o['schedule_entries'], key=lambda e: e['start_time'])
            if first['device_id'] == 2 and o['id'] != 2:  # 排除WO-B
                device2_orders.append(o)
    
    device2_orders.sort(key=lambda o: min(o['schedule_entries'], key=lambda e: e['start_time'])['start_time'])
    
    for o in device2_orders:
        first = min(o['schedule_entries'], key=lambda e: e['start_time'])
        print(f"   ID={o['id']:2d} {o['order_no']:15s} priority={o['priority']} "
              f"{first['start_time']} ~ {first['end_time']} product={o['product_name']}")
    
    if len(device2_orders) >= 2:
        # 选择后面的一个工单进行插单测试
        test_order = device2_orders[-1]  # 选时间最后的
        print(f"\n2. 选择工单 {test_order['order_no']} (ID={test_order['id']}) 进行插单测试")
        print(f"   当前优先级: {test_order['priority']}, 当前首工序开始时间: {min(test_order['schedule_entries'], key=lambda e: e['start_time'])['start_time']}")
        
        print(f"\n3. 执行插单 (优先级 {test_order['priority']} → 9):")
        r = requests.post(f"{BASE_URL}/insertion/orders", json={
            "order_id": test_order['id'],
            "new_priority": 9,
            "operator": "测试员",
            "reason": "级联重排测试"
        })
        
        if r.status_code == 200:
            result = r.json()
            print(f"   成功: {result['success']}")
            print(f"   消息: {result['message']}")
            print(f"   受影响工单: {len(result['affected_orders'])} 个")
            print(f"   延迟: {result['delayed_count']} 个, 受阻: {result['blocked_count']} 个")
            
            if result['affected_orders']:
                print("   受影响工单列表:")
                for ao in result['affected_orders']:
                    print(f"     - {ao['order_no']}: {ao['impact_type']}, 延迟{ao['delay_minutes']}分钟")
        else:
            print(f"   状态码: {r.status_code}")
            print(f"   响应: {r.text}")
    
    # 测试锁定工单冲突
    print("\n4. 测试锁定工单冲突:")
    print("   设备19上有锁定工单 WO-001")
    
    # 找一个在设备19上的非锁定工单
    device19_orders = []
    for o in orders:
        if o['status'] == 'scheduled' and o['schedule_entries'] and not o['is_locked']:
            first = min(o['schedule_entries'], key=lambda e: e['start_time'])
            if first['device_id'] == 19:
                device19_orders.append(o)
    
    if device19_orders:
        test_order = device19_orders[0]
        print(f"   选择工单 {test_order['order_no']} (ID={test_order['id']}) 进行测试")
        
        # 把它的开始时间调整到和锁定工单冲突的时间？
        # 或者直接插单看看会不会冲突
        
        # 先看看锁定工单WO-001的时间
        wo001 = None
        for o in orders:
            if o['order_no'] == 'WO-001':
                wo001 = o
                break
        
        if wo001:
            first_locked = min(wo001['schedule_entries'], key=lambda e: e['start_time'])
            print(f"   锁定工单 WO-001: {first_locked['start_time']} ~ {first_locked['end_time']}")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
