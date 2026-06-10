import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, SubBatch,
    SubBatchStepProgress
)
from app.scheduler import (
    schedule_order, report_step_progress, get_order_summary
)
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
    order_no="WO-TEST-CLEAN",
    product_name="测试产品A",
    expected_start_time=start,
    deadline=start + timedelta(hours=8),
    total_quantity=2
)
db.add(order)
db.commit()
db.refresh(order)

schedule_order(db, order)
db.refresh(order)

sub_batches = db.query(SubBatch).filter(SubBatch.order_id == order.id).all()
print(f"子批次数量: {len(sub_batches)}")
for sb in sub_batches:
    print(f"  ID={sb.id}, No={sb.batch_no}, Qty={sb.quantity}")

print("\n=== 无废品上报所有子批次的所有工序 ===")
for idx, sb in enumerate(sub_batches):
    print(f"\n子批次 {sb.batch_no}:")
    for step_order in [1, 2, 3]:
        ok, res = report_step_progress(
            db, sb.id, None, step_order,
            start + timedelta(minutes=30 + idx * 120 + step_order * 30),
            sb.quantity
        )
        print(f"  工序{step_order}: success={ok}, scrap={res.get('scrap_quantity')}, replenish={res.get('replenishment_created')}")
        if not ok:
            print(f"    错误: {res.get('message')}")

db.refresh(order)
print(f"\n工单状态: {order.status}")

summary = get_order_summary(db, order.id)
print(f"汇总: status={summary['status']}, progress={summary['progress_percent']}%")
print(f"  total_sub_batches={summary['total_sub_batches']}, completed={summary['completed_sub_batches']}")
print(f"  total_steps={summary['total_steps']}, completed_steps={summary['completed_steps']}")

all_sb = db.query(SubBatch).filter(SubBatch.order_id == order.id).all()
print(f"\n所有子批次:")
for sb in all_sb:
    progresses = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sb.id,
        SubBatchStepProgress.is_completed == True
    ).all()
    print(f"  ID={sb.id}, No={sb.batch_no}, status={sb.status}, completed_steps={len(progresses)}/3")

db.close()
