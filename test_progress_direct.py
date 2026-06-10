import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine, Base
from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, SubBatch,
    SubBatchStepProgress, ScheduleEntry, Material, StepMaterialRequirement
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

print("=== 创建测试数据 ===")

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
    order_no="WO-TEST-001",
    product_name="测试产品A",
    expected_start_time=start,
    deadline=start + timedelta(hours=8),
    total_quantity=3
)
db.add(order)
db.commit()
db.refresh(order)
print(f"工单创建成功: ID={order.id}")

print("\n=== 排产 ===")
result = schedule_order(db, order)
print(f"排产成功: {result['success']}, message={result.get('message')}")
print(f"是否拆分: {result.get('is_split')}, 子批次数: {result.get('total_sub_batches')}")

db.refresh(order)
sub_batches = db.query(SubBatch).filter(SubBatch.order_id == order.id).all()
print(f"\n子批次列表 ({len(sub_batches)} 个):")
for sb in sub_batches:
    print(f"  ID={sb.id}, No={sb.batch_no}, Qty={sb.quantity}, status={sb.status}")

test_sb = sub_batches[0]
sb_id = test_sb.id
sb_qty = test_sb.quantity

print("\n=== 测试1: 跳序上报 (应该失败) ===")
ok, res = report_step_progress(
    db, sub_batch_id=sb_id, order_id=None,
    step_order=2, actual_completion_time=start + timedelta(minutes=60),
    good_quantity=sb_qty
)
print(f"跳序上报工序2 -> success={ok}, msg={res.get('message')}")
assert not ok, "跳序应该失败"

print("\n=== 测试2: 正常上报工序1 (无废品) ===")
ok, res = report_step_progress(
    db, sub_batch_id=sb_id, order_id=None,
    step_order=1, actual_completion_time=start + timedelta(minutes=30),
    good_quantity=sb_qty
)
print(f"上报工序1(良品{sb_qty}) -> success={ok}, 废品={res.get('scrap_quantity')}, 补产={res.get('replenishment_created')}")
assert ok, "应该成功"
assert res.get('scrap_quantity') == 0
assert not res.get('replenishment_created')

print("\n=== 测试3: 重复上报 (应该失败) ===")
ok, res = report_step_progress(
    db, sub_batch_id=sb_id, order_id=None,
    step_order=1, actual_completion_time=start + timedelta(minutes=30),
    good_quantity=sb_qty
)
print(f"重复上报工序1 -> success={ok}, msg={res.get('message')}")
assert not ok, "重复上报应该失败"

print("\n=== 测试4: 上报工序2 (产生1个废品, 触发补产) ===")
good_qty = sb_qty - 1
scrap_qty = 1
ok, res = report_step_progress(
    db, sub_batch_id=sb_id, order_id=None,
    step_order=2, actual_completion_time=start + timedelta(minutes=90),
    good_quantity=good_qty
)
print(f"上报工序2(良品{good_qty},废品{scrap_qty}) -> success={ok}")
print(f"  废品数: {res.get('scrap_quantity')}")
print(f"  是否补产: {res.get('replenishment_created')}")
print(f"  补产批次ID: {res.get('replenishment_sub_batch_id')}")
print(f"  补产批次号: {res.get('replenishment_batch_no')}")
assert ok, "应该成功"
assert res.get('scrap_quantity') == scrap_qty
assert res.get('replenishment_created')
replenish_sb_id = res.get('replenishment_sub_batch_id')

print("\n=== 测试5: 查看补产子批次详情 ===")
sb_progress = get_sub_batch_progress(db, replenish_sb_id)
print(f"补产子批次: No={sb_progress['batch_no']}")
print(f"  is_replenishment={sb_progress['is_replenishment']}")
print(f"  replenish_level={sb_progress['replenish_level']}")
print(f"  replenish_from_step={sb_progress['replenish_from_step']}")
print(f"  parent_sub_batch_id={sb_progress['parent_sub_batch_id']}")
print(f"  已完成工序: {sb_progress['completed_steps']} / {sb_progress['total_steps']}")
for sd in sb_progress['step_details']:
    print(f"    工序{sd['step_order']} {sd['step_name']}: completed={sd['is_completed']}, good={sd['good_quantity']}, scrap={sd['scrap_quantity']}")
assert sb_progress['is_replenishment']
assert sb_progress['replenish_level'] == 1
assert sb_progress['replenish_from_step'] == 2
assert sb_progress['completed_steps'] >= 1

print("\n=== 测试6: 上报工序3 (完成原批次) ===")
ok, res = report_step_progress(
    db, sub_batch_id=sb_id, order_id=None,
    step_order=3, actual_completion_time=start + timedelta(minutes=120),
    good_quantity=good_qty
)
print(f"上报工序3(良品{good_qty}) -> success={ok}, 批次状态={db.query(SubBatch).get(sb_id).status}")
assert ok

print("\n=== 测试7: 完成补产子批次所有工序 ===")
for step_order in [2, 3]:
    ok, res = report_step_progress(
        db, sub_batch_id=replenish_sb_id, order_id=None,
        step_order=step_order,
        actual_completion_time=start + timedelta(minutes=150 + step_order * 20),
        good_quantity=scrap_qty
    )
    print(f"补产批次上报工序{step_order} -> success={ok}, msg={res.get('message')}")
    assert ok, f"补产上报工序{step_order}应该成功"

print("\n=== 测试8: 完成其他原始子批次 ===")
for sb in sub_batches[1:]:
    other_sb_id = sb.id
    other_qty = sb.quantity
    for step_order in [1, 2, 3]:
        ok, res = report_step_progress(
            db, sub_batch_id=other_sb_id, order_id=None,
            step_order=step_order,
            actual_completion_time=start + timedelta(minutes=180 + step_order * 20),
            good_quantity=other_qty
        )
        assert ok, f"批次{sb.batch_no}工序{step_order}应该成功"
    print(f"批次 {sb.batch_no} 完成")

print("\n=== 测试9: 检查工单状态 ===")
summary = get_order_summary(db, order.id)
print(f"工单状态: {summary['status']}")
print(f"进度: {summary['progress_percent']}%")
print(f"总批次: {summary['total_sub_batches']}, 已完成: {summary['completed_sub_batches']}")
print(f"总工序: {summary['total_steps']}, 已完成: {summary['completed_steps']}")
assert summary['status'] == 'completed', f"工单应该已完成, 实际是 {summary['status']}"
assert summary['progress_percent'] == 100.0, f"进度应该100%, 实际是 {summary['progress_percent']}"

print("\n" + "="*60)
print("所有测试通过! ✓")
print("="*60)

db.close()
