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
    print("=== 全面功能测试 ===")

    # 清空数据（通过删除所有订单和设备）
    # 先不删，用新数据测试

    # 1. 测试锁定功能 - 锁定的工单不能被抢占
    print("\n--- 测试 1: 锁定工单保护 ---")
    # 锁定 WO-001 (id=1)
    r = requests.post(f"{BASE_URL}/orders/1/lock")
    print_response("锁定 WO-001", r)

    # 提交一个高优先级的新工单 WO-004
    start = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    if start < datetime.now():
        start += timedelta(days=1)

    order4 = {
        "order_no": "WO-004",
        "product_name": "产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=6)).isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order4)
    print_response("提交 WO-004（截止时间短，看是否抢占锁定工单）", r)

    # 查看所有工单
    r = requests.get(f"{BASE_URL}/orders/")
    orders = r.json()
    print(f"\n当前工单数量: {len(orders)}")
    for o in orders:
        print(f"  {o['order_no']}: status={o['status']}, locked={o['is_locked']}")
        if o['schedule_entries']:
            for e in o['schedule_entries']:
                if e['step_name'] == '热处理':
                    print(f"    热处理: device={e['device_id']}, {e['start_time']} ~ {e['end_time']}")

    # 2. 测试撤销工单 - 释放时间窗，其他工单回填
    print("\n--- 测试 2: 撤销工单与自动回填 ---")
    # 先查一下 WO-002 的时间
    r = requests.get(f"{BASE_URL}/orders/2")
    wo2_before = r.json()
    print(f"WO-002 删除前的热处理时间: {[e for e in wo2_before['schedule_entries'] if e['step_name']=='热处理'][0]['start_time']}")

    # 删除 WO-001（锁定的那个）
    r = requests.delete(f"{BASE_URL}/orders/1")
    print_response("删除 WO-001", r)

    # 再查 WO-002 的时间，看是否提前了
    r = requests.get(f"{BASE_URL}/orders/2")
    wo2_after = r.json()
    print(f"WO-002 删除后的热处理时间: {[e for e in wo2_after['schedule_entries'] if e['step_name']=='热处理'][0]['start_time']}")

    # 3. 测试排产失败 - 截止时间太近
    print("\n--- 测试 3: 排产失败（瓶颈工序） ---")
    order_fail = {
        "order_no": "WO-FAIL",
        "product_name": "产品A",
        "expected_start_time": start.isoformat(),
        "deadline": (start + timedelta(hours=1)).isoformat()  # 只有1小时，肯定不够
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order_fail)
    print_response("提交时间不足的工单（应该失败）", r)

    # 4. 查看冲突列表
    print("\n--- 测试 4: 冲突列表 ---")
    r = requests.get(f"{BASE_URL}/schedule/conflicts")
    print_response("冲突列表", r)

    # 5. 查看甘特图
    print("\n--- 测试 5: 甘特图数据 ---")
    date_str = start.strftime("%Y-%m-%d")
    r = requests.get(f"{BASE_URL}/schedule/gantt", params={"date_str": date_str})
    data = r.json()
    print(f"甘特图日期: {data['date']}")
    for dev in data['devices']:
        print(f"  {dev['device_name']} ({dev['device_type']}): {len(dev['entries'])} 个任务")
        for e in dev['entries']:
            print(f"    - {e['order_no']} / {e['step_name']}: {e['start_time'][11:16]} ~ {e['end_time'][11:16]}")

    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()
