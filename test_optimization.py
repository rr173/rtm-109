import requests
import json
import time
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api"


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def print_response(label, resp):
    print(f"\n--- {label} ---")
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except:
        print(resp.text)


def setup_devices():
    print_section("Step 1: 创建设备")

    devices = [
        {"name": "CNC-O1", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "CNC-O2", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "抛光-O1", "device_type": "抛光", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10},
        {"name": "清洗-O1", "device_type": "清洗", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 20},
        {"name": "检验-O1", "device_type": "检验", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 15},
    ]

    created = []
    for d in devices:
        resp = requests.post(f"{BASE_URL}/devices/", json=d)
        if resp.status_code == 201:
            created.append(resp.json())
            print(f"  ✓ 创建设备: {d['name']}")
        elif resp.status_code == 400 and "already exists" in resp.text:
            print(f"  - 设备已存在: {d['name']}")
        else:
            print(f"  ✗ 创建设备失败: {d['name']} - {resp.status_code}")
    return created


def setup_product_families():
    print_section("Step 2: 创建产品族和换型规则")

    families = [
        {"name": "精密零件族A", "description": "高精度CNC零件"},
        {"name": "精密零件族B", "description": "标准CNC零件"},
    ]

    for fam in families:
        resp = requests.post(f"{BASE_URL}/changeover/product-families", json=fam)
        if resp.status_code == 201:
            print(f"  ✓ 创建产品族: {fam['name']}")
        elif resp.status_code == 400:
            print(f"  - 产品族已存在: {fam['name']}")

    time.sleep(0.5)
    fam_list_resp = requests.get(f"{BASE_URL}/changeover/product-families")
    fam_map = {}
    if fam_list_resp.status_code == 200:
        families = fam_list_resp.json()
        if isinstance(families, list):
            for f in families:
                fam_map[f["name"]] = f["id"]
        else:
            for f in families.get("product_families", []):
                fam_map[f["name"]] = f["id"]

    changeover_rules = [
        {
            "device_type": "CNC",
            "from_product_family_id": fam_map.get("精密零件族A"),
            "to_product_family_id": fam_map.get("精密零件族A"),
            "changeover_minutes": 15,
            "changeover_type": "same_family",
            "description": "同族内换型"
        },
        {
            "device_type": "CNC",
            "from_product_family_id": fam_map.get("精密零件族A"),
            "to_product_family_id": fam_map.get("精密零件族B"),
            "changeover_minutes": 60,
            "changeover_type": "cross_family",
            "description": "跨族换型"
        },
        {
            "device_type": "CNC",
            "from_product_family_id": fam_map.get("精密零件族B"),
            "to_product_family_id": fam_map.get("精密零件族A"),
            "changeover_minutes": 60,
            "changeover_type": "cross_family",
            "description": "跨族换型"
        },
    ]

    for rule in changeover_rules:
        if rule["from_product_family_id"] and rule["to_product_family_id"]:
            resp = requests.post(f"{BASE_URL}/changeover/rules", json=rule)
            if resp.status_code == 201:
                print(f"  ✓ 创建换型规则")
            else:
                print(f"  - 换型规则可能已存在: {resp.status_code}")

    return fam_map


def setup_routes(fam_map):
    print_section("Step 3: 创建工艺路线")

    routes_data = [
        {
            "product_name": "精密零件-Opt-A1",
            "product_family_id": fam_map.get("精密零件族A"),
            "steps": [
                {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 60, "min_gap_after": 10},
                {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 30, "min_gap_after": 5},
                {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 20, "min_gap_after": 5},
                {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 15, "min_gap_after": 0},
            ]
        },
        {
            "product_name": "精密零件-Opt-B1",
            "product_family_id": fam_map.get("精密零件族B"),
            "steps": [
                {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 45, "min_gap_after": 10},
                {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 25, "min_gap_after": 5},
                {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 15, "min_gap_after": 5},
                {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 10, "min_gap_after": 0},
            ]
        },
    ]

    created = []
    for route in routes_data:
        resp = requests.post(f"{BASE_URL}/routes/", json=route)
        if resp.status_code == 201:
            print(f"  ✓ 创建工艺路线: {route['product_name']}")
            created.append(route['product_name'])
        elif resp.status_code == 400:
            print(f"  - 工艺路线已存在: {route['product_name']}")
            created.append(route['product_name'])
    return created


def create_orders(product_names):
    print_section("Step 4: 创建测试工单（共6个工单，用于寻优）")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=7)

    orders = [
        {"order_no": "WO-OPT-A001", "product_name": product_names[0], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 8},
        {"order_no": "WO-OPT-A002", "product_name": product_names[0], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 5},
        {"order_no": "WO-OPT-A003", "product_name": product_names[0], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 3},
        {"order_no": "WO-OPT-B001", "product_name": product_names[1], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 9},
        {"order_no": "WO-OPT-B002", "product_name": product_names[1], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 6},
        {"order_no": "WO-OPT-B003", "product_name": product_names[1], "total_quantity": 1, "expected_start_time": tomorrow.isoformat(), "deadline": deadline.isoformat(), "priority": 2},
    ]

    order_ids = []
    for order in orders:
        resp = requests.post(f"{BASE_URL}/orders/", json=order)
        if resp.status_code == 201:
            data = resp.json()
            order_id = data["order_id"]
            order_ids.append(order_id)
            print(f"  ✓ 创建工单: {order['order_no']} (ID={order_id}, priority={order['priority']})")
        else:
            print(f"  ✗ 创建工单失败: {order['order_no']}")
            print_response("详细错误", resp)

    return order_ids


def test_submit_task(order_ids):
    print_section("Test 1: 提交寻优任务 - 最小化最大完工时间")

    request_data = {
        "order_ids": order_ids,
        "objective": "min_makespan",
        "max_duration_seconds": 30,
        "created_by": "test_user"
    }

    resp = requests.post(f"{BASE_URL}/optimization/tasks", json=request_data)
    print_response("提交寻优任务", resp)

    if resp.status_code != 200:
        print("  ✗ 提交失败")
        return None

    data = resp.json()
    task_id = data["id"]
    print(f"  ✓ 任务提交成功，任务ID: {task_id}")
    print(f"    状态: {data['status']}")
    print(f"    优化目标: {data['objective']}")
    print(f"    工单数量: {len(data['order_ids'])}")

    return task_id


def test_query_progress(task_id):
    print_section("Test 2: 查询寻优任务进度")

    print("  等待5秒后查询进度...")
    time.sleep(5)

    resp = requests.get(f"{BASE_URL}/optimization/tasks/{task_id}")
    print_response("查询任务详情", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✓ 进度查询成功")
        print(f"    状态: {data['status']}")
        print(f"    已探索方案数: {data['explored_count']}")
        print(f"    当前最优值: {data.get('current_best_value')}")
        print(f"    基线值: {data.get('baseline_value')}")
        print(f"    剩余时间: {data.get('remaining_seconds')} 秒")

        if data.get('trajectories'):
            print(f"    轨迹点数: {len(data['trajectories'])}")

    return resp.json() if resp.status_code == 200 else None


def wait_for_completion(task_id, max_wait=60):
    print_section("Test 3: 等待任务完成")

    start = time.time()
    while time.time() - start < max_wait:
        resp = requests.get(f"{BASE_URL}/optimization/tasks/{task_id}")
        if resp.status_code != 200:
            time.sleep(2)
            continue

        data = resp.json()
        status = data["status"]
        print(f"  当前状态: {status}, 已探索: {data['explored_count']}, 最优值: {data.get('current_best_value')}")

        if status in ["completed", "failed", "cancelled"]:
            print(f"  ✓ 任务已结束，状态: {status}")
            return data

        time.sleep(3)

    print("  ✗ 等待超时")
    return None


def test_compare_results(task_detail):
    print_section("Test 4: 对比优化结果与基线")

    if not task_detail:
        print("  ✗ 无任务详情数据")
        return

    print(f"  任务状态: {task_detail['status']}")
    print(f"  总探索方案数: {task_detail['explored_count']}")
    print(f"  优化目标: {task_detail['objective']}")

    baseline_metrics = task_detail.get("baseline_metrics")
    metrics = task_detail.get("metrics")
    improvements = task_detail.get("improvements", [])

    if baseline_metrics and metrics:
        print(f"\n  ===== 基线指标 =====")
        print(f"    最大完工时间: {baseline_metrics['makespan_minutes']} 分钟")
        print(f"    总换型时间: {baseline_metrics['total_changeover_minutes']} 分钟")
        print(f"    总空闲时间: {baseline_metrics['total_idle_minutes']} 分钟")
        print(f"    平均设备利用率: {baseline_metrics['avg_device_utilization']:.2%}")

        print(f"\n  ===== 优化后指标 =====")
        print(f"    最大完工时间: {metrics['makespan_minutes']} 分钟")
        print(f"    总换型时间: {metrics['total_changeover_minutes']} 分钟")
        print(f"    总空闲时间: {metrics['total_idle_minutes']} 分钟")
        print(f"    平均设备利用率: {metrics['avg_device_utilization']:.2%}")

        print(f"\n  ===== 改善百分比 =====")
        for imp in improvements:
            arrow = "↓" if imp["improvement_percent"] > 0 else ("↑" if imp["improvement_percent"] < 0 else "→")
            print(f"    {imp['metric_name']}: {imp['improvement_percent']:+.2f}% {arrow}")

    if task_detail.get("trajectories"):
        print(f"\n  ===== 优化轨迹（前10个点）=====")
        for t in task_detail["trajectories"][:10]:
            marker = " ★新最优" if t["is_best"] else ""
            print(f"    迭代{t['iteration']}: 目标值={t['objective_value']}{marker}")

    result_schedule = task_detail.get("result_schedule", [])
    baseline_schedule = task_detail.get("baseline_schedule", [])
    if result_schedule:
        print(f"\n  ===== 优化后排产结果（共{len(result_schedule)}条工序）=====")
        by_order = {}
        for e in result_schedule:
            oid = e["order_no"]
            if oid not in by_order:
                by_order[oid] = []
            by_order[oid].append(e)

        for order_no, entries in sorted(by_order.items()):
            entries.sort(key=lambda x: x["step_order"])
            first_start = entries[0]["start_time"]
            last_end = entries[-1]["end_time"]
            print(f"    {order_no}: {first_start[:19]} ~ {last_end[:19]}")


def test_apply_result(task_id):
    print_section("Test 5: 应用优化结果到正式排产")

    request_data = {"operator": "test_user"}
    resp = requests.post(f"{BASE_URL}/optimization/tasks/{task_id}/apply", json=request_data)
    print_response("应用优化结果", resp)

    if resp.status_code == 200:
        data = resp.json()
        if data.get("applied"):
            print(f"  ✓ 应用成功: {data['message']}")
        else:
            print(f"  ⚠ 应用被拒绝: {data.get('message')}")
            if data.get("conflict_reason"):
                print(f"    冲突原因: {data['conflict_reason']}")


def test_list_tasks():
    print_section("Test 6: 查询寻优任务历史列表")

    resp = requests.get(f"{BASE_URL}/optimization/tasks", params={"limit": 10})
    print_response("任务列表查询", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✓ 查询成功，共 {data['total']} 个任务")
        for t in data["tasks"][:5]:
            print(f"    ID={t['id']}: status={t['status']}, objective={t['objective']}, explored={t['explored_count']}, applied={t['is_applied']}")


def test_cancel_task(order_ids):
    print_section("Test 7: 测试取消寻优任务")

    request_data = {
        "order_ids": order_ids,
        "objective": "min_changeover",
        "max_duration_seconds": 120,
        "created_by": "test_cancel"
    }

    resp = requests.post(f"{BASE_URL}/optimization/tasks", json=request_data)
    if resp.status_code != 200:
        print("  ✗ 提交任务失败")
        return

    task_id = resp.json()["id"]
    print(f"  ✓ 已提交任务 {task_id}，等待2秒后取消...")

    time.sleep(2)

    cancel_resp = requests.post(f"{BASE_URL}/optimization/tasks/{task_id}/cancel", params={"operator": "test_user"})
    print_response("取消任务", cancel_resp)

    if cancel_resp.status_code == 200:
        data = cancel_resp.json()
        print(f"  ✓ 取消请求已发送，状态: {data['status']}")

    time.sleep(2)

    check_resp = requests.get(f"{BASE_URL}/optimization/tasks/{task_id}")
    if check_resp.status_code == 200:
        data = check_resp.json()
        print(f"  最终状态: {data['status']}")
        print(f"  已探索方案数: {data['explored_count']}")


def run_all_tests():
    print("\n" + "#"*70)
    print("#" + " "*10 + "多约束排产自动寻优模块测试" + " "*30 + "#")
    print("#"*70)

    try:
        devices = setup_devices()
        fam_map = setup_product_families()
        products = setup_routes(fam_map)

        if len(products) < 2:
            print("  ✗ 工艺路线创建失败，终止测试")
            return

        order_ids = create_orders(products)

        if len(order_ids) < 2:
            print("  ✗ 工单创建失败，终止测试")
            return

        task_id = test_submit_task(order_ids)
        if not task_id:
            return

        task_detail = test_query_progress(task_id)
        final_detail = wait_for_completion(task_id, max_wait=60)

        test_compare_results(final_detail or task_detail)
        test_apply_result(task_id)
        test_list_tasks()
        test_cancel_task(order_ids)

        print_section("所有测试完成!")
        print("✓✓✓ 多约束排产寻优模块功能验证完成 ✓✓✓")

    except Exception as e:
        print(f"\n✗ 测试出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
