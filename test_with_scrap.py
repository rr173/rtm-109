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
    schedule_order, report_step_progress, get_order_summary,
    get_sub_batch_progress
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
    order_no="WO-TEST-SCRAP",
    product_name="测试产品A",
    expected_start_time=start,
    deadline=start + timedelta(hours=24),
    total_quantity=2
)
db.add(order)
db.commit()
db.refresh(order)

result = schedule_order(db, order)
print(f"排产: success={result['success']}, split={result.get('is_split')}, batches={result.get('total_sub_batches')}")
db.refresh(order)

def get_active_sub_batches():
    return db.query(SubBatch).filter(
        SubBatch.order_id == order.id,
        SubBatch.status != "cancelled"
    ).all()

def get_incomplete_steps(sb_id):
    sb = db.query(SubBatch).get(sb_id)
    replenish_from = sb.replenish_from_step or 1
    progresses = db.query(SubBatchStepProgress).filter(
        SubBatchStepProgress.sub_batch_id == sb_id,
        SubBatchStepProgress.is_completed == True
    ).all()
    completed_orders = {p.step_order for p in progresses}
    incomplete = []
    for s in range(replenish_from, 4):
        if s not in completed_orders:
            incomplete.append(s)
    return incomplete

sub_batches = get_active_sub_batches()
print(f"\n初始子批次: {len(sub_batches)}")

initial_sb_ids = [sb.id for sb in sub_batches]

print("\n=== 初始子批次1: 工序1全良品, 工序2报废1个, 工序3全良品 ===")
sb0 = sub_batches[0]
# 工序1: 全良品
ok, res = report_step_progress(db, sb0.id, None, 1, start + timedelta(minutes=30), sb0.quantity)
print(f"  工序1: ok={ok}, good={sb0.quantity}, scrap={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}")

# 工序2: 报废1个 (因为数量=1, 所以良品=0, 报废=1)
ok, res = report_step_progress(db, sb0.id, None, 2, start + timedelta(minutes=90), 0)
print(f"  工序2: ok={ok}, good=0, scrap={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}, 补产ID={res.get('replenishment_sub_batch_id')}")

# 工序3: 完成 (数量=0个良品，但 sb.quantity=1，所以报废=1个，又会触发从工序3开始的补产!)
ok, res = report_step_progress(db, sb0.id, None, 3, start + timedelta(minutes=120), 0)
print(f"  工序3: ok={ok}, good=0, scrap={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}, 补产ID={res.get('replenishment_sub_batch_id')}")

print("\n=== 初始子批次2: 全部无废品 ===")
sb1 = sub_batches[1]
for step_order in [1, 2, 3]:
    ok, res = report_step_progress(
        db, sb1.id, None, step_order,
        start + timedelta(minutes=150 + step_order * 30),
        sb1.quantity
    )
    print(f"  工序{step_order}: ok={ok}, scrap={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}")

print("\n=== 递归完成所有补产子批次 ===")
t = 300
while True:
    all_sb = get_active_sub_batches()
    found_pending = False
    for sb in all_sb:
        incomplete = get_incomplete_steps(sb.id)
        if incomplete:
            found_pending = True
            for step_order in incomplete:
                ok, res = report_step_progress(
                    db, sb.id, None, step_order,
                    start + timedelta(minutes=t),
                    sb.quantity
                )
                print(f"  补产批次 {sb.batch_no} 工序{step_order}: ok={ok}, scrap={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}")
                t += 30
                if res.get('replenishment_created'):
                    print(f"    -> 产生新补产: {res.get('replenishment_batch_no')}")
            db.commit()
    if not found_pending:
        break

print("\n=== 最终工单状态 ===")
summary = get_order_summary(db, order.id)
print(f"状态: {summary['status']}")
print(f"进度: {summary['progress_percent']}%")
print(f"子批次: {summary['completed_sub_batches']}/{summary['total_sub_batches']} 完成")
print(f"工序: {summary['completed_steps']}/{summary['total_sub_batches'] * summary['total_steps']} 完成")

all_sb = get_active_sub_batches()
print(f"\n所有子批次详情 ({len(all_sb)} 个):")
for sb in all_sb:
    prog = get_sub_batch_progress(db, sb.id)
    print(f"  {prog['batch_no']} (ID={sb.id}, repl={prog['is_replenishment']}, lvl={prog['replenish_level']}): "
          f"status={sb.status}, steps={prog['completed_steps']}/{prog['total_steps']}")

assert summary['status'] == 'completed', f"工单应该已完成, 实际是 {summary['status']}"
assert summary['progress_percent'] == 100.0, f"进度应该是100%, 实际是 {summary['progress_percent']}"

print("\n" + "="*60)
print("所有测试通过! 有废品+递归补产的场景也能正常完成!")
print("="*60)

db.close()
