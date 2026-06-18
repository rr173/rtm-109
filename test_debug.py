import requests
import json
from datetime import datetime, timedelta

BASE = 'http://localhost:8000/api'

def test_1_create_basic_data():
    print("=== Test 1: 创建基础数据 ===")

    for i in range(2):
        r = requests.post(f'{BASE}/devices/', json={
            'name': f'测试设备-{i+1}',
            'device_type': 'CNC',
            'daily_start': '08:00',
            'daily_end': '20:00',
            'max_batch_size': 10
        })
        print(f'  设备{i+1}: {r.status_code}')

    r = requests.post(f'{BASE}/changeover/product-families', json={
        'name': '测试产品族A',
        'description': '测试用'
    })
    print(f'  产品族创建: {r.status_code}')
    family_id = None
    if r.status_code == 201:
        family_id = r.json()['id']
    else:
        r = requests.get(f'{BASE}/changeover/product-families')
        for f in r.json():
            if f['name'] == '测试产品族A':
                family_id = f['id']
                break
    print(f'  产品族ID: {family_id}')

    route_data = {
        'product_name': '测试产品A1',
        'product_family_id': family_id,
        'steps': [
            {'step_order': 1, 'step_name': 'CNC加工', 'device_type': 'CNC', 'duration_minutes': 60, 'min_gap_after': 10}
        ]
    }
    r = requests.post(f'{BASE}/routes/', json=route_data)
    print(f'  工艺路线: {r.status_code}')
    if r.status_code != 201:
        print(f'    错误: {r.text[:200]}')

    return family_id


def test_2_create_order():
    print("\n=== Test 2: 创建工单（测试设备产能不足问题） ===")

    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=5)

    order_data = {
        'order_no': 'WO-TEST-001',
        'product_name': '测试产品A1',
        'total_quantity': 2,
        'expected_start_time': tomorrow.isoformat(),
        'deadline': deadline.isoformat()
    }
    r = requests.post(f'{BASE}/orders/', json=order_data)
    print(f'  创建工单: {r.status_code}')
    if r.status_code == 201:
        data = r.json()
        print(f'  成功: order_id={data.get("order_id")}, status={data.get("status")}')
        print(f'  schedule_entries数: {len(data.get("schedule_entries", []))}')
        return data.get('order_id')
    else:
        print(f'  失败: {r.status_code}')
        print(f'  错误信息: {r.text[:300]}')
        return None


def test_3_group_scheduling(order_ids):
    print("\n=== Test 3: 成组排产接口测试 ===")

    if not order_ids:
        print("  跳过：没有工单")
        return

    request_data = {
        'order_ids': order_ids,
        'force_group': False,
        'allow_delay': True
    }
    r = requests.post(f'{BASE}/group-scheduling/schedule', json=request_data)
    print(f'  成组排产: {r.status_code}')
    if r.status_code == 200:
        data = r.json()
        print(f'  成功: {data.get("message")}')
        print(f'  总成组数: {len(data.get("results", []))}')
        print(f'  成功排产: {data.get("total_scheduled_orders")}')
        print(f'  排产失败: {data.get("total_failed_orders")}')
        for i, res in enumerate(data.get('results', [])):
            print(f'  组{i+1}: group_id={res.get("group_id")}, group_code={res.get("group_code")}, 成功={res.get("scheduled_order_ids")}')
    else:
        print(f'  失败: {r.status_code}')
        print(f'  错误: {r.text[:500]}')


def test_4_list_groups():
    print("\n=== Test 4: 查询成组列表 ===")

    r = requests.get(f'{BASE}/group-scheduling/groups')
    print(f'  查询成组: {r.status_code}')
    if r.status_code == 200:
        data = r.json()
        print(f'  总成组: {data.get("total")}')
        for g in data.get('groups', []):
            print(f'    - {g["group_code"]}: {g.get("entry_count")}条, 设备={g.get("device_name")}')
    else:
        print(f'  错误: {r.text[:300]}')


if __name__ == '__main__':
    family_id = test_1_create_basic_data()

    order_ids = []
    for i in range(3):
        oid = test_2_create_order()
        if oid:
            order_ids.append(oid)

    test_3_group_scheduling(order_ids)
    test_4_list_groups()

    print("\n=== 测试完成 ===")
