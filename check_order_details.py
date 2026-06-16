import requests
import json

BASE_URL = "http://localhost:9000/api"

def main():
    print("=== 检查设备2上的工单排产详情 ===\n")
    
    # 检查设备2上的几个工单
    order_ids = [18, 19, 20, 21]  # WO-002, WO-003, WO-004, WO-005
    
    for oid in order_ids:
        r = requests.get(f"{BASE_URL}/orders/{oid}")
        o = r.json()
        print(f"工单: {o['order_no']} (ID={o['id']}), priority={o['priority']}, product={o['product_name']}")
        print(f"  工序列表:")
        for e in sorted(o['schedule_entries'], key=lambda x: x['step_order']):
            print(f"    步骤{e['step_order']}: {e['step_name']:10s} "
                  f"设备{e['device_id']:2d} "
                  f"{e['start_time']} ~ {e['end_time']}")
        print()
    
    print("=== 检查插单后的变化 ===")
    print("WO-003 插单前时间: 10:00-11:00 (priority 5)")
    print("WO-003 插单后时间: 需要确认")
    
    # 再检查一下WO-003的当前状态
    r = requests.get(f"{BASE_URL}/orders/19")
    o = r.json()
    first = min(o['schedule_entries'], key=lambda e: e['start_time'])
    print(f"\nWO-003 当前首工序: 设备{first['device_id']}, {first['start_time']} ~ {first['end_time']}")

if __name__ == "__main__":
    main()
