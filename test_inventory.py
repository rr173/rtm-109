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
    print("=== 物料需求与库存锁定模块功能测试 ===")

    # 1. 创建测试物料
    print("\n--- 1. 创建测试物料 ---")
    materials = [
        {"name": "硬质合金刀具", "unit": "把", "initial_quantity": 10, "description": "车削用刀具"},
        {"name": "冷却液", "unit": "升", "initial_quantity": 50, "description": "水溶性冷却液"},
        {"name": "保护气体", "unit": "立方米", "initial_quantity": 20, "description": "热处理用氩气"}
    ]
    material_ids = []
    for mat in materials:
        r = requests.post(f"{BASE_URL}/inventory/materials", json=mat)
        if r.status_code == 400:
            print(f"物料 '{mat['name']}' 已存在，跳过创建")
            r = requests.get(f"{BASE_URL}/inventory/materials")
            all_mats = r.json()
            for m in all_mats:
                if m["name"] == mat["name"]:
                    material_ids.append(m["id"])
                    break
        else:
            print_response(f"创建物料: {mat['name']}", r)
            material_ids.append(r.json()["id"])

    tool_id, coolant_id, gas_id = material_ids
    print(f"\n物料ID: 刀具={tool_id}, 冷却液={coolant_id}, 保护气体={gas_id}")

    # 2. 查询当前库存
    print("\n--- 2. 查询当前库存 ---")
    r = requests.get(f"{BASE_URL}/inventory/inventory/all")
    print_response("所有物料库存", r)

    # 3. 物料入库测试
    print("\n--- 3. 物料入库测试 ---")
    r = requests.post(f"{BASE_URL}/inventory/materials/{tool_id}/stock-in", json={"quantity": 5, "remark": "采购入库"})
    print_response("刀具入库 5 把", r)

    # 4. 创建设备
    print("\n--- 4. 创建设备 ---")
    device_name = f"测试车床-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    device_data = {
        "name": device_name,
        "device_type": "车床",
        "daily_start": "08:00",
        "daily_end": "20:00"
    }
    r = requests.post(f"{BASE_URL}/devices/", json=device_data)
    print_response("创建车床设备", r)

    furnace_name = f"测试热处理炉-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    furnace_data = {
        "name": furnace_name,
        "device_type": "热处理炉",
        "daily_start": "08:00",
        "daily_end": "20:00"
    }
    r = requests.post(f"{BASE_URL}/devices/", json=furnace_data)
    print_response("创建热处理炉设备", r)

    # 5. 创建带物料需求的工艺路线
    print("\n--- 5. 创建带物料需求的工艺路线 ---")
    product_name = f"测试轴类零件-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    route_data = {
        "product_name": product_name,
        "steps": [
            {
                "step_order": 1,
                "step_name": "粗车",
                "device_type": "车床",
                "duration_minutes": 60,
                "min_gap_after": 0,
                "material_requirements": [
                    {"material_id": tool_id, "quantity": 1},
                    {"material_id": coolant_id, "quantity": 5}
                ]
            },
            {
                "step_order": 2,
                "step_name": "热处理",
                "device_type": "热处理炉",
                "duration_minutes": 120,
                "min_gap_after": 0,
                "material_requirements": [
                    {"material_id": gas_id, "quantity": 3}
                ]
            }
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print_response("创建带物料需求的工艺路线", r)

    # 6. 查询工艺路线详情，验证物料需求
    print("\n--- 6. 查询工艺路线详情 ---")
    r = requests.get(f"{BASE_URL}/routes/{product_name}")
    print_response("工艺路线详情（含物料需求）", r)

    # 7. 创建工单并排产 - 库存足够的情况
    print("\n--- 7. 创建工单并排产（库存足够） ---")
    expected_start = datetime.now() + timedelta(days=1)
    expected_start = expected_start.replace(hour=9, minute=0, second=0, microsecond=0)
    deadline = expected_start + timedelta(days=3)

    order_data = {
        "order_no": f"TEST-MAT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "product_name": product_name,
        "expected_start_time": expected_start.isoformat(),
        "deadline": deadline.isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建工单并排产", r)
    order_result = r.json()
    order_id = order_result.get("order_id")

    if order_result.get("success"):
        print("\n✓ 排产成功！")

        # 8. 检查排产后库存变化
        print("\n--- 8. 排产后库存查询 ---")
        r = requests.get(f"{BASE_URL}/inventory/inventory/all")
        print_response("排产后的库存状态", r)

        # 9. 查询工单锁定的物料明细
        print("\n--- 9. 查询工单锁定物料明细 ---")
        r = requests.get(f"{BASE_URL}/inventory/order-locks/{order_id}")
        print_response("工单物料锁定明细", r)
    else:
        print(f"\n✗ 排产失败: {order_result.get('message')}")

    # 10. 测试物料不足的情况
    print("\n--- 10. 测试物料不足时排产失败 ---")

    # 先创建一个需要大量物料的工艺路线
    product_name2 = f"测试大零件-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    route_data2 = {
        "product_name": product_name2,
        "steps": [
            {
                "step_order": 1,
                "step_name": "重型车削",
                "device_type": "车床",
                "duration_minutes": 60,
                "min_gap_after": 0,
                "material_requirements": [
                    {"material_id": tool_id, "quantity": 100}
                ]
            }
        ]
    }
    r = requests.post(f"{BASE_URL}/routes/", json=route_data2)
    print_response("创建大量物料需求的工艺路线", r)

    # 创建工单
    order_data2 = {
        "order_no": f"TEST-MAT-SHORT-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "product_name": product_name2,
        "expected_start_time": expected_start.isoformat(),
        "deadline": deadline.isoformat()
    }
    r = requests.post(f"{BASE_URL}/orders/", json=order_data2)
    print_response("物料不足时排产结果", r)

    if not r.json().get("success"):
        print("\n✓ 正确：物料不足时排产失败")
        print(f"  错误信息: {r.json().get('message')}")

    # 11. 测试删除工单后物料释放
    print("\n--- 11. 测试删除工单后物料释放 ---")
    if order_id and order_result.get("success"):
        # 先记录删除前的库存
        r = requests.get(f"{BASE_URL}/inventory/materials/{tool_id}/inventory")
        before_delete = r.json()
        print(f"删除工单前刀具库存: 总{before_delete['total_quantity']}, 锁定{before_delete['locked_quantity']}, 可用{before_delete['available_quantity']}")

        # 删除工单
        r = requests.delete(f"{BASE_URL}/orders/{order_id}")
        print_response("删除工单", r)

        # 检查删除后的库存
        r = requests.get(f"{BASE_URL}/inventory/materials/{tool_id}/inventory")
        after_delete = r.json()
        print(f"删除工单后刀具库存: 总{after_delete['total_quantity']}, 锁定{after_delete['locked_quantity']}, 可用{after_delete['available_quantity']}")

        if after_delete["locked_quantity"] < before_delete["locked_quantity"]:
            print("\n✓ 正确：删除工单后物料锁定已释放")
        else:
            print("\n✗ 错误：删除工单后物料锁定未释放")

    print("\n=== 测试完成 ===")


if __name__ == "__main__":
    main()
