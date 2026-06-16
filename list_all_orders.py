import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.models import WorkOrder, ScheduleEntry
from sqlalchemy.orm import joinedload

def main():
    db = SessionLocal()
    try:
        orders = db.query(WorkOrder).filter(
            WorkOrder.scenario_id.is_(None)
        ).order_by(WorkOrder.id.asc()).all()
        
        print(f"共有 {len(orders)} 个工单\n")
        print(f"{'ID':4s} {'工单号':25s} {'优先级':4s} {'状态':10s} {'锁定':4s} {'首工序设备':8s} {'首工序时间':20s}")
        print("-" * 90)
        
        for o in orders:
            first_entry = db.query(ScheduleEntry).filter(
                ScheduleEntry.order_id == o.id
            ).order_by(ScheduleEntry.start_time.asc()).first()
            
            device_id = first_entry.device_id if first_entry else "N/A"
            start_time = first_entry.start_time.strftime('%m-%d %H:%M') if first_entry else "N/A"
            
            print(f"{o.id:<4d} {o.order_no:<25s} {o.priority:<4d} {o.status:<10s} "
                  f"{'是' if o.is_locked else '否':<4s} {str(device_id):<8s} {start_time:<20s}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main()
