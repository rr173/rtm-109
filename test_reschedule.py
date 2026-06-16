import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.scheduler import reschedule_unlocked_orders
from datetime import datetime
from collections import defaultdict

def check_conflicts(db):
    from app.models import ScheduleEntry
    from sqlalchemy.orm import joinedload
    device_entries = defaultdict(list)
    
    entries = db.query(ScheduleEntry).options(
        joinedload(ScheduleEntry.order)
    ).filter(
            ScheduleEntry.scenario_id.is_(None)
        ).all()
    
    for e in entries:
        device_entries[e.device_id].append(e)
    
    conflicts = []
    for device_id, entries in device_entries.items():
        entries.sort(key=lambda x: x.start_time)
        for i in range(len(entries)-1):
            e1 = entries[i]
            e2 = entries[i+1]
            
            e1_end = e1.end_time
            e2_start = e2.changeover_start_time if e2.changeover_start_time else e2.start_time
            
            if e1_end > e2_start and e1.order_id != e2.order_id:
                overlap = (e1_end - e2_start).total_seconds() / 60
                conflicts.append({
                    'device_id': device_id,
                    'e1_order': e1,
                    'e2_order': e2,
                    'overlap_minutes': overlap
                })
    
    return conflicts

def main():
    db = SessionLocal()
    try:
        print("=== 重排前冲突数:", len(check_conflicts(db)))
        
        print("\n执行 reschedule_unlocked_orders...")
        reschedule_unlocked_orders(db)
        
        print("\n=== 重排后冲突数:", len(check_conflicts(db)))
        
        conflicts = check_conflicts(db)
        if conflicts:
            for c in conflicts:
                e1 = c['e1_order']
                e2 = c['e2_order']
                print(f"  设备{c['device_id']}: {e1.order.order_no} (pri={e1.order.priority}) vs {e2.order.order_no} (pri={e2.order.priority})")
                print(f"    {e1.start_time} ~ {e1.end_time} vs {e2.start_time} ~ {e2.end_time}")
                print(f"    重叠: {c['overlap_minutes']:.0f} 分钟")
        else:
            print("✅ 没有冲突！")
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
