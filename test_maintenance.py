import requests
import json
from datetime import datetime, timedelta, date

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
    print("=== 设备维护计划功能测试 ===")

    # 1. 创建一个测试用设备(白班)
    device_name = f"测试车床-{datetime.now().strftime('%H%M%S')}"
    device_data = {
        "name": device_name,
        "device_type": "测试车床",
        "daily_start": "08:00",
        "daily_end": "20:00"
    }
    r = requests.post(f"{BASE_URL}/devices/", json=device_data)
    if r.status_code != 201:
        # 如果设备名冲突，换一个
        device_name = f"测试车床-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        device_data["name"] = device_name
        r = requests.post(f"{BASE_URL}/devices/", json=device_data)
    print_response("创建设备", r)
    device_id = r.json()["id"]
    device_type = r.json()["device_type"]

    print(f"\n使用设备 ID: {device_id}, 类型: {device_type}")

    # 2. 创建维护计划 - 每周三(2) 14:00-16:00
    plan_data = {
        "device_id": device_id,
        "day_of_week": 2,
        "start_time": "14:00",
        "end_time": "16:00",
        "description": "每周保养"
    }
    r = requests.post(f"{BASE_URL}/maintenance/plans", json=plan_data)
    print_response("创建维护计划(每周三14:00-16:00)", r)

    # 3. 查看维护计划列表
    r = requests.get(f"{BASE_URL}/maintenance/plans")
    print_response("维护计划列表", r)

    # 4. 查看单个维护计划
    plan_id = r.json()[0]["id"]
    r = requests.get(f"{BASE_URL}/maintenance/plans/{plan_id}")
    print_response("单个维护计划详情", r)

    # 5. 更新维护计划
    update_data = {
        "description": "每周三定期保养"
    }
    r = requests.put(f"{BASE_URL}/maintenance/plans/{plan_id}", json=update_data)
    print_response("更新维护计划", r)

    # 6. 查看设备7天时间线
    today = date.today().isoformat()
    r = requests.get(f"{BASE_URL}/maintenance/device-timeline/{device_id}?days=7&start_date={today}")
    print_response(f"设备时间线(7天，从{today})", r)

    # 7. 测试排产是否避开维护窗口
    # 先创建工艺路线和工单
    print("\n=== 测试排产是否避开维护窗口 ===")

    product_name = f"测试产品-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    route_data = {
        "product_name": product_name,
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": device_type, "duration_minutes": 120, "min_gap_after": 0}
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print_response("创建工艺路线", r)

    # 创建一个工单，期望开始时间是周三13:00，看看会不会被顺延到16:00之后
    today = date.today()
    days_until_wednesday = (2 - today.weekday() + 7) % 7
    if days_until_wednesday == 0:
        days_until_wednesday = 7
    next_wednesday = today + timedelta(days=days_until_wednesday)
    expected_start = datetime.combine(next_wednesday, datetime.strptime("13:00", "%H:%M").time())
    deadline = expected_start + timedelta(days=2)

    order_data = {
        "order_no": f"TEST-MAINT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "product_name": product_name,
        "expected_start_time": expected_start.isoformat(),
        "deadline": deadline.isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建工单", r)

    # 创建工单可能直接返回排产结果，也可能需要单独排产
    order_data = r.json()
    if "order_id" in order_data:
        order_id = order_data["order_id"]
    else:
        order_id = order_data["id"]

    # 如果还没排产，就手动触发排产
    if "schedule_entries" not in order_data or not order_data.get("schedule_entries"):
        r = requests.post(f"{BASE_URL}/orders/{order_id}/schedule")
        print_response("工单排产结果", r)
        result = r.json()
    else:
        result = order_data

    # 检查排产结果
    if result.get("success"):
        entries = result.get("schedule_entries", [])
        for entry in entries:
            start = entry["start_time"]
            end = entry["end_time"]
            print(f"\n排产时段: {start} - {end}")
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            print(f"  星期: {start_dt.weekday()} (0=周一, 6=周日)")
            print(f"  维护窗口: 周三 14:00-16:00")
            print(f"  检查是否避开维护窗口...")
            if start_dt.weekday() == 2:
                maint_start = datetime.combine(start_dt.date(), datetime.strptime("14:00", "%H:%M").time())
                maint_end = datetime.combine(start_dt.date(), datetime.strptime("16:00", "%H:%M").time())
                if start_dt < maint_end and end_dt > maint_start:
                    print("  ⚠️  警告: 排产与维护窗口重叠!")
                else:
                    print("  ✓ 正确: 排产避开了维护窗口")
                    if start_dt >= maint_end:
                        print(f"    (排产顺延到了维护结束后 {start_dt.hour}:{start_dt.minute:02d})")
            else:
                print("  (不在周三，跳过验证)")

    # 再看一次时间线
    r = requests.get(f"{BASE_URL}/maintenance/device-timeline/{device_id}?days=7&start_date={today.isoformat()}")
    print_response(f"排产后的设备时间线(7天)", r)


if __name__ == "__main__":
    main()
