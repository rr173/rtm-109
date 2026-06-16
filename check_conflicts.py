import requests
from collections import defaultdict
from datetime import datetime

BASE_URL = "http://localhost:9000/api"

def parse_dt(s):
    return datetime.fromisoformat(s)

def main():
    print("=== 验证设备独占问题 ===\n")
    
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    
    # 按设备分组，检查每个设备上的排产是否有重叠
    device_entries = defaultdict(list)
    
    for o in orders:
        if o['status'] == 'scheduled' and o['schedule_entries']:
            for e in o['schedule_entries']:
                device_entries[e['device_id']].append({
                    'order_id': o['id'],
                    'order_no': o['order_no'],
                    'priority': o['priority'],
                    'is_locked': o['is_locked'],
                    'step_order': e['step_order'],
                    'step_name': e['step_name'],
                    'start': parse_dt(e['start_time']),
                    'end': parse_dt(e['end_time']),
                    'changeover_start': parse_dt(e['changeover_start_time']) if e.get('changeover_start_time') else None,
                })
    
    conflicts = []
    for device_id, entries in device_entries.items():
        entries.sort(key=lambda x: x['start'])
        for i in range(len(entries)-1):
            e1 = entries[i]
            e2 = entries[i+1]
            # 检查是否重叠（考虑换型时间）
            e1_effective_end = e1['end']
            e2_effective_start = e2['changeover_start'] if e2['changeover_start'] else e2['start']
            
            if e1_effective_end > e2_effective_start:
                overlap = (e1_effective_end - e2_effective_start).total_seconds() / 60
                conflicts.append({
                    'device_id': device_id,
                    'e1': e1,
                    'e2': e2,
                    'overlap_minutes': overlap
                })
    
    if conflicts:
        print(f"发现 {len(conflicts)} 个设备时间冲突:\n")
        for c in conflicts:
            e1 = c['e1']
            e2 = c['e2']
            print(f"设备 {c['device_id']}:")
            print(f"  {e1['order_no']} (priority={e1['priority']}, step={e1['step_order']}): "
                  f"{e1['start']} ~ {e1['end']}")
            print(f"  {e2['order_no']} (priority={e2['priority']}, step={e2['step_order']}): "
                  f"{e2['start']} ~ {e2['end']}")
            print(f"  重叠: {c['overlap_minutes']:.0f} 分钟")
            print()
    else:
        print("✅ 没有发现设备时间冲突")
    
    print("\n=== 设备2详情 ===")
    if 2 in device_entries:
        entries = sorted(device_entries[2], key=lambda x: x['start'])
        for e in entries:
            print(f"  {e['order_no']:15s} pri={e['priority']:2d} step{e['step_order']} "
                  f"{e['start'].strftime('%m-%d %H:%M')} ~ {e['end'].strftime('%H:%M')}")

if __name__ == "__main__":
    main()
