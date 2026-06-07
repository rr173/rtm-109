import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:9000/api"

def print_response(label, response):
    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"Status: {response.status_code}")
    try:
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print('='*60)

def main():
    print("=== 工艺路线排产服务测试 ===")

    # 1. 创建设备
    devices = [
        {"name": "车床-A1", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "车床-A2", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "热处理炉-B1", "device_type": "热处理", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "检测台-C1", "device_type": "检测", "daily_start": "08:00", "daily_end": "20:00"},
    ]
    for d in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=d)
        if r.status_code == 201:
            print(f"设备 {d['name']} 创建成功")
        else:
            print(f"设备 {d['name']}: {r.status_code} - {r.text}")

    # 2. 查看设备列表
    r = requests.get(f"{BASE_URL}/devices/")
    print_response("设备列表", r)

    # 3. 创建工艺路线 - 产品A
    route_data = {
        "product_name": "产品A",
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": "车床", "duration_minutes": 60, "min_gap_after": 0},
            {"step_order": 2, "step_name": "精车", "device_type": "车床", "duration_minutes": 90, "min_gap_after": 0},
            {"step_order": 3, "step_name": "热处理", "device_type": "热处理", "duration_minutes": 120, "min_gap_after": 30},
            {"step_order": 4, "step_name": "检测", "device_type": "检测", "duration_minutes": 30, "min_gap_after": 0},
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print_response("创建工艺路线-产品A", r)

    # 4. 提交第一个工单
    now = datetime.now()
    start_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if start_time < now:
        start_time += timedelta(days=1)
    deadline = start_time + timedelta(hours=10)

    order1 = {
        "order_no": "WO-001",
        "product_name": "产品A",
        "expected_start_time": start_time.isoformat(),
        "deadline": deadline.isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order1)
    print_response("提交工单 WO-001", r)

    # 5. 提交第二个工单
    order2 = {
        "order_no": "WO-002",
        "product_name": "产品A",
        "expected_start_time": start_time.isoformat(),
        "deadline": (start_time + timedelta(hours=12)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order2)
    print_response("提交工单 WO-002", r)

    # 6. 查看工单列表
    r = requests.get(f"{BASE_URL}/orders/")
    print_response("工单列表", r)

    # 7. 查询甘特图
    date_str = start_time.strftime("%Y-%m-%d")
    r = requests.get(f"{BASE_URL}/schedule/gantt", params={"date_str": date_str})
    print_response(f"甘特图 - {date_str}", r)

    # 8. 锁定第一个工单
    order_id = 1
    r = requests.post(f"{BASE_URL}/orders/{order_id}/lock")
    print_response("锁定工单 1", r)

    # 9. 提交第三个工单（测试抢占）
    order3 = {
        "order_no": "WO-003",
        "product_name": "产品A",
        "expected_start_time": start_time.isoformat(),
        "deadline": (start_time + timedelta(hours=8)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order3)
    print_response("提交工单 WO-003（测试排期紧张）", r)

    # 10. 查询冲突列表
    r = requests.get(f"{BASE_URL}/schedule/conflicts")
    print_response("冲突列表", r)

    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()
