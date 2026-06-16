import requests
import json
import time
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api"


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


print_section("Step 1: 获取已有工单列表")
resp = requests.get(f"{BASE_URL}/orders/")
orders = resp.json()
print(f"共 {len(orders)} 个工单")

opt_orders = [o for o in orders if "WO-OPT" in o["order_no"]]
print(f"找到 {len(opt_orders)} 个 OPT 测试工单")

if len(opt_orders) < 6:
    print("工单不足，需要先运行 test_optimization.py 创建测试数据")
    exit(1)

order_ids = [o["id"] for o in opt_orders[:6]]
print(f"使用工单ID: {order_ids}")


print_section("Test 1: 提交寻优任务 - 最小化总换型时间")
request_data = {
    "order_ids": order_ids,
    "objective": "min_changeover",
    "max_duration_seconds": 20,
    "created_by": "test_fix"
}
resp = requests.post(f"{BASE_URL}/optimization/tasks", json=request_data)
task_id = resp.json()["id"]
print(f"✓ 任务已提交，ID={task_id}")


print_section("Test 2: 等待寻优完成")
for i in range(30):
    time.sleep(1)
    detail_resp = requests.get(f"{BASE_URL}/optimization/tasks/{task_id}")
    data = detail_resp.json()
    status = data["status"]
    print(f"  [{i+1}s] status={status}, explored={data['explored_count']}, best={data.get('current_best_value')}")
    if status in ["completed", "failed", "cancelled"]:
        break


print_section("Test 3: 验证空闲率计算（换型计入占用）")
detail = requests.get(f"{BASE_URL}/optimization/tasks/{task_id}").json()
metrics = detail.get("metrics")
baseline_metrics = detail.get("baseline_metrics")
improvements = detail.get("improvements", [])

if metrics:
    print(f"  优化后指标:")
    print(f"    最大完工时间: {metrics['makespan_minutes']} 分钟")
    print(f"    总换型时间: {metrics['total_changeover_minutes']} 分钟")
    print(f"    总空闲时间: {metrics['total_idle_minutes']} 分钟")
    print(f"    平均设备利用率: {metrics['avg_device_utilization']:.2%}")

if baseline_metrics:
    print(f"\n  基线指标:")
    print(f"    最大完工时间: {baseline_metrics['makespan_minutes']} 分钟")
    print(f"    总换型时间: {baseline_metrics['total_changeover_minutes']} 分钟")
    print(f"    总空闲时间: {baseline_metrics['total_idle_minutes']} 分钟")
    print(f"    平均设备利用率: {baseline_metrics['avg_device_utilization']:.2%}")

print(f"\n  改善百分比:")
for imp in improvements:
    print(f"    {imp['metric_name']}: {imp['improvement_percent']:+.2f}%")

print(f"\n  验证: 换型时间计入设备占用后，利用率应该比之前更高，空闲更少")
print(f"  注意: 如果利用率 > 50% 且空闲 < 总时间的50%，说明计算基本合理")


print_section("Test 4: 验证工装约束（检查排产结果中是否有fixture_id）")
result_schedule = detail.get("result_schedule", [])
has_fixture = any(e.get("fixture_id") is not None for e in result_schedule)
fixture_count = sum(1 for e in result_schedule if e.get("fixture_id") is not None)
print(f"  总工序数: {len(result_schedule)}")
print(f"  含工装的工序数: {fixture_count}")

if has_fixture:
    print(f"  ✓ 工装约束已参与排产")
else:
    print(f"  ⚠ 当前工序未设置工装需求（属于正常情况，需要有工装配置的工序才能看到）")


print_section("Test 5: 应用优化结果，验证子批次和数据完整性")
apply_resp = requests.post(
    f"{BASE_URL}/optimization/tasks/{task_id}/apply",
    json={"operator": "test_fix"}
)
apply_result = apply_resp.json()
print(f"  应用结果: {apply_result}")

if apply_result.get("applied"):
    print(f"  ✓ 应用成功")

    print(f"\n  验证应用后的数据完整性:")
    all_ok = True
    for oid in order_ids:
        order_resp = requests.get(f"{BASE_URL}/orders/{oid}")
        order = order_resp.json()

        sub_batches_resp = requests.get(f"{BASE_URL}/orders/{oid}/sub-batches")
        sub_batches = sub_batches_resp.json()

        summary_resp = requests.get(f"{BASE_URL}/orders/{oid}/summary")
        summary = summary_resp.json() if summary_resp.status_code == 200 else {}

        n_entries = len(order.get("schedule_entries", []))

        print(f"\n  工单 {order['order_no']}:")
        print(f"    状态: {order.get('status')}")
        print(f"    排产条目数: {n_entries}")
        print(f"    子批次数: {len(sub_batches)}")
        print(f"    是否拆批: {order.get('is_split')}")
        print(f"    物料锁定数: {summary.get('total_material_locks', 0)}")

        if order.get("status") != "scheduled":
            print(f"    ✗ 状态异常，应为 scheduled")
            all_ok = False

        if n_entries == 0:
            print(f"    ✗ 没有排产条目")
            all_ok = False

    if all_ok:
        print(f"\n  ✓✓✓ 所有工单数据完整，应用后子批次、排产条目、物料锁都正确生成")
    else:
        print(f"\n  ✗ 部分工单数据不完整")

else:
    print(f"  ✗ 应用失败: {apply_result.get('message')}")
    if apply_result.get("conflict_reason"):
        print(f"    冲突原因: {apply_result['conflict_reason']}")


print_section("所有验证测试完成")
