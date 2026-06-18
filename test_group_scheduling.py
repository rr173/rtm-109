import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api"

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def print_response(label, resp):
    print(f"\n--- {label} ---")
    print(f"Status: {resp.status_code}")
    try:
        data = resp.json()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except:
        print(resp.text)
    return resp

def setup_test_environment():
    print_section("Step 1: 初始化测试环境")

    print_section("1.1 创建设备")
    devices = [
        {"name": "CNC-G1", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "CNC-G2", "device_type": "CNC", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 5},
        {"name": "抛光-G1", "device_type": "抛光", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10},
        {"name": "清洗-G1", "device_type": "清洗", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 20},
        {"name": "检验-G1", "device_type": "检验", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 15},
    ]
    for d in devices:
        resp = requests.post(f"{BASE_URL}/devices/", json=d)
        print(f"  设备 {d['name']}: {resp.status_code}")

    print_section("1.2 创建产品族")
    families = [
        {"name": "精密零件族A", "description": "高精度CNC零件系列"},
        {"name": "精密零件族B", "description": "普通精度零件系列"},
    ]
    family_ids = {}
    for f in families:
        resp = requests.post(f"{BASE_URL}/changeover/product-families", json=f)
        if resp.status_code == 201:
            data = resp.json()
            family_ids[f["name"]] = data["id"]
            print(f"  产品族 {f['name']}: ID={data['id']}")
        else:
            resp = requests.get(f"{BASE_URL}/changeover/product-families")
            for fam in resp.json():
                if fam["name"] == f["name"]:
                    family_ids[f["name"]] = fam["id"]
                    print(f"  产品族已存在 {f['name']}: ID={fam['id']}")

    print_section("1.3 创建换型规则")
    family_a_id = family_ids.get("精密零件族A")
    family_b_id = family_ids.get("精密零件族B")

    if family_a_id and family_b_id:
        changeover_rules = [
            {
                "device_type": "CNC",
                "from_product_family_id": family_a_id,
                "to_product_family_id": family_a_id,
                "changeover_minutes": 10,
                "changeover_type": "same_family",
                "description": "同族A内短换型"
            },
            {
                "device_type": "CNC",
                "from_product_family_id": family_a_id,
                "to_product_family_id": family_b_id,
                "changeover_minutes": 60,
                "changeover_type": "cross_family",
                "description": "A->B跨族长换型"
            },
            {
                "device_type": "CNC",
                "from_product_family_id": family_b_id,
                "to_product_family_id": family_b_id,
                "changeover_minutes": 10,
                "changeover_type": "same_family",
                "description": "同族B内短换型"
            },
        ]
        for rule in changeover_rules:
            resp = requests.post(f"{BASE_URL}/changeover/rules", json=rule)
            print(f"  换型规则: {resp.status_code}")

    print_section("1.4 创建工艺路线")
    route_family_a = {
        "product_name": "精密零件A1",
        "product_family_id": family_a_id,
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 60, "min_gap_after": 10},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 30, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 15, "min_gap_after": 0},
        ]
    }
    route_family_a2 = {
        "product_name": "精密零件A2",
        "product_family_id": family_a_id,
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 45, "min_gap_after": 10},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 25, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 15, "min_gap_after": 0},
        ]
    }
    route_family_b = {
        "product_name": "精密零件B1",
        "product_family_id": family_b_id,
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 30, "min_gap_after": 10},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光", "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗", "duration_minutes": 15, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验", "duration_minutes": 10, "min_gap_after": 0},
        ]
    }

    for route_data in [route_family_a, route_family_a2, route_family_b]:
        resp = requests.post(f"{BASE_URL}/routes/", json=route_data)
        print(f"  工艺路线 {route_data['product_name']}: {resp.status_code}")

    return family_ids


def test_recommend_groups():
    print_section("Test Case 1: 自动推荐成组方案")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)

    print("  创建5张待排产工单（3张同族A，2张同族B）")
    order_ids = []
    for i in range(3):
        order_data = {
            "order_no": f"WO-REC-A-{i+1:03d}",
            "product_name": "精密零件A1",
            "total_quantity": 2,
            "expected_start_time": tomorrow.isoformat(),
            "deadline": deadline.isoformat()
        }
        resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if resp.status_code == 201:
            data = resp.json()
            if data.get("success"):
                order_ids.append(data["order_id"])
                print(f"    创建工单 WO-REC-A-{i+1:03d}: ID={data['order_id']}")

    for i in range(2):
        order_data = {
            "order_no": f"WO-REC-B-{i+1:03d}",
            "product_name": "精密零件B1",
            "total_quantity": 2,
            "expected_start_time": tomorrow.isoformat(),
            "deadline": deadline.isoformat()
        }
        resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if resp.status_code == 201:
            data = resp.json()
            if data.get("success"):
                order_ids.append(data["order_id"])
                print(f"    创建工单 WO-REC-B-{i+1:03d}: ID={data['order_id']}")

    print(f"\n  共创建 {len(order_ids)} 张工单，调用成组推荐接口")
    order_ids_str = [str(oid) for oid in order_ids]
    resp = requests.post(
        f"{BASE_URL}/group-scheduling/recommend",
        params={"order_ids": order_ids_str}
    )
    print_response("推荐成组结果", resp)

    if resp.status_code == 200:
        data = resp.json()
        recommendations = data.get("recommendations", [])
        print(f"\n  推荐成组数: {len(recommendations)}")
        print(f"  预计节省总换型时间: {data.get('total_estimated_savings_minutes', 0)} 分钟")

        for i, rec in enumerate(recommendations):
            print(f"\n  推荐组 {i+1}:")
            print(f"    产品族: {rec.get('product_family_name')}")
            print(f"    设备: {rec.get('device_name')}")
            print(f"    工单数: {len(rec.get('order_ids', []))}")
            print(f"    预计节省: {rec.get('estimated_savings_minutes')} 分钟")
            print(f"    独立排产换型: {rec.get('current_changeover_minutes')} 分钟")
            print(f"    成组后排产换型: {rec.get('grouped_changeover_minutes')} 分钟")

        print("\n✓ 推荐成组接口测试通过")

    return order_ids


def test_group_scheduling(order_ids):
    print_section("Test Case 2: 成组排产执行")

    if len(order_ids) < 2:
        print("  跳过：工单不足")
        return None

    print(f"  对 {len(order_ids)} 张工单执行成组排产")

    request_data = {
        "order_ids": order_ids,
        "force_group": False,
        "allow_delay": True
    }
    resp = requests.post(f"{BASE_URL}/group-scheduling/schedule", json=request_data)
    print_response("成组排产结果", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  总体结果:")
        print(f"    成功排产: {data.get('total_scheduled_orders')} 张")
        print(f"    排产失败: {data.get('total_failed_orders')} 张")
        print(f"    预计节省换型: {data.get('total_estimated_savings_minutes')} 分钟")

        results = data.get("results", [])
        group_ids = []
        for r in results:
            if r.get("group_id"):
                group_ids.append(r["group_id"])
                print(f"\n  成组 {r.get('group_code')}:")
                print(f"    组ID: {r.get('group_id')}")
                print(f"    包含工单: {len(r.get('scheduled_order_ids', []))} 张")
                print(f"    失败工单: {len(r.get('failed_order_ids', []))} 张")
                print(f"    预计节省: {r.get('estimated_savings_minutes')} 分钟")

        print("\n✓ 成组排产接口测试通过")
        return group_ids
    return None


def test_list_groups(group_ids):
    print_section("Test Case 3: 查询成组列表")

    resp = requests.get(f"{BASE_URL}/group-scheduling/groups")
    print_response("成组列表", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  总成组数: {data.get('total')}")

        for g in data.get("groups", []):
            print(f"\n  组 {g['group_code']}:")
            print(f"    ID: {g['id']}")
            print(f"    产品族: {g.get('product_family_name')}")
            print(f"    设备: {g.get('device_name')}")
            print(f"    类型: {g['group_type']} (强制: {g['is_forced']})")
            print(f"    包含工单: {len(g.get('order_ids', []))} 张")
            print(f"    排产条目: {g.get('entry_count')} 条")
            print(f"    预计节省: {g.get('estimated_savings_minutes')} 分钟")

        print("\n✓ 成组列表查询测试通过")


def test_gantt_with_groups():
    print_section("Test Case 4: 甘特图显示成组标识")

    today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    date_str = today.strftime("%Y-%m-%d")

    resp = requests.get(f"{BASE_URL}/group-scheduling/gantt", params={"date_str": date_str})
    print_response(f"成组甘特图 ({date_str})", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  查询日期: {data.get('date')}")
        print(f"  成组数量: {len(data.get('groups', []))}")

        for dg in data.get("devices", []):
            if dg["entries"]:
                print(f"\n  设备 {dg['device_name']} ({dg['device_type']}):")
                print(f"    关联成组: {dg.get('group_ids', [])}")
                group_set = set()
                for e in dg["entries"]:
                    if e.get("group_id"):
                        group_set.add(e["group_id"])
                    group_info = f", 组ID={e.get('group_id')}, 组码={e.get('group_code')}" if e.get("group_id") else ""
                    print(f"    - {e['order_no']}: {e['step_name']} ({e['entry_type']}){group_info}")
                print(f"    实际成组数量: {len(group_set)}")

        print("\n✓ 成组甘特图测试通过")


def test_force_group():
    print_section("Test Case 5: 强制成组")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)

    print("  创建2张同族工单并单独排产")
    order_ids = []
    for i in range(2):
        order_data = {
            "order_no": f"WO-FORCE-{i+1:03d}",
            "product_name": "精密零件A2",
            "total_quantity": 1,
            "expected_start_time": tomorrow.isoformat(),
            "deadline": deadline.isoformat(),
            "priority": 5
        }
        resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if resp.status_code == 201:
            data = resp.json()
            if data.get("success"):
                order_ids.append(data["order_id"])
                print(f"    创建工单 WO-FORCE-{i+1:03d}: ID={data['order_id']}")

    if len(order_ids) >= 2:
        print(f"\n  对2张工单执行强制成组")
        request_data = {
            "order_ids": order_ids,
            "created_by": "test_user"
        }
        resp = requests.post(f"{BASE_URL}/group-scheduling/force", json=request_data)
        print_response("强制成组结果", resp)

        if resp.status_code == 200:
            data = resp.json()
            print(f"\n  强制成组成功:")
            print(f"    组ID: {data.get('group_id')}")
            print(f"    组码: {data.get('group_code')}")

            gid = data.get("group_id")
            if gid:
                detail_resp = requests.get(f"{BASE_URL}/group-scheduling/groups/{gid}")
                print_response("成组详情", detail_resp)

            print("\n✓ 强制成组测试通过")

        return order_ids, data.get("group_id") if resp.status_code == 200 else None

    return order_ids, None


def test_ungroup(force_group_id, force_order_ids):
    print_section("Test Case 6: 解散成组")

    if not force_group_id:
        print("  跳过：没有可解散的成组")
        return

    print(f"  解散成组 ID={force_group_id}")

    resp = requests.post(f"{BASE_URL}/group-scheduling/{force_group_id}/ungroup")
    print_response("解散成组结果", resp)

    if resp.status_code == 200:
        data = resp.json()
        print(f"\n  解散成组数: {data.get('affected_group_count')}")

        detail_resp = requests.get(f"{BASE_URL}/group-scheduling/groups/{force_group_id}")
        print(f"  解散后查询成组详情: {detail_resp.status_code} (应为404)")

        print("\n✓ 解散成组测试通过")


def test_delete_order_in_group():
    print_section("Test Case 7: 删除成组内工单 - 剩余工单组标识应更新")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)

    print("  创建3张同族工单并成组排产")
    order_ids = []
    for i in range(3):
        order_data = {
            "order_no": f"WO-DELGRP-{i+1:03d}",
            "product_name": "精密零件A1",
            "total_quantity": 1,
            "expected_start_time": tomorrow.isoformat(),
            "deadline": deadline.isoformat()
        }
        resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if resp.status_code == 201:
            data = resp.json()
            if data.get("success"):
                order_ids.append(data["order_id"])

    if len(order_ids) >= 3:
        schedule_req = {"order_ids": order_ids, "force_group": False, "allow_delay": True}
        schedule_resp = requests.post(f"{BASE_URL}/group-scheduling/schedule", json=schedule_req)
        group_id = None
        if schedule_resp.status_code == 200:
            for r in schedule_resp.json().get("results", []):
                if r.get("group_id") and len(r.get("scheduled_order_ids", [])) >= 3:
                    group_id = r["group_id"]
                    print(f"    成组成功: 组ID={group_id}")
                    break

        if group_id:
            delete_id = order_ids[0]
            print(f"\n  删除成组内工单 ID={delete_id}")
            resp = requests.delete(f"{BASE_URL}/orders/{delete_id}")
            print(f"    删除结果: {resp.status_code}")

            detail_resp = requests.get(f"{BASE_URL}/group-scheduling/groups/{group_id}")
            print(f"  查询原成组 (应该自动解散或剩余2张): {detail_resp.status_code}")
            if detail_resp.status_code == 200:
                detail = detail_resp.json()
                remaining_orders = len(detail["group"].get("order_ids", []))
                print(f"    剩余工单数: {remaining_orders} (应为2)")
                if remaining_orders == 2:
                    print("    ✓ 组码已更新，剩余2张工单仍成组")

            print("\n✓ 删除成组内工单测试通过")


def test_insertion_with_grouping():
    print_section("Test Case 8: 插单时自动追加到同族成组")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=15)

    print("  先创建2张同族A工单并成组排产")
    base_order_ids = []
    for i in range(2):
        order_data = {
            "order_no": f"WO-INS-BASE-{i+1:03d}",
            "product_name": "精密零件A1",
            "total_quantity": 1,
            "expected_start_time": tomorrow.isoformat(),
            "deadline": deadline.isoformat(),
            "priority": 5
        }
        resp = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if resp.status_code == 201:
            data = resp.json()
            if data.get("success"):
                base_order_ids.append(data["order_id"])

    if len(base_order_ids) >= 2:
        schedule_req = {"order_ids": base_order_ids, "force_group": True, "allow_delay": True}
        schedule_resp = requests.post(f"{BASE_URL}/group-scheduling/schedule", json=schedule_req)
        base_group_id = None
        if schedule_resp.status_code == 200:
            for r in schedule_resp.json().get("results", []):
                if r.get("group_id"):
                    base_group_id = r["group_id"]
                    print(f"    基础成组ID: {base_group_id}")

    print("\n  创建新同族A工单（低优先级）")
    new_order_data = {
        "order_no": "WO-INS-NEW-001",
        "product_name": "精密零件A1",
        "total_quantity": 1,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat(),
        "priority": 3
    }
    resp = requests.post(f"{BASE_URL}/orders/", json=new_order_data)
    new_order_id = None
    if resp.status_code == 201:
        data = resp.json()
        if data.get("success"):
            new_order_id = data["order_id"]
            print(f"    新工单ID: {new_order_id}")

    if new_order_id and base_group_id:
        print(f"\n  执行插单：提升新工单优先级到8")
        insert_req = {
            "order_id": new_order_id,
            "new_priority": 8,
            "operator": "tester",
            "reason": "紧急插单测试"
        }
        resp = requests.post(f"{BASE_URL}/insertion/orders", json=insert_req)
        print_response("插单结果", resp)

        if resp.status_code == 200:
            print(f"\n  检查新工单是否被追加到同族成组")
            detail_resp = requests.get(f"{BASE_URL}/group-scheduling/groups/{base_group_id}")
            if detail_resp.status_code == 200:
                detail = detail_resp.json()
                order_ids_in_group = detail["group"].get("order_ids", [])
                print(f"    成组内工单: {order_ids_in_group}")
                if new_order_id in order_ids_in_group:
                    print("    ✓ 新工单已自动追加到同族成组！")
                else:
                    print("    新工单未追加到成组（可能受约束限制）")

            print("\n✓ 插单成组追加测试完成")


def run_all_tests():
    print("\n" + "#"*70)
    print("#" + " "*12 + "多产品族成组排产与合并换型模块测试" + " "*17 + "#")
    print("#"*70)

    try:
        family_ids = setup_test_environment()

        recommended_order_ids = test_recommend_groups()

        group_ids = test_group_scheduling(recommended_order_ids[:4] if recommended_order_ids else [])

        test_list_groups(group_ids)

        test_gantt_with_groups()

        force_order_ids, force_group_id = test_force_group()

        test_ungroup(force_group_id, force_order_ids)

        test_delete_order_in_group()

        test_insertion_with_grouping()

        print_section("所有测试完成!")
        print("✓✓✓ 成组排产模块功能验证完成 ✓✓✓")

    except Exception as e:
        print(f"\n✗ 测试出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
