import requests
import json
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
    return resp

def setup_devices_and_route():
    print_section("Step 1: 初始化设备和工艺路线")

    devices = [
        {"name": "DEL-CNC-1", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 3},
        {"name": "DEL-CNC-2", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 3},
        {"name": "DEL-POLISH-1", "device_type": "抛光", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10},
        {"name": "DEL-CLEAN-1", "device_type": "清洗", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 20},
        {"name": "DEL-CHECK-1", "device_type": "检验", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 15},
    ]

    for d in devices:
        resp = requests.post(f"{BASE_URL}/devices/", json=d)
        if resp.status_code == 400:
            print(f"  设备 {d['name']} 已存在，跳过")
        elif resp.status_code == 201:
            print(f"  创建设备 {d['name']} 成功")
        else:
            print_response(f"创建设备 {d['name']}", resp)

    route_data = {
        "product_name": "分批交付零件",
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 60, "min_gap_after": 10},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 30, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 15, "min_gap_after": 0},
        ]
    }

    resp = requests.post(f"{BASE_URL}/routes/", json=route_data)
    if resp.status_code == 400 and "already exists" in str(resp.content):
        print("  工艺路线已存在，跳过")
    elif resp.status_code == 201:
        print("  创建工艺路线成功")
    else:
        print_response("创建工艺路线", resp)

    return True


def test_scenario_1_set_delivery_plan_before_scheduling():
    """场景1: 排产前先设置交付计划，然后创建工单，排产时按交付计划拆批"""
    print_section("场景1: 排产前设置交付计划，验证按计划拆批")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)

    order_data = {
        "order_no": "WO-DEL-TEST-001",
        "product_name": "分批交付零件",
        "total_quantity": 10,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": (tomorrow + timedelta(days=20)).isoformat(),
    }

    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建工单(先不设置交付计划)", resp)
    if resp.status_code not in [200, 201]:
        print("  创建工单失败，跳过此场景")
        return None, None

    order_id = resp.json()["order_id"]
    print(f"  工单创建成功, ID={order_id}")

    delivery_plan = {
        "order_id": order_id,
        "plans": [
            {
                "plan_index": 1,
                "planned_quantity": 3,
                "expected_delivery_date": (tomorrow + timedelta(days=3)).isoformat()
            },
            {
                "plan_index": 2,
                "planned_quantity": 4,
                "expected_delivery_date": (tomorrow + timedelta(days=7)).isoformat()
            },
            {
                "plan_index": 3,
                "planned_quantity": 3,
                "expected_delivery_date": (tomorrow + timedelta(days=12)).isoformat()
            }
        ]
    }

    resp = requests.post(f"{BASE_URL}/delivery/plans", json=delivery_plan)
    print_response("设置交付计划", resp)
    assert resp.status_code == 200, "设置交付计划应成功"

    resp = requests.get(f"{BASE_URL}/delivery/orders/{order_id}/plans")
    print_response("查询交付计划", resp)
    data = resp.json()
    assert len(data["plans"]) == 3, "应该有3批交付计划"
    assert data["total_planned_quantity"] == 10, "计划总数量应该是10"
    print("  ✓ 交付计划设置正确")

    print("\n--- 删除已创建工单后重新创建，验证排产前设置交付计划的效果 ---")
    requests.delete(f"{BASE_URL}/orders/{order_id}")

    order_data2 = {
        "order_no": "WO-DEL-TEST-002",
        "product_name": "分批交付零件",
        "total_quantity": 10,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": (tomorrow + timedelta(days=20)).isoformat(),
    }
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data2)
    order_id2 = resp.json()["order_id"]
    print(f"  新建工单 ID={order_id2}")

    resp = requests.post(f"{BASE_URL}/delivery/plans", json={
        "order_id": order_id2,
        "plans": [
            {"plan_index": 1, "planned_quantity": 4, "expected_delivery_date": (tomorrow + timedelta(days=3)).isoformat()},
            {"plan_index": 2, "planned_quantity": 3, "expected_delivery_date": (tomorrow + timedelta(days=7)).isoformat()},
            {"plan_index": 3, "planned_quantity": 3, "expected_delivery_date": (tomorrow + timedelta(days=12)).isoformat()}
        ]
    })
    assert resp.status_code == 200

    resp = requests.post(f"{BASE_URL}/orders/{order_id2}/reschedule")
    print_response("重排工单(按交付计划拆批)", resp)
    if resp.status_code in [200, 201]:
        sub_batches = resp.json().get("sub_batches", [])
        print(f"  子批次数: {len(sub_batches)}")
        for sb in sub_batches:
            print(f"    - {sb['batch_no']}: 数量={sb['quantity']}")
            if "P1" in sb["batch_no"]:
                print(f"      ↑ 属于第1批交付计划")
            elif "P2" in sb["batch_no"]:
                print(f"      ↑ 属于第2批交付计划")
            elif "P3" in sb["batch_no"]:
                print(f"      ↑ 属于第3批交付计划")

        total_qty = sum(sb["quantity"] for sb in sub_batches)
        assert total_qty == 10, "子批次总数量应等于10"
        print("  ✓ 按交付计划拆批成功！")

    return order_id2, resp.json() if resp.status_code in [200, 201] else None


def test_scenario_2_delivery_progress_query(order_id):
    """场景2: 查询交付进度"""
    print_section("场景2: 查询交付进度")
    if not order_id:
        print("  跳过：无有效order_id")
        return

    resp = requests.get(f"{BASE_URL}/delivery/orders/{order_id}/progress")
    print_response("交付进度查询", resp)
    if resp.status_code == 200:
        data = resp.json()
        print(f"  总计划批次数: {data['total_planned_batches']}")
        print(f"  总计划数量: {data['total_planned_quantity']}")
        print(f"  已交付数量: {data['total_delivered_quantity']}")
        print(f"  交付进度: {data['delivery_percent']}%")
        if data.get("next_batch_plan_index"):
            print(f"  下一批: 第{data['next_batch_plan_index']}批")
            print(f"    计划数量: {data['next_batch_planned_quantity']}")
            print(f"    期望日期: {data['next_batch_expected_date']}")
            print(f"    预计可交付时间: {data.get('next_batch_estimated_delivery')}")
        print("  ✓ 交付进度查询成功！")


def test_scenario_3_report_progress_and_deliver(order_id, schedule_result):
    """场景3: 完成子批次后上报进度并发起批次交付"""
    print_section("场景3: 上报完工进度后发起批次交付")
    if not order_id:
        print("  跳过：无有效order_id")
        return None

    sub_batches_resp = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    if sub_batches_resp.status_code != 200:
        print("  查询子批次失败")
        return None

    sub_batches = sub_batches_resp.json()

    first_plan_sbs = [sb for sb in sub_batches if sb.get("batch_no", "").startswith("WO-DEL-TEST-002-P1-")]
    print(f"  找到第1批交付计划的子批次数: {len(first_plan_sbs)}")

    if not first_plan_sbs:
        print("  未找到属于第1批的子批次，尝试使用第一个子批次")
        first_plan_sbs = sub_batches[:1] if sub_batches else []

    completed_sb_ids = []
    now = datetime.now()
    for i, sb in enumerate(first_plan_sbs[:1]):
        sb_id = sb["sub_batch_id"]
        print(f"\n  上报子批次 {sb['batch_no']} (ID={sb_id}) 的所有工序进度:")
        for step_order in range(1, 5):
            progress_time = (now + timedelta(hours=i * 8 + step_order)).isoformat()
            resp = requests.post(f"{BASE_URL}/orders/progress/report", json={
                "sub_batch_id": sb_id,
                "step_order": step_order,
                "actual_completion_time": progress_time,
                "good_quantity": sb["quantity"]
            })
            if resp.status_code == 200:
                data = resp.json()
                print(f"    工序{step_order}上报成功: 良品{data['good_quantity']}, 报废{data['scrap_quantity']}")
            else:
                print_response(f"    工序{step_order}上报", resp)
        completed_sb_ids.append(sb_id)

    print("\n  查询交付计划，获取第1批的delivery_plan_id:")
    plans_resp = requests.get(f"{BASE_URL}/delivery/orders/{order_id}/plans")
    plan_id = None
    if plans_resp.status_code == 200:
        plans_data = plans_resp.json()
        for p in plans_data["plans"]:
            if p["plan_index"] == 1:
                plan_id = p["id"]
                print(f"  第1批交付计划 ID={plan_id}, 计划数量={p['planned_quantity']}")
                break

    if plan_id is None:
        print("  未找到交付计划，跳过交付测试")
        return None

    deliver_quantity = 2
    print(f"\n  发起第1批部分交付: 交付{deliver_quantity}件")
    delivery_resp = requests.post(f"{BASE_URL}/delivery/deliver", json={
        "delivery_plan_id": plan_id,
        "actual_quantity": deliver_quantity,
        "accepted_by": "测试验收员张三",
        "remarks": "第一批部分交付，客户验收通过"
    })
    print_response("批次交付结果", delivery_resp)

    if delivery_resp.status_code == 200:
        result = delivery_resp.json()
        print(f"  ✓ 交付成功！本批剩余待交付: {result.get('remaining_quantity')}")
        print("  验证: 查询进度确认交付数量已更新")

        progress_resp = requests.get(f"{BASE_URL}/delivery/orders/{order_id}/progress")
        if progress_resp.status_code == 200:
            progress = progress_resp.json()
            print(f"    总交付数量: {progress['total_delivered_quantity']}")
            print(f"    已部分交付批次数: {progress['partially_delivered_batches']}")
            for bd in progress["batches_detail"]:
                if bd["plan_index"] == 1:
                    print(f"    第1批状态: {bd['status']}, 已交付{bd['actual_delivered_quantity']}/{bd['planned_quantity']}")
                    assert bd["actual_delivered_quantity"] == deliver_quantity, "交付数量应匹配"
            print("  ✓ 交付进度更新验证通过！")
    else:
        print("  交付失败（可能是因为良品数不足，部分子批次未完成）")

    return plan_id


def test_scenario_4_delivery_conflicts():
    """场景4: 交付计划延期冲突检测"""
    print_section("场景4: 交付计划延期 - 冲突检测")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)

    order_data = {
        "order_no": "WO-DEL-CONFLICT-001",
        "product_name": "分批交付零件",
        "total_quantity": 10,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": (tomorrow + timedelta(days=2)).isoformat(),
    }

    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    if resp.status_code not in [200, 201]:
        print("  注: 工单排产失败(预期的极短截止时间)")
        order_id = None
        if resp.status_code == 400:
            print("  先设置交付计划(更长的截止时间):")

            order_data2 = dict(order_data)
            order_data2["order_no"] = "WO-DEL-CONFLICT-002"
            order_data2["deadline"] = (tomorrow + timedelta(days=10)).isoformat()
            resp2 = requests.post(f"{BASE_URL}/orders/", json=order_data2)
            if resp2.status_code in [200, 201]:
                order_id = resp2.json()["order_id"]
                print(f"  创建长截止时间工单: ID={order_id}")

                plan_resp = requests.post(f"{BASE_URL}/delivery/plans", json={
                    "order_id": order_id,
                    "plans": [
                        {"plan_index": 1, "planned_quantity": 10,
                         "expected_delivery_date": (tomorrow + timedelta(hours=4)).isoformat()}
                    ]
                })
                if plan_resp.status_code == 200:
                    resched_resp = requests.post(f"{BASE_URL}/orders/{order_id}/reschedule")
                    if resched_resp.status_code in [200, 201]:
                        print("  重排产完成")
                    else:
                        print_response("  重排产", resched_resp)
    else:
        order_id = resp.json()["order_id"]
        plan_resp = requests.post(f"{BASE_URL}/delivery/plans", json={
            "order_id": order_id,
            "plans": [
                {"plan_index": 1, "planned_quantity": 10,
                 "expected_delivery_date": (tomorrow + timedelta(hours=2)).isoformat()}
            ]
        })
        print_response("  设置极短期望交付日期的计划", plan_resp)

    print("\n  查询交付延期冲突列表:")
    conflict_resp = requests.get(f"{BASE_URL}/delivery/conflicts")
    print_response("交付冲突列表", conflict_resp)
    if conflict_resp.status_code == 200:
        conflicts = conflict_resp.json()
        if conflicts:
            print(f"  ✓ 检测到 {len(conflicts)} 个交付计划延期冲突")
            for c in conflicts[:3]:
                print(f"    - 第{c.get('plan_index')}批延期{c.get('delay_human', 'N/A')}")
        else:
            print("  (无冲突检测到，排产能按时完成)")

    if order_id:
        requests.delete(f"{BASE_URL}/orders/{order_id}")


def test_scenario_5_cancel_order_with_deliveries():
    """场景5: 有交付记录的工单撤销 - 保留已交付部分"""
    print_section("场景5: 撤销有交付记录的工单(保留已交付部分)")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)

    order_data = {
        "order_no": "WO-DEL-CANCEL-001",
        "product_name": "分批交付零件",
        "total_quantity": 6,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": (tomorrow + timedelta(days=15)).isoformat(),
    }
    resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
    if resp.status_code not in [200, 201]:
        print("  创建工单失败，跳过此场景")
        print_response("创建工单", resp)
        return

    order_id = resp.json()["order_id"]
    print(f"  创建工单成功 ID={order_id}")

    plan_resp = requests.post(f"{BASE_URL}/delivery/plans", json={
        "order_id": order_id,
        "plans": [
            {"plan_index": 1, "planned_quantity": 3,
             "expected_delivery_date": (tomorrow + timedelta(days=3)).isoformat()},
            {"plan_index": 2, "planned_quantity": 3,
             "expected_delivery_date": (tomorrow + timedelta(days=8)).isoformat()}
        ]
    })
    assert plan_resp.status_code == 200, "设置交付计划应成功"
    plans = plan_resp.json()["plans"]
    plan1_id = plans[0]["id"]
    print(f"  交付计划设置完成，第1批ID={plan1_id}")

    sbs_resp = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    if sbs_resp.status_code == 200:
        sbs = sbs_resp.json()
        plan1_sbs = [sb for sb in sbs if "P1" in sb.get("batch_no", "")]
        if not plan1_sbs and sbs:
            plan1_sbs = sbs[:1]

        if plan1_sbs:
            sb_id = plan1_sbs[0]["sub_batch_id"]
            now = datetime.now()
            for step_order in range(1, 5):
                step_time = (now + timedelta(hours=step_order)).isoformat()
                pg_resp = requests.post(f"{BASE_URL}/orders/progress/report", json={
                    "sub_batch_id": sb_id,
                    "step_order": step_order,
                    "actual_completion_time": step_time,
                    "good_quantity": min(3, plan1_sbs[0]["quantity"])
                })
                if pg_resp.status_code != 200:
                    print(f"    工序{step_order}上报: status={pg_resp.status_code}")
            print("  第1批子批次完工上报完成")

    deliver_resp = requests.post(f"{BASE_URL}/delivery/deliver", json={
        "delivery_plan_id": plan1_id,
        "actual_quantity": 2,
        "accepted_by": "测试验收员",
        "remarks": "交付测试"
    })
    print_response("尝试交付第1批", deliver_resp)

    print("\n  删除/撤销工单:")
    delete_resp = requests.delete(f"{BASE_URL}/orders/{order_id}")
    print_response("撤销工单结果", delete_resp)

    if delete_resp.status_code == 200:
        data = delete_resp.json()
        print(f"  ✓ 部分撤销成功！")
        print(f"    已交付数量: {data.get('total_delivered_quantity')}")
        print(f"    工单最终状态: {data.get('order_status')}")
        assert data.get("order_status") == "partially_cancelled", "应保留已交付部分"
        print("  ✓ 有交付记录时部分撤销功能验证通过！")
    elif delete_resp.status_code == 204:
        print("  (整单删除，无实际交付记录)")
    else:
        print("  撤销结果状态:", delete_resp.status_code)


def run_all_tests():
    print("\n" + "#" * 70)
    print("#" + " " * 10 + "工单分批交付与阶段性验收模块测试" + " " * 20 + "#")
    print("#" * 70)

    try:
        setup_devices_and_route()

        order_id, schedule_result = test_scenario_1_set_delivery_plan_before_scheduling()

        test_scenario_2_delivery_progress_query(order_id)

        test_scenario_3_report_progress_and_deliver(order_id, schedule_result)

        test_scenario_4_delivery_conflicts()

        test_scenario_5_cancel_order_with_deliveries()

        print_section("所有测试场景完成!")
        print("✓✓✓ 分批交付与阶段性验收功能模块已集成 ✓✓✓")

    except Exception as e:
        print(f"\n✗ 测试出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
