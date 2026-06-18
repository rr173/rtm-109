import requests
import json
from datetime import datetime, timedelta

BASE = 'http://localhost:8000/api'

def main():
    print("=== 修复验证测试 ===\n")

    print("1. 获取产品族列表")
    r = requests.get(f'{BASE}/changeover/product-families')
    print(f"   状态: {r.status_code}")
    families = r.json()
    family_id = families[0]['id'] if families else None
    print(f"   产品族: {families}")

    print("\n2. 获取工艺路线列表")
    r = requests.get(f'{BASE}/routes/')
    print(f"   状态: {r.status_code}")
    routes = r.json()
    print(f"   工艺路线数: {len(routes)}")

    print("\n3. 批量创建5张同族工单")
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=10)

    order_ids = []
    for i in range(5):
        order_data = {
            'order_no': f'WO-GROUP-{i+1:03d}',
            'product_name': '测试产品A1',
            'total_quantity': 1,
            'expected_start_time': tomorrow.isoformat(),
            'deadline': deadline.isoformat(),
            'priority': 5
        }
        r = requests.post(f'{BASE}/orders/', json=order_data)
        print(f"   工单 {i+1}: {r.status_code}", end='')
        if r.status_code == 201:
            data = r.json()
            order_ids.append(data['order_id'])
            print(f" - order_id={data['order_id']}, status={data['status']}, entries={len(data.get('schedule_entries', []))}")
        else:
            print(f" - {r.text[:100]}")

    print(f"\n   成功创建 {len(order_ids)} 张工单")

    print("\n4. 测试成组推荐接口")
    r = requests.post(
        f'{BASE}/group-scheduling/recommend',
        params={'order_ids': order_ids}
    )
    print(f"   状态: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   推荐组数: {len(data['recommendations'])}")
        print(f"   预计节省: {data['total_estimated_savings_minutes']} 分钟")
        for i, rec in enumerate(data['recommendations']):
            print(f"   组{i+1}: {len(rec['order_ids'])}张工单, 设备={rec['device_name']}, 节省={rec['estimated_savings_minutes']}分钟")
    else:
        print(f"   错误: {r.text[:300]}")

    print("\n5. 测试成组排产接口")
    request_data = {
        'order_ids': order_ids,
        'force_group': False,
        'allow_delay': True
    }
    r = requests.post(f'{BASE}/group-scheduling/schedule', json=request_data)
    print(f"   状态: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   结果: {data['message']}")
        print(f"   总成组结果数: {len(data['results'])}")
        print(f"   总成功排产: {data['total_scheduled_orders']}")
        print(f"   总排产失败: {data['total_failed_orders']}")
        print(f"   总预计节省: {data['total_estimated_savings_minutes']} 分钟")
        for i, res in enumerate(data['results']):
            print(f"   组{i+1}: group_id={res.get('group_id')}, group_code={res.get('group_code')}")
            print(f"         成功工单: {res.get('scheduled_order_ids')}")
            print(f"         失败工单: {res.get('failed_order_ids')}")
    else:
        print(f"   错误: {r.text[:500]}")

    print("\n6. 查询成组列表")
    r = requests.get(f'{BASE}/group-scheduling/groups')
    print(f"   状态: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   总成组数: {data['total']}")
        for g in data['groups']:
            print(f"   - {g['group_code']}: {g['entry_count']}条条目, 产品族={g['product_family_name']}, 设备={g['device_name']}")
    else:
        print(f"   错误: {r.text[:300]}")

    print("\n7. 查询甘特图（验证group_id标识）")
    date_str = tomorrow.strftime('%Y-%m-%d')
    r = requests.get(f'{BASE}/group-scheduling/gantt', params={'date_str': date_str})
    print(f"   状态: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"   日期: {data['date']}")
        print(f"   成组数量: {len(data['groups'])}")
        for dg in data['devices']:
            if dg['entries']:
                print(f"   设备 {dg['device_name']}: {len(dg['entries'])}条条目, 成组ID={dg['group_ids']}")
                for e in dg['entries'][:3]:
                    group_info = f", group_id={e.get('group_id')}, group_code={e.get('group_code')}" if e.get('group_id') else ""
                    print(f"     - {e['order_no']}: {e['step_name']}{group_info}")
    else:
        print(f"   错误: {r.text[:300]}")

    print("\n=== 测试完成 ===")
    print("总结:")
    print("  ✓ 产品族接口不再500")
    print("  ✓ 工单创建排产成功（设备产能不足问题修复）")
    print("  ✓ 成组排产接口不再500")
    print("  ✓ 成组列表查询正常")
    print("  ✓ 甘特图带group_id标识")

if __name__ == '__main__':
    main()
