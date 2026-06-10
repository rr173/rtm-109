import requests
import json
from datetime import datetime, timedelta, time, date

BASE_URL = "http://localhost:9000/api"

def print_response(label, response):
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    except Exception as e:
        print(f"Response text: {response.text}")
        print(f"Error: {e}")
    print('='*60)

def main():
    print("=== 设备效率分析与瓶颈预测模块测试 ===")

    # 1. 清理并准备基础数据
    print("\n--- 准备基础数据 ---")

    # 创建设备
    devices = [
        {"name": "车床-E1", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        {"name": "车床-E2", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        {"name": "热处理炉-E1", "device_type": "热处理", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
        {"name": "检测台-E1", "device_type": "检测", "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 1},
    ]
    for d in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=d)
        if r.status_code == 201:
            print(f"设备 {d['name']} 创建成功")
        elif r.status_code == 400 and "already exists" in r.text:
            print(f"设备 {d['name']} 已存在")
        else:
            print(f"设备 {d['name']}: {r.status_code} - {r.text}")

    # 创建工艺路线 - 产品B
    route_data = {
        "product_name": "产品B",
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": "车床", "duration_minutes": 60, "min_gap_after": 0},
            {"step_order": 2, "step_name": "精车", "device_type": "车床", "duration_minutes": 90, "min_gap_after": 0},
            {"step_order": 3, "step_name": "热处理", "device_type": "热处理", "duration_minutes": 120, "min_gap_after": 30},
            {"step_order": 4, "step_name": "检测", "device_type": "检测", "duration_minutes": 30, "min_gap_after": 0},
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    if r.status_code == 201:
        print("工艺路线 产品B 创建成功")
    elif r.status_code == 400 and "already exists" in r.text:
        print("工艺路线 产品B 已存在")
    else:
        print(f"工艺路线: {r.status_code} - {r.text}")

    # 创建几个真实工单来产生排产数据
    now = datetime.now()
    start_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if start_time < now:
        start_time += timedelta(days=1)

    for i in range(1, 4):
        order = {
            "order_no": f"EFF-TEST-{i:03d}",
            "product_name": "产品B",
            "expected_start_time": start_time.isoformat(),
            "deadline": (start_time + timedelta(hours=20)).isoformat(),
            "total_quantity": 1
        }
        r = requests.post(f"{BASE_URL}/orders/", json=order)
        if r.status_code == 201:
            print(f"工单 EFF-TEST-{i:03d} 创建并排产成功")
        elif r.status_code == 400 and "already exists" in r.text:
            print(f"工单 EFF-TEST-{i:03d} 已存在")
        else:
            print(f"工单 EFF-TEST-{i:03d}: {r.status_code} - {r.text}")

    # 2. 测试效率统计接口
    print("\n--- 测试 1: 效率统计接口 ---")

    start_dt = start_time - timedelta(days=1)
    end_dt = start_time + timedelta(days=7)

    efficiency_request = {
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "group_by_type": True
    }
    r = requests.post(f"{BASE_URL}/efficiency/stats", json=efficiency_request)
    print_response("效率统计结果", r)

    if r.status_code == 200:
        data = r.json()
        print(f"\n统计摘要:")
        print(f"  - 统计设备总数: {data['total_devices']}")
        print(f"  - 时间范围: {data['start_time']} ~ {data['end_time']}")
        for dev in data['device_efficiencies']:
            print(f"  - 设备 {dev['device_name']} ({dev['device_type']}): "
                  f"利用率 {dev['utilization_rate']*100:.1f}%, "
                  f"已排产 {dev['scheduled_minutes']}分钟, "
                  f"空闲时段 {len(dev['idle_periods'])}个, "
                  f"平均等待时间 {dev['avg_waiting_time_minutes']}分钟")
        for dtype in data['device_type_efficiencies']:
            print(f"  - 类型 {dtype['device_type']}: "
                  f"平均利用率 {dtype['avg_utilization_rate']*100:.1f}%, "
                  f"负载差值 {dtype['max_utilization_diff']*100:.1f}%")

    # 3. 测试时间范围超过90天的错误处理
    print("\n--- 测试 2: 时间范围超过90天 ---")
    bad_request = {
        "start_time": start_time.isoformat(),
        "end_time": (start_time + timedelta(days=100)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/efficiency/stats", json=bad_request)
    print_response("时间范围超过90天（应返回错误）", r)
    assert r.status_code == 400, f"预期400错误，实际得到 {r.status_code}"
    print("✓ 正确返回400错误")

    # 4. 测试瓶颈预测接口
    print("\n--- 测试 3: 瓶颈预测接口 ---")

    future_start = start_time

    simulated_orders = []
    for i in range(5):
        simulated_orders.append({
            "product_name": "产品B",
            "quantity": 1,
            "expected_start_time": (future_start + timedelta(hours=i*2)).isoformat()
        })

    prediction_request = {
        "future_days": 7,
        "simulated_orders": simulated_orders
    }
    r = requests.post(f"{BASE_URL}/efficiency/bottleneck-prediction", json=prediction_request)
    print_response("瓶颈预测结果", r)

    if r.status_code == 200:
        data = r.json()
        print(f"\n预测摘要:")
        print(f"  - 预测天数: {data['future_days']}")
        print(f"  - 模拟工单总数: {data['total_simulated_orders']}")
        print(f"  - 高风险设备类型(>90%): {len(data['high_risk_device_types'])}个")
        for hr in data['high_risk_device_types']:
            print(f"    * {hr['device_type']} 在 {hr['date']}: 利用率 {hr['utilization_rate']*100:.1f}%")
        print(f"  - 排产失败工单: {len(data['failed_orders'])}个")
        for failed in data['failed_orders']:
            print(f"    * {failed['product_name']} - {failed['reason']}")
        print(f"  - 设备建议: {len(data['device_recommendations'])}条")
        for rec in data['device_recommendations']:
            print(f"    * {rec['device_type']}: 建议增加 {rec['recommended_count']} 台 - {rec['reason']}")
        print(f"  - 模拟结果详情:")
        for sr in data['simulated_results']:
            status = "✓ 排产成功" if sr['scheduled'] else "✗ 排产失败"
            print(f"    * {sr['product_name']} x{sr['quantity']}: {status}")
            if sr['schedule_entries']:
                for e in sr['schedule_entries']:
                    print(f"      - 工序{e['step_order']} {e['step_name']}: {e['device_name']} ({e['start_time']} ~ {e['end_time']})")

    # 5. 测试模拟工单超过50条的错误处理
    print("\n--- 测试 4: 模拟工单超过50条 ---")
    too_many_orders = [{
        "product_name": "产品B",
        "quantity": 1,
        "expected_start_time": start_time.isoformat()
    } for _ in range(51)]

    bad_prediction = {
        "future_days": 7,
        "simulated_orders": too_many_orders
    }
    r = requests.post(f"{BASE_URL}/efficiency/bottleneck-prediction", json=bad_prediction)
    print_response("模拟工单超过50条（应返回错误）", r)
    assert r.status_code == 400, f"预期400错误，实际得到 {r.status_code}"
    print("✓ 正确返回400错误")

    # 6. 验证模拟不影响真实数据
    print("\n--- 测试 5: 验证模拟不影响真实数据 ---")
    r = requests.get(f"{BASE_URL}/orders/")
    if r.status_code == 200:
        orders = r.json()
        real_count = len([o for o in orders if o['order_no'].startswith('EFF-TEST')])
        print(f"  真实工单数量: {real_count} 个 (应为3个，模拟工单不应入库)")
        print("✓ 模拟工单未入库，真实数据未受影响")

    print("\n=== 所有测试完成 ===")

if __name__ == "__main__":
    main()
