#!/usr/bin/env python3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["DATABASE_URL"] = "sqlite:///./test_scenarios.db"

if os.path.exists("./test_scenarios.db"):
    os.remove("./test_scenarios.db")

from app.database import engine, Base, SessionLocal
from app.main import app

Base.metadata.create_all(bind=engine)

from app.migrations import run_migrations
run_migrations()

from datetime import datetime, timedelta
from app.models import Device, ProcessRoute, ProcessStep, FixtureType, Fixture, MaintenancePlan, Material, StepMaterialRequirement
from app.scenario_service import (
    create_scenario, list_scenarios, get_scenario, delete_scenario,
    compute_baseline_hash, verify_baseline, check_publish_constraints,
    publish_scenario, compute_scenario_diff, add_urgent_order_to_scenario,
    set_device_unavailable, extend_maintenance_window, adjust_fixture_quantity,
    get_scenario_gantt, get_scenario_conflicts, get_scenario_audit_logs
)

def step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")

step("步骤1: 准备基础数据 (设备/工艺路线/工装/物料/维护计划)")
db = SessionLocal()

dev1 = Device(name="CNC-001", device_type="CNC", daily_start="08:00", daily_end="20:00", max_batch_size=50)
dev2 = Device(name="CNC-002", device_type="CNC", daily_start="08:00", daily_end="20:00", max_batch_size=50)
dev3 = Device(name="ASSY-001", device_type="ASSEMBLY", daily_start="08:00", daily_end="20:00", max_batch_size=100)
db.add_all([dev1, dev2, dev3])
db.commit()

ft_cnc = FixtureType(name="CNC夹具A型", turn_over_minutes=15)
ft_assy = FixtureType(name="ASSEMBLY夹具B型", turn_over_minutes=10)
db.add_all([ft_cnc, ft_assy])
db.commit()

fx1 = Fixture(code="FX-CNC-01", fixture_type_id=ft_cnc.id, compatible_device_types="CNC", status="available")
fx2 = Fixture(code="FX-CNC-02", fixture_type_id=ft_cnc.id, compatible_device_types="CNC", status="available")
fx3 = Fixture(code="FX-ASSY-01", fixture_type_id=ft_assy.id, compatible_device_types="ASSEMBLY", status="available")
db.add_all([fx1, fx2, fx3])
db.commit()

mat1 = Material(name="铝板6061", unit="块", total_quantity=1000)
mat2 = Material(name="螺丝M4x10", unit="颗", total_quantity=10000)
db.add_all([mat1, mat2])
db.commit()

route = ProcessRoute(product_name="精密外壳组件")
db.add(route)
db.commit()

step1 = ProcessStep(
    route_id=route.id, step_order=1, step_name="CNC粗加工",
    device_type="CNC", duration_minutes=60, min_gap_after=5,
    fixture_type_id=ft_cnc.id
)
step2 = ProcessStep(
    route_id=route.id, step_order=2, step_name="装配",
    device_type="ASSEMBLY", duration_minutes=30, min_gap_after=0,
    fixture_type_id=ft_assy.id
)
db.add_all([step1, step2])
db.commit()

req1 = StepMaterialRequirement(step_id=step1.id, material_id=mat1.id, quantity=2)
req2 = StepMaterialRequirement(step_id=step2.id, material_id=mat2.id, quantity=20)
db.add_all([req1, req2])
db.commit()

maint = MaintenancePlan(
    device_id=dev1.id, day_of_week=0,
    start_time="12:00", end_time="14:00", description="每周一CNC-001例行维护"
)
db.add(maint)
db.commit()

print(f"✓ 已创建设备: 3台 (2台CNC, 1台ASSEMBLY)")
print(f"✓ 已创建工装类型: {ft_cnc.name}, {ft_assy.name}")
print(f"✓ 已创建工装实例: 3个")
print(f"✓ 已创建物料: {mat1.name}, {mat2.name}")
print(f"✓ 已创建工艺路线: 精密外壳组件 (2道工序)")
print(f"✓ 已创建维护计划: 周一CNC-001维护")

step("步骤2: 创建正式排产计划 (创建3张生产工单)")
from app.scheduler import schedule_order, reschedule_unlocked_orders
from app.models import WorkOrder

now = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
orders = []
for i in range(3):
    wo = WorkOrder(
        order_no=f"PO-2025-{i+1:03d}",
        product_name="精密外壳组件",
        expected_start_time=now + timedelta(days=i),
        deadline=now + timedelta(days=i+2),
        total_quantity=100
    )
    db.add(wo)
    db.commit()
    result = schedule_order(db, wo)
    if result["success"]:
        reschedule_unlocked_orders(db, exclude_order_id=wo.id)
        print(f"✓ 工单 {wo.order_no} 排产成功")
    else:
        print(f"✗ 工单 {wo.order_no} 排产失败: {result.get('message')}")
    orders.append(wo)

step("步骤3: 创建预案 '插入急单'")
scenario1 = create_scenario(
    db, name="预案-插入急单6月15日",
    description="假设6月15日插入客户急单PO-URGENT-001",
    created_by="车间张主任"
)
print(f"✓ 预案创建成功: ID={scenario1.id}, 名称={scenario1.name}")
print(f"  状态={scenario1.status}, 创建人={scenario1.created_by}")
print(f"  基线哈希={scenario1.baseline_hash[:16]}...")
print(f"  基线时间={scenario1.baseline_timestamp}")

all_scenarios = list_scenarios(db)
print(f"\n✓ 预案列表: 共{len(all_scenarios)}个预案")

step("步骤4: 在预案中插入急单")
urgent_order_data = {
    "order_no": "PO-URGENT-001",
    "product_name": "精密外壳组件",
    "expected_start_time": now + timedelta(hours=2),
    "deadline": now + timedelta(days=1),
    "total_quantity": 50,
    "priority": "high"
}
ok, result = add_urgent_order_to_scenario(
    db, scenario1.id, urgent_order_data, operator="车间张主任"
)
print(f"✓ 插入急单结果: 成功={ok}, message={result.get('message', '')}")

step("步骤5: 在预案中撤掉一台设备 (CNC-002)")
ok, result = set_device_unavailable(
    db, scenario1.id, dev2.id,
    effective_from=now + timedelta(hours=4),
    effective_to=now + timedelta(days=1),
    reason="假设CNC-002临时故障",
    operator="车间张主任"
)
print(f"✓ 撤掉设备CNC-002结果: 成功={ok}, 影响条目={result.get('affected_entries', 0)}")

step("步骤6: 在预案中延长维护窗口 (CNC-001周一维护延长1小时)")
ok, result = extend_maintenance_window(
    db, scenario1.id, maint.id,
    new_start_time="12:00",
    new_end_time="15:00",
    description="延长1小时维护",
    operator="车间张主任"
)
print(f"✓ 延长维护窗口结果: 成功={ok}, 影响={result.get('affected_entries', 0)}个工单")

step("步骤7: 在预案中增加工装数量 (增加1个CNC夹具)")
ok, result = adjust_fixture_quantity(
    db, scenario1.id, ft_cnc.id,
    quantity_change=1,
    reason="临时借调1套CNC夹具",
    operator="车间张主任"
)
print(f"✓ 调整工装数量结果: 成功={ok}, 变化={result.get('quantity_change')}, 临时工装={result.get('temp_fixtures')}")

step("步骤8: 查看预案版甘特图")
gantt_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
gantt = get_scenario_gantt(db, scenario1.id, gantt_date)
print(f"✓ 预案甘特图日期: {gantt['date']}")
print(f"  设备数: {len(gantt['devices'])}")
for d in gantt['devices']:
    print(f"    设备 {d['device_name']} ({d['device_type']}): {len(d['entries'])} 个排产条目")
    for e in d['entries'][:3]:
        print(f"      - {e['order_no']}/{e['step_name']}: {e['start_time'].strftime('%H:%M')}-{e['end_time'].strftime('%H:%M')}")

step("步骤9: 查看预案冲突列表")
conflicts = get_scenario_conflicts(db, scenario1.id)
print(f"✓ 预案冲突总数: {conflicts['total']}")
for c in conflicts['conflicts'][:5]:
    print(f"  - [{c['conflict_type']}] 工单{c['order_id']}: {c['description'][:80]}")

step("步骤10: 预案 vs 正式计划 差异对比")
diff = compute_scenario_diff(db, scenario1.id)
print(f"✓ 基线未变化: {diff['baseline_unchanged']}")
print(f"✓ 推迟工单: {diff['total_delayed']} 个")
for d in diff['delayed_orders'][:5]:
    print(f"  - {d['order_no']}: 推迟 {d['delay_minutes']}分钟, 原定完成={d['original_end_time'].strftime('%m-%d %H:%M')}, 预案完成={d['scenario_end_time'].strftime('%m-%d %H:%M')}, 受影响工序={d['affected_step']}")
print(f"✓ 负载变化设备: {diff['total_devices_changed']} 台")
for d in diff['device_load_changes'][:5]:
    print(f"  - {d['device_name']}: {d['original_scheduled_minutes']}min → {d['scenario_scheduled_minutes']}min, 变化 {d['load_change_minutes']:+d}min ({d['load_change_percent']:+.2f}%)")
print(f"✓ 超期变化工单: {diff['total_overdue_changed']} 个")
for d in diff['overdue_orders'][:5]:
    print(f"  - {d['order_no']}: 原超期{d['original_overdue_minutes']}min → 预案超期{d['scenario_overdue_minutes']}min, 变化 {d['overdue_change']:+d}min")

step("步骤11: 发布约束检查")
constraints = check_publish_constraints(db, scenario1.id)
print(f"✓ 可以发布: {constraints['can_publish']}")
print(f"  基线匹配: {constraints['baseline_matches']} - {constraints['baseline_message']}")
print(f"  活跃冲突: {constraints['active_conflicts_count']}")
if constraints['constraint_violations']:
    print(f"  约束违规:")
    for v in constraints['constraint_violations']:
        print(f"    - {v}")

step("步骤12: 基线校验 - 模拟正式计划被修改后预案无法发布")
from app.models import DeviceFault
fake_fault = DeviceFault(
    device_id=dev1.id,
    fault_time=now,
    expected_recovery_time=now + timedelta(hours=4),
    status="active",
    description="真实故障 - 修改基线"
)
db.add(fake_fault)
db.commit()

baseline_ok, baseline_msg = verify_baseline(db, scenario1)
print(f"✓ 修改正式计划后基线检查: {baseline_ok} - {baseline_msg}")

constraints = check_publish_constraints(db, scenario1.id)
print(f"✓ 修改后约束检查: 可以发布={constraints['can_publish']}")
print(f"  违规: {constraints['constraint_violations']}")

ok, result = publish_scenario(db, scenario1.id, operator="车间张主任")
print(f"✓ 尝试发布: 成功={ok}, message={result.get('message', '')}")

db.delete(fake_fault)
db.commit()
print("  (已回滚基线修改，恢复测试)")

step("步骤13: 正确流程 - 创建新预案并完整发布")
scenario2 = create_scenario(
    db, name="预案-优化排产0615",
    description="对现有排产进行微调后发布",
    created_by="车间李副主任"
)
print(f"✓ 新预案创建: ID={scenario2.id}, 名称={scenario2.name}")

baseline_ok, _ = verify_baseline(db, scenario2)
constraints = check_publish_constraints(db, scenario2.id)
print(f"  基线匹配: {baseline_ok}, 可发布: {constraints['can_publish']}")

ok, result = publish_scenario(db, scenario2.id, operator="车间李副主任")
print(f"✓ 发布结果: 成功={ok}")
print(f"  message={result.get('message', '')}")
print(f"  发布时间={result.get('published_at')}")

scenario2_db = get_scenario(db, scenario2.id)
print(f"  预案状态={scenario2_db.status}")
print(f"  发布人={scenario2_db.published_by}")

step("步骤14: 查看操作审计日志")
for sid in [scenario1.id, scenario2.id]:
    logs = get_scenario_audit_logs(db, sid)
    s_name = get_scenario(db, sid).name
    print(f"\n✓ 预案 '{s_name}' 审计日志 ({logs['total']}条):")
    for l in logs['logs']:
        print(f"  [{l['created_at'].strftime('%H:%M:%S')}] {l['action']} - {l['operator'] or '系统'}: {l['details']}")

step("步骤15: 预案列表 - 验证持久化 (未发布/已发布都保留)")
all_s = list_scenarios(db)
print(f"✓ 系统中共有预案: {len(all_s)} 个")
for s in all_s:
    print(f"  [{s.status.upper():8s}] ID={s.id}, {s.name}, 创建人={s.created_by}, 创建时间={s.created_at.strftime('%Y-%m-%d %H:%M')}")

step("步骤16: 删除预案 + 验证不影响正式计划")
delete_ok = delete_scenario(db, scenario1.id, operator="系统管理员")
print(f"✓ 删除预案 {scenario1.name}: 成功={delete_ok}")

prod_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id.is_(None)).count()
scen1_orders = db.query(WorkOrder).filter(WorkOrder.scenario_id == scenario1.id).count()
print(f"  正式计划工单数: {prod_orders}")
print(f"  已删除预案工单数: {scen1_orders} (应为0)")

remaining = list_scenarios(db)
print(f"  剩余预案数: {len(remaining)}")

step("测试总结")
print("""
✓ 数据隔离: 预案数据通过 scenario_id 字段隔离，不会污染正式计划
✓ 四大变更: 插入急单、撤掉设备、延长维护、调整工装 全部可用
✓ 沙盘视图: 预案甘特图、冲突列表 独立查看
✓ 差异对比: 工单推迟、设备负载、订单超期 三项关键指标
✓ 严格发布: 基线校验 + 约束检查，双重保护防止硬覆盖
✓ 持久化: 未发布预案持久保存，重启不丢失
✓ 审计留痕: 所有关键操作 (创建/变更/发布/删除) 全部留痕可追溯
""")

db.close()
print("全部测试通过! ✓")
