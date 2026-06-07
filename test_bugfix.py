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

def main():
    print("=== Bug 修复验证测试 ===\n")

    # === Bug 1: 时间格式校验 ===
    print("--- Bug 1: 注册设备时工作时间格式校验 ---")

    # 测试乱码时间
    bad_device = {
        "name": "测试设备-坏时间",
        "device_type": "测试",
        "daily_start": "abcdef",
        "daily_end": "乱码"
    }
    r = requests.post(f"{BASE_URL}/devices/", json=bad_device)
    print_response("提交乱码时间设备（应该返回400错误）", r)

    # 测试不完整时间
    bad_device2 = {
        "name": "测试设备-坏时间2",
        "device_type": "测试",
        "daily_start": "25:00",
        "daily_end": "8:"
    }
    r = requests.post(f"{BASE_URL}/devices/", json=bad_device2)
    print_response("提交无效小时设备（应该返回400错误）", r)

    # 正常创建设备
    devices = [
        {"name": "车床-A1", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "车床-A2", "device_type": "车床", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "热处理炉-B1", "device_type": "热处理", "daily_start": "08:00", "daily_end": "20:00"},
        {"name": "检测台-C1", "device_type": "检测", "daily_start": "08:00", "daily_end": "20:00"},
    ]
    for d in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=d)
        if r.status_code == 201:
            print(f"  ✓ 设备 {d['name']} 创建成功")
        else:
            print(f"  ✗ 设备 {d['name']}: {r.status_code}")

    # 创建工艺路线
    route_data = {
        "product_name": "产品A",
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": "车床", "duration_minutes": 60},
            {"step_order": 2, "step_name": "精车", "device_type": "车床", "duration_minutes": 90},
            {"step_order": 3, "step_name": "热处理", "device_type": "热处理", "duration_minutes": 120, "min_gap_after": 30},
            {"step_order": 4, "step_name": "检测", "device_type": "检测", "duration_minutes": 30},
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print(f"\n  ✓ 工艺路线创建: {r.status_code}")

    # 提交第一个工单
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)

    order1 = {
        "order_no": "WO-001",
        "product_name": "产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=8)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order1)
    print(f"  ✓ WO-001 提交: {r.status_code}")

    # === Bug 2: 删除有关联工单的设备 ===
    print("\n--- Bug 2: 删除有排产的设备（应该报错） ---")
    r = requests.delete(f"{BASE_URL}/devices/1")
    print_response("删除车床-A1（有排产，应该返回400错误）", r)

    # 测试删除没有排产的设备（先创一个）
    test_dev = {"name": "待删设备", "device_type": "测试", "daily_start": "08:00", "daily_end": "20:00"}
    r = requests.post(f"{BASE_URL}/devices/", json=test_dev)
    dev_id = r.json()["id"]
    r = requests.delete(f"{BASE_URL}/devices/{dev_id}")
    print_response(f"删除无排产的设备{dev_id}（应该成功204）", r)

    # === Bug 3: 工单被推迟但冲突记录缺失 ===
    print("\n--- Bug 3: 工单被推迟应有冲突记录 ---")

    # 先查一下当前冲突列表（应该为空）
    r = requests.get(f"{BASE_URL}/schedule/conflicts")
    print(f"  初始冲突数量: {r.json()['total']}")

    # 提交第二个工单（和WO-001竞争，会导致WO-001被推后或WO-002被推后）
    order2 = {
        "order_no": "WO-002",
        "product_name": "产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=10)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order2)
    print(f"  ✓ WO-002 提交: {r.status_code}")

    # 再查冲突列表
    r = requests.get(f"{BASE_URL}/schedule/conflicts")
    data = r.json()
    print(f"  提交WO-002后的冲突数量: {data['total']}")
    delayed_conflicts = [c for c in data['conflicts'] if c['conflict_type'] == 'delayed']
    print(f"  其中 delayed 类型冲突: {len(delayed_conflicts)}")
    for c in delayed_conflicts:
        print(f"    - 订单ID {c['order_id']}: {c['description'][:80]}...")

    # 再查一下甘特图确认
    date_str = start.strftime("%Y-%m-%d")
    r = requests.get(f"{BASE_URL}/schedule/gantt", params={"date_str": date_str})
    data = r.json()
    print(f"\n  甘特图概览 ({date_str}):")
    for dev in data['devices']:
        if dev['entries']:
            print(f"    {dev['device_name']}:")
            for e in dev['entries']:
                print(f"      {e['order_no']} / {e['step_name']}: {e['start_time'][11:16]} ~ {e['end_time'][11:16]}")

    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()
