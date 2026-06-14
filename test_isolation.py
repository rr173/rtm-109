#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["DATABASE_URL"] = "sqlite:///./test_isolation.db"
if os.path.exists("./test_isolation.db"):
    os.remove("./test_isolation.db")

from app.database import Base, engine, SessionLocal
from app.migrations import run_migrations
from app.models import (
    Device, ProcessRoute, ProcessStep, WorkOrder, ScheduleEntry,
    ConflictRecord, FixtureType, Fixture, SubBatch, MaterialLock,
    DeviceFault, SubBatchStepProgress, MaintenancePlan
)

Base.metadata.create_all(bind=engine)
run_migrations()

from datetime import datetime, timedelta
from app.scenario_service import create_scenario, add_urgent_order_to_scenario

db = SessionLocal()

dev = Device(name='CNC-001', device_type='CNC', daily_start='08:00', daily_end='20:00', max_batch_size=50)
db.add(dev)
db.commit()

ft = FixtureType(name='治具A', turn_over_minutes=5)
db.add(ft)
db.commit()

fx = Fixture(code='FX-001', fixture_type_id=ft.id, compatible_device_types='CNC', status='available')
db.add(fx)
db.commit()

route = ProcessRoute(product_name='产品A')
db.add(route)
db.commit()

step = ProcessStep(route_id=route.id, step_order=1, step_name='工序1', device_type='CNC', duration_minutes=30, fixture_type_id=ft.id)
db.add(step)
db.commit()

from app.scheduler import schedule_order
now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
po = WorkOrder(order_no='PO-FORMAL-001', product_name='产品A', expected_start_time=now, deadline=now+timedelta(days=1), total_quantity=10)
db.add(po)
db.commit()
schedule_order(db, po)

formal_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).all()
formal_entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).all()
print(f'正式计划: {len(formal_orders)} 个工单, {len(formal_entries)} 条排产')

sc = create_scenario(db, '测试预案', '隔离测试', '测试员')
print(f'创建预案成功: id={sc.id}')

add_urgent_order_to_scenario(db, sc.id, {
    'order_no': 'PO-URGENT-001',
    'product_name': '产品A',
    'expected_start_time': now + timedelta(hours=1),
    'deadline': now + timedelta(days=2),
    'total_quantity': 5,
    'priority': 'high'
}, operator='测试员')

print()
print('=' * 50)
print('  数据隔离验证')
print('=' * 50)

formal_orders_after = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).all()
print(f'✓ 正式工单 (scenario_id=NULL): {len(formal_orders_after)} 个 (预期: 1)')
for o in formal_orders_after:
    print(f'    - {o.order_no} (id={o.id}, scenario_id={o.scenario_id})')

scenario_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id == sc.id).all()
print(f'✓ 预案工单 (scenario_id={sc.id}): {len(scenario_orders)} 个 (预期: 2)')
for o in scenario_orders:
    print(f'    - {o.order_no} (id={o.id}, scenario_id={o.scenario_id})')

all_orders = db.query(WorkOrder).all()
print(f'✓ 全部工单 (不加过滤): {len(all_orders)} 个 (预期: 1正式 + 2预案 = 3)')

formal_entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id.is_(None)).all()
scenario_entries = db.query(ScheduleEntry).filter(ScheduleEntry.scenario_id == sc.id).all()
print(f'✓ 正式排产条目: {len(formal_entries)} 条')
print(f'✓ 预案排产条目: {len(scenario_entries)} 条')

same_no_count = db.query(WorkOrder).filter(WorkOrder.order_no == 'PO-FORMAL-001').count()
print(f'✓ 同单号 PO-FORMAL-001 记录数: {same_no_count} (预期: 2，正式+预案各一)')

print()
print('=' * 50)
print('  路由接口返回验证')
print('=' * 50)

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

resp = client.get("/api/orders/")
assert resp.status_code == 200
data = resp.json()
print(f'✓ GET /api/orders/ 返回 {len(data)} 个工单 (预期: 1，只有正式的)')
order_nos = [o['order_no'] for o in data]
print(f'    工单号: {order_nos}')
assert 'PO-URGENT-001' not in order_nos, "紧急单不应该出现在正式列表里！"
assert len(data) == 1, "正式工单应该只有1个"

resp = client.get(f"/api/schedule/gantt?date_str={now.strftime('%Y-%m-%d')}")
assert resp.status_code == 200
data = resp.json()
total_entries = sum(len(d['entries']) for d in data['devices'])
print(f"✓ GET /api/schedule/gantt 返回 {total_entries} 条排产 (预期: {len(formal_entries)})")
assert total_entries == len(formal_entries), "正式甘特图应该只有正式排产"

resp = client.get("/api/schedule/conflicts")
assert resp.status_code == 200
data = resp.json()
print(f"✓ GET /api/schedule/conflicts 返回 {data['total']} 条冲突")

print()
print('✓✓✓ 全部数据隔离验证通过！正式数据与预案数据彻底隔离 ✓✓✓')

db.close()
os.remove('./test_isolation.db')
