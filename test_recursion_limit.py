import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, SubBatch
)
from app.scheduler import schedule_order, report_step_progress
from app.migrations import run_migrations

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
run_migrations()

db = SessionLocal()

dev1 = Device(name="CNC-1", device_type="CNC", daily_start="08:00", daily_end="20:00", max_batch_size=1)
dev2 = Device(name="HT-1", device_type="热处理", daily_start="08:00", daily_end="20:00", max_batch_size=1)
dev3 = Device(name="QC-1", device_type="质检", daily_start="08:00", daily_end="20:00", max_batch_size=1)
db.add_all([dev1, dev2, dev3])
db.commit()

route = ProcessRoute(product_name="测试产品A")
db.add(route)
db.flush()
step1 = ProcessStep(route_id=route.id, step_order=1, step_name="CNC加工", device_type="CNC", duration_minutes=30)
step2 = ProcessStep(route_id=route.id, step_order=2, step_name="热处理", device_type="热处理", duration_minutes=60)
step3 = ProcessStep(route_id=route.id, step_order=3, step_name="质检", device_type="质检", duration_minutes=15)
db.add_all([step1, step2, step3])
db.commit()

start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)

order = WorkOrder(
    order_no="WO-TEST-RECUR",
    product_name="测试产品A",
    expected_start_time=start,
    deadline=start + timedelta(hours=240),
    total_quantity=2
)
db.add(order)
db.commit()
db.refresh(order)

schedule_order(db, order)
db.refresh(order)

sb = db.query(SubBatch).filter(SubBatch.order_id == order.id).first()
print(f"初始子批次: ID={sb.id}, level={sb.replenish_level}")

print("\n=== 递归补产测试: 每次工序2都全部报废，看能递归几层 ===")
current_sb_id = sb.id
for i in range(5):
    print(f"\n--- 第{i+1}轮: 子批次 {db.query(SubBatch).get(current_sb_id).batch_no} (level={db.query(SubBatch).get(current_sb_id).replenish_level}) ---")
    
    replenish_from = db.query(SubBatch).get(current_sb_id).replenish_from_step or 1
    print(f"  从工序 {replenish_from} 开始上报")
    
    for step_order in range(replenish_from, 3):
        sb_qty = db.query(SubBatch).get(current_sb_id).quantity
        ok, res = report_step_progress(
            db, current_sb_id, None, step_order,
            start + timedelta(minutes=30 + i * 100 + step_order * 20),
            sb_qty
        )
        print(f"  工序{step_order}: ok={ok}")
        if not ok:
            print(f"    错误: {res.get('message')}")
    
    ok, res = report_step_progress(
        db, current_sb_id, None, 3,
        start + timedelta(minutes=90 + i * 100),
        0
    )
    print(f"  工序3 (报废1个): ok={ok}, 补产={res.get('replenishment_created')}")
    
    if not ok:
        print(f"    错误: {res.get('message')}")
        if "3层" in res.get('message', ''):
            print("\n✓ 成功触发补产层数限制，返回人工介入错误")
            break
    
    if res.get('replenishment_created'):
        current_sb_id = res['replenishment_sub_batch_id']
        print(f"  新补产ID={current_sb_id}, No={res['replenishment_batch_no']}")
    else:
        print(f"  未产生补产")
        break

db.close()
