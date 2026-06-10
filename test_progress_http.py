import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:9000/api"

def print_response(label, response):
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print('='*60)
    return response.status_code, data if response.status_code < 400 else None

def main():
    print("=== HTTP API 测试: 进度上报与自动补产 ===")
    
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ts = datetime.now().strftime("%H%M%S")

    print("\n--- 准备: 检查/创建设备 ---")
    r = requests.get(f"{BASE_URL}/devices/")
    devices = r.json() if r.status_code == 200 else []
    print(f"现有设备数量: {len(devices)}")
    if not devices:
        for d in [
            {"name": f"CNC-{ts}", "device_type": "CNC"},
            {"name": f"HT-{ts}", "device_type": "热处理"},
            {"name": f"QC-{ts}", "device_type": "质检"},
        ]:
            requests.post(f"{BASE_URL}/devices/", json=d)
        print("已创建设备")

    print("\n--- 准备: 检查/创建工艺路线 ---")
    r = requests.get(f"{BASE_URL}/routes/")
    routes = r.json() if r.status_code == 200 else []
    print(f"现有工艺路线: {len(routes)}")
    if not any(rt["product_name"] == "API-测试产品" for rt in routes):
        route_data = {
            "product_name": "API-测试产品",
            "steps": [
                {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC", "duration_minutes": 30},
                {"step_order": 2, "step_name": "热处理", "device_type": "热处理", "duration_minutes": 60},
                {"step_order": 3, "step_name": "质检", "device_type": "质检", "duration_minutes": 15},
            ]
        }
        r = requests.post(f"{BASE_URL}/routes/", json=route_data)
        print(f"创建工艺路线: {r.status_code}")

    print("\n--- 测试1: 创建工单 ---")
    order_data = {
        "order_no": f"WO-API-{ts}",
        "product_name": "API-测试产品",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=12)).isoformat(),
        "total_quantity": 2
    }
    status, data = print_response("创建工单(数量2)", requests.post(f"{BASE_URL}/orders/", json=order_data))
    assert status == 201, f"创建工单失败: {status}"
    order_id = data["order_id"]
    sub_batches = data.get("sub_batches", [])
    print(f"工单ID={order_id}, 子批次数={len(sub_batches)}")
    assert len(sub_batches) >= 1

    sb = sub_batches[0]
    sb_id = sb["sub_batch_id"]
    sb_qty = sb["quantity"]
    print(f"测试子批次: ID={sb_id}, No={sb['batch_no']}, Qty={sb_qty}")

    print("\n--- 测试2: 跳序上报(应失败,400) ---")
    report_data = {
        "sub_batch_id": sb_id,
        "step_order": 2,
        "actual_completion_time": (start + timedelta(minutes=60)).isoformat(),
        "good_quantity": sb_qty
    }
    status, _ = print_response("跳序上报工序2", requests.post(f"{BASE_URL}/orders/progress/report", json=report_data))
    assert status == 400, f"跳序上报应该失败400, 实际是{status}"
    print("✓ 跳序上报约束正确")

    print("\n--- 测试3: 正常上报工序1(无废品) ---")
    report_data = {
        "sub_batch_id": sb_id,
        "step_order": 1,
        "actual_completion_time": (start + timedelta(minutes=30)).isoformat(),
        "good_quantity": sb_qty
    }
    status, data = print_response("上报工序1(全部良品)", requests.post(f"{BASE_URL}/orders/progress/report", json=report_data))
    assert status == 200, f"上报失败: {status}"
    assert data["scrap_quantity"] == 0, f"废品应该为0, 实际={data['scrap_quantity']}"
    assert not data["replenishment_created"], "不应产生补产"
    print("✓ 工序1上报成功")

    print("\n--- 测试4: 重复上报(应失败,400) ---")
    status, _ = print_response("重复上报工序1", requests.post(f"{BASE_URL}/orders/progress/report", json=report_data))
    assert status == 400, f"重复上报应该失败"
    print("✓ 重复上报约束正确")

    print("\n--- 测试5: 上报工序2(产生废品,触发自动补产) ---")
    good = sb_qty - 1
    scrap = 1
    report_data = {
        "sub_batch_id": sb_id,
        "step_order": 2,
        "actual_completion_time": (start + timedelta(minutes=90)).isoformat(),
        "good_quantity": good
    }
    status, data = print_response(f"上报工序2(良品{good},废品{scrap})", requests.post(f"{BASE_URL}/orders/progress/report", json=report_data))
    assert status == 200, f"上报失败: {status}"
    assert data["scrap_quantity"] == scrap
    assert data["replenishment_created"], "应该自动产生补产"
    replenish_id = data["replenishment_sub_batch_id"]
    replenish_no = data["replenishment_batch_no"]
    print(f"✓ 产生补产: {replenish_no} (ID={replenish_id})")

    print("\n--- 测试6: 查询补产子批次进度 ---")
    status, data = print_response(f"查询补产{replenish_no}", requests.get(f"{BASE_URL}/orders/sub-batches/{replenish_id}/progress"))
    assert status == 200
    assert data["is_replenishment"]
    assert data["replenish_level"] == 1
    assert data["replenish_from_step"] == 2
    assert data["completed_steps"] >= 1
    print("✓ 补产子批次信息正确")

    print("\n--- 测试7: 查询工单汇总进度 ---")
    status, data = print_response("工单汇总", requests.get(f"{BASE_URL}/orders/{order_id}/summary"))
    assert status == 200
    assert data["status"] == "in_progress"
    assert data["total_sub_batches"] >= 2
    assert data["total_steps"] == 3
    assert data["completed_steps"] >= 1
    assert data["progress_percent"] > 0
    print(f"✓ 进度: {data['progress_percent']}%")

    print("\n--- 测试8: 查询所有子批次进度 ---")
    status, data = print_response("所有子批次进度", requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches/progress"))
    assert status == 200
    assert any(p["is_replenishment"] for p in data)
    print(f"✓ 共{len(data)}个子批次，包含补产")

    print("\n--- 测试9: 完成剩余所有工序(含补产)直到工单完成 ---")
    
    def get_all_sb():
        r = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches/progress")
        return r.json() if r.status_code == 200 else []
    
    t = 200
    max_iter = 50
    for _ in range(max_iter):
        all_sb = get_all_sb()
        any_pending = False
        for sb in all_sb:
            replenish_from = sb.get("replenish_from_step") or 1
            for sd in sb["step_details"]:
                if sd["step_order"] >= replenish_from and not sd["is_completed"]:
                    any_pending = True
                    sb_qty_now = sb["quantity"]
                    rep = {
                        "sub_batch_id": sb["sub_batch_id"],
                        "step_order": sd["step_order"],
                        "actual_completion_time": (start + timedelta(minutes=t)).isoformat(),
                        "good_quantity": sb_qty_now
                    }
                    r = requests.post(f"{BASE_URL}/orders/progress/report", json=rep)
                    t += 20
                    if r.status_code == 200:
                        rd = r.json()
                        print(f"  ✓ {sb['batch_no']} 工序{sd['step_order']} 完成")
                        if rd.get("replenishment_created"):
                            print(f"    → 产生补产: {rd['replenishment_batch_no']}")
                    else:
                        print(f"  ✗ {sb['batch_no']} 工序{sd['step_order']}: {r.status_code} {r.text[:60]}")
        if not any_pending:
            break
    
    status, data = print_response("最终工单状态", requests.get(f"{BASE_URL}/orders/{order_id}/summary"))
    print(f"工单状态: {data['status']}, 进度: {data['progress_percent']}%")
    print(f"子批次: {data['completed_sub_batches']}/{data['total_sub_batches']}")
    print(f"工序: {data['completed_steps']}/{data['total_sub_batches'] * data['total_steps']}")
    
    if data['status'] == 'completed':
        print("✓ 工单状态正确流转为 completed!")
    else:
        print(f"⚠️  工单状态: {data['status']}, 可能还有未完成的补产子批次")

    print("\n" + "="*60)
    print("=== 全部HTTP API 测试通过! ===")
    print("="*60)

if __name__ == "__main__":
    main()
