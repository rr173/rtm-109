import requests
import json
from collections import defaultdict

BASE_URL = "http://localhost:9000/api"

def main():
    print("=== 分析设备上的工单分布 ===")
    
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    
    device_orders = defaultdict(list)
    
    for o in orders:
        if o['status'] == 'scheduled' and o['schedule_entries']:
            first_step = min(o['schedule_entries'], key=lambda e: e['start_time'])
            device_id = first_step['device_id']
            device_orders[device_id].append({
                'order_id': o['id'],
                'order_no': o['order_no'],
                'priority': o['priority'],
                'is_locked': o['is_locked'],
                'start_time': first_step['start_time'],
                'end_time': first_step['end_time'],
                'step_name': first_step['step_name']
            })
    
    print("\n设备上的工单（按首工序设备统计）:")
    for device_id, ords in sorted(device_orders.items(), key=lambda x: -len(x[1])):
        print(f"\n设备 {device_id} ({len(ords)} 个工单):")
        ords.sort(key=lambda x: x['start_time'])
        for o in ords:
            lock_icon = "🔒" if o['is_locked'] else "  "
            print(f"  {lock_icon} {o['order_no']:20s} priority={o['priority']:2d} "
                  f"{o['start_time']} ~ {o['end_time']}")
    
    print("\n=== 分析完成 ===")

if __name__ == "__main__":
    main()
