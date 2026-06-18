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

def setup_test_environment():
    print("=== 设置测试环境 ===")
    
    devices = [
        {"name": "数控车床-1", "device_type": "数控车床", "daily_start": "08:00", "daily_end": "22:00"},
        {"name": "数控车床-2", "device_type": "数控车床", "daily_start": "08:00", "daily_end": "22:00"},
        {"name": "精密磨床-1", "device_type": "精密磨床", "daily_start": "08:00", "daily_end": "22:00"},
        {"name": "普通车床-1", "device_type": "普通车床", "daily_start": "08:00", "daily_end": "22:00"},
        {"name": "普通车床-2", "device_type": "普通车床", "daily_start": "08:00", "daily_end": "22:00"},
    ]
    
    device_ids = {}
    for d in devices:
        r = requests.post(f"{BASE_URL}/devices/", json=d)
        if r.status_code == 201:
            device_ids[d["name"]] = r.json()["id"]
            print(f"设备 {d['name']} 创建成功, ID: {device_ids[d['name']]}")
        elif r.status_code == 400 and "already exists" in r.text:
            r_list = requests.get(f"{BASE_URL}/devices/", params={"device_type": d["device_type"]})
            for dev in r_list.json():
                if dev["name"] == d["name"]:
                    device_ids[d["name"]] = dev["id"]
                    print(f"设备 {d['name']} 已存在, ID: {device_ids[d['name']}")
    
    skills = [
        {"name": "数控车工", "code": "CNC-TURN", "description": "操作数控车床", "device_types": ["数控车床"]},
        {"name": "精密磨工", "code": "GRIND-HIGH", "description": "操作精密磨床", "device_types": ["精密磨床"]},
        {"name": "普通车工", "code": "TURN-LOW", "description": "操作普通车床", "device_types": ["普通车床"]},
        {"name": "高级数控车工", "code": "CNC-TURN-HIGH", "description": "操作高精度数控车床", "device_types": ["数控车床"]},
    ]
    
    skill_ids = {}
    for s in skills:
        r = requests.post(f"{BASE_URL}/skills/", json=s)
        if r.status_code == 201:
            skill_ids[s["name"]] = r.json()["id"]
            print(f"技能 {s['name']} 创建成功, ID: {skill_ids[s['name']]}")
        elif r.status_code == 400 and "already exists" in r.text:
            r_list = requests.get(f"{BASE_URL}/skills/")
            for skill in r_list.json():
                if skill["name"] == s["name"]:
                    skill_ids[s["name"]] = skill["id"]
                    print(f"技能 {s['name']} 已存在, ID: {skill_ids[s['name']]}")
    
    teams = [
        {"name": "甲班", "description": "早班"},
        {"name": "乙班", "description": "中班"},
        {"name": "丙班", "description": "夜班"},
    ]
    
    team_ids = {}
    for t in teams:
        r = requests.post(f"{BASE_URL}/teams/", json=t)
        if r.status_code == 201:
            team_ids[t["name"]] = r.json()["id"]
            print(f"班组 {t['name']} 创建成功, ID: {team_ids[t['name']]}")
        elif r.status_code == 400 and "already exists" in r.text:
            r_list = requests.get(f"{BASE_URL}/teams/")
            for team in r_list.json():
                if team["name"] == t["name"]:
                    team_ids[t["name"]] = team["id"]
                    print(f"班组 {t['name']} 已存在, ID: {team_ids[t['name']]}")
    
    employees = [
        {"employee_no": "E001", "name": "张三", "team_id": team_ids["甲班"], 
         "skills": [{"skill_id": skill_ids["数控车工"], "level": 3}]},
        {"employee_no": "E002", "name": "李四", "team_id": team_ids["甲班"],
         "skills": [{"skill_id": skill_ids["精密磨工"], "level": 5}, {"skill_id": skill_ids["普通车工"], "level": 3}]},
        {"employee_no": "E003", "name": "王五", "team_id": team_ids["乙班"],
         "skills": [{"skill_id": skill_ids["数控车工"], "level": 4}]},
        {"employee_no": "E004", "name": "赵六", "team_id": team_ids["乙班"],
         "skills": [{"skill_id": skill_ids["普通车工"], "level": 2}]},
        {"employee_no": "E005", "name": "钱七", "team_id": team_ids["丙班"],
         "skills": [{"skill_id": skill_ids["数控车工"], "level": 2}]},
    ]
    
    employee_ids = {}
    for e in employees:
        r = requests.post(f"{BASE_URL}/employees/", json=e)
        if r.status_code == 201:
            employee_ids[e["name"]] = r.json()["id"]
            print(f"人员 {e['name']} 创建成功, ID: {employee_ids[e['name']]}")
        elif r.status_code == 400 and "already exists" in r.text:
            r_list = requests.get(f"{BASE_URL}/employees/")
            for emp in r_list.json():
                if emp["employee_no"] == e["employee_no"]:
                    employee_ids[e["name"]] = emp["id"]
                    print(f"人员 {e['name']} 已存在, ID: {employee_ids[e['name']]}")
    
    route_data = {
        "product_name": "人员测试产品A",
        "steps": [
            {"step_order": 1, "step_name": "粗车", "device_type": "普通车床", "duration_minutes": 60, "min_gap_after": 0,
             "required_skill_id": skill_ids["普通车工"], "required_skill_level": 2},
            {"step_order": 2, "step_name": "精车", "device_type": "数控车床", "duration_minutes": 90, "min_gap_after": 0,
             "required_skill_id": skill_ids["数控车工"], "required_skill_level": 3},
            {"step_order": 3, "step_name": "精密磨削", "device_type": "精密磨床", "duration_minutes": 120, "min_gap_after": 0,
             "required_skill_id": skill_ids["精密磨工"], "required_skill_level": 5},
        ]
    }
    
    r = requests.post(f"{BASE_URL}/routes/", json=route_data)
    if r.status_code == 201:
        print("工艺路线A创建成功")
    elif r.status_code == 400 and "already exists" in r.text:
        print("工艺路线A已存在")
    
    route_data_b = {
        "product_name": "人员测试产品B",
        "steps": [
            {"step_order": 1, "step_name": "数控加工", "device_type": "数控车床", "duration_minutes": 120, "min_gap_after": 0,
             "required_skill_id": skill_ids["数控车工"], "required_skill_level": 3},
            {"step_order": 2, "step_name": "普通车削", "device_type": "普通车床", "duration_minutes": 60, "min_gap_after": 0,
             "required_skill_id": skill_ids["普通车工"], "required_skill_level": 2},
        ]
    }
    
    r = requests.post(f"{BASE_URL}/routes/", json=route_data_b)
    if r.status_code == 201:
        print("工艺路线B创建成功")
    elif r.status_code == 400 and "already exists" in r.text:
        print("工艺路线B已存在")
    
    return device_ids, skill_ids, team_ids, employee_ids

def create_shift_schedules(employee_ids, start_date):
    print("\n=== 创建排班计划 ===")
    
    monday = start_date - timedelta(days=start_date.weekday())
    
    shift_definitions = {
        "morning": {"start": "08:00", "end": "16:00"},
        "afternoon": {"start": "16:00", "end": "00:00"},
        "night": {"start": "00:00", "end": "08:00"},
        "rest": {"start": None, "end": None, "is_rest": True}
    }
    
    schedules = [
        {"employee_id": employee_ids["张三"], "week_start": monday.isoformat(),
         "monday": shift_definitions["morning"],
         "tuesday": shift_definitions["morning"],
         "wednesday": shift_definitions["morning"],
         "thursday": shift_definitions["morning"],
         "friday": shift_definitions["morning"],
         "saturday": shift_definitions["rest"],
         "sunday": shift_definitions["rest"]},
        {"employee_id": employee_ids["李四"], "week_start": monday.isoformat(),
         "monday": shift_definitions["morning"],
         "tuesday": shift_definitions["morning"],
         "wednesday": shift_definitions["morning"],
         "thursday": shift_definitions["morning"],
         "friday": shift_definitions["morning"],
         "saturday": shift_definitions["rest"],
         "sunday": shift_definitions["rest"]},
        {"employee_id": employee_ids["王五"], "week_start": monday.isoformat(),
         "monday": shift_definitions["afternoon"],
         "tuesday": shift_definitions["afternoon"],
         "wednesday": shift_definitions["afternoon"],
         "thursday": shift_definitions["afternoon"],
         "friday": shift_definitions["afternoon"],
         "saturday": shift_definitions["rest"],
         "sunday": shift_definitions["rest"]},
        {"employee_id": employee_ids["赵六"], "week_start": monday.isoformat(),
         "monday": shift_definitions["afternoon"],
         "tuesday": shift_definitions["afternoon"],
         "wednesday": shift_definitions["afternoon"],
         "thursday": shift_definitions["afternoon"],
         "friday": shift_definitions["afternoon"],
         "saturday": shift_definitions["rest"],
         "sunday": shift_definitions["rest"]},
        {"employee_id": employee_ids["钱七"], "week_start": monday.isoformat(),
         "monday": shift_definitions["night"],
         "tuesday": shift_definitions["night"],
         "wednesday": shift_definitions["night"],
         "thursday": shift_definitions["night"],
         "friday": shift_definitions["night"],
         "saturday": shift_definitions["rest"],
         "sunday": shift_definitions["rest"]},
    ]
    
    schedule_ids = []
    for s in schedules:
        r = requests.post(f"{BASE_URL}/schedules/", json=s)
        if r.status_code == 201:
            schedule_ids.append(r.json()["id"])
            print(f"排班计划创建成功")
        else:
            print_response("排班计划创建失败", r)
    
    return schedule_ids

def test_1_basic_staffing_constraint(start_time):
    print("\n=== 测试1: 基本人员约束排产 ===")
    
    order_data = {
        "order_no": "STAFF-TEST-001",
        "product_name": "人员测试产品A",
        "expected_start_time": start_time.isoformat(),
        "deadline": (start_time + timedelta(hours=24)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建工单 STAFF-TEST-001", r)
    
    if r.status_code == 201:
        result = r.json()
        order_id = result["order_id"]
        if result.get("schedule_result", {}).get("success"):
            print("\n✓ 工单排产成功")
            entries = result.get("schedule_result", {}).get("schedule_entries", [])
            for entry in entries:
                operator = entry.get("operator_id")
                operator_name = entry.get("operator_name", "未分配")
                print(f"  工序 {entry['step_name']}: {entry['start_time']} - {entry['end_time']}, "
                      f"设备: {entry['device_name']}, 操作人员: {operator_name}")
        else:
            error_msg = result.get("schedule_result", {}).get("message", "未知错误")
            print(f"\n✗ 工单排产失败: {error_msg}")
        return order_id
    return None

def test_2_night_shift_shortage(start_time):
    print("\n=== 测试2: 夜班人员不足场景 ===")
    
    night_start = datetime(start_time.year, start_time.month, start_time.day, 2, 0, 0)
    
    order_data = {
        "order_no": "STAFF-TEST-002",
        "product_name": "人员测试产品A",
        "expected_start_time": night_start.isoformat(),
        "deadline": (night_start + timedelta(hours=12)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r = requests.post(f"{BASE_URL}/orders/", json=order_data)
    print_response("创建夜班工单 STAFF-TEST-002", r)
    
    if r.status_code == 201:
        result = r.json()
        schedule_result = result.get("schedule_result", {})
        if not schedule_result.get("success"):
            error_msg = schedule_result.get("message", "")
            if "人员" in error_msg or "技能" in error_msg:
                print(f"\n✓ 正确检测到夜班人员不足: {error_msg}")
            else:
                print(f"\n? 排产失败但错误信息不符合预期: {error_msg}")
        else:
            print(f"\n? 夜班排产成功（可能有足够的夜班人员）")
            entries = schedule_result.get("schedule_entries", [])
            for entry in entries:
                print(f"  工序 {entry['step_name']}: {entry['start_time']} - {entry['end_time']}, "
                      f"操作人员: {entry.get('operator_name', '未分配')}")
        return result.get("order_id")
    return None

def test_3_priority_based_assignment(start_time):
    print("\n=== 测试3: 基于优先级的人员分配 ===")
    
    order_ids = []
    
    for i in range(1, 4):
        priority = 6 if i == 2 else 3
        order_data = {
            "order_no": f"STAFF-TEST-01{i}",
            "product_name": "人员测试产品B",
            "expected_start_time": start_time.isoformat(),
            "deadline": (start_time + timedelta(hours=12)).isoformat(),
            "total_quantity": 1,
            "priority": priority
        }
        
        r = requests.post(f"{BASE_URL}/orders/", json=order_data)
        if r.status_code == 201:
            order_ids.append(r.json()["order_id"])
            result = r.json()
            schedule_result = result.get("schedule_result", {})
            if schedule_result.get("success"):
                entries = schedule_result.get("schedule_entries", [])
                first_entry = entries[0] if entries else None
                operator = first_entry.get("operator_name", "未分配") if first_entry else "未分配"
                status = "✓ 成功" if schedule_result.get("success") else "✗ 失败"
                print(f"  工单 STAFF-TEST-01{i} (优先级{priority}): {status}, 操作人员: {operator}")
            else:
                msg = schedule_result.get("message", "")
                print(f"  工单 STAFF-TEST-01{i} (优先级{priority}): ✗ 排产失败 - {msg}")
    
    print("\n优先级为6的工单应该优先获得操作人员分配")
    return order_ids

def test_4_query_interfaces(employee_ids, team_ids, device_ids, start_time):
    print("\n=== 测试4: 查询接口 ===")
    
    print("\n4.1 按人员查未来7天排班和已分配工序时间线")
    for emp_name, emp_id in employee_ids.items():
        r = requests.get(f"{BASE_URL}/employees/{emp_id}/timeline")
        if r.status_code == 200:
            data = r.json()
            print(f"\n  {emp_name} (ID: {emp_id}):")
            print(f"    排班条目: {len(data.get('schedule_entries', []))} 条")
            print(f"    工序分配: {len(data.get('assigned_entries', []))} 条")
    
    print("\n4.2 按班组查当日在岗人数和技能覆盖情况")
    for team_name, team_id in team_ids.items():
        r = requests.get(f"{BASE_URL}/teams/{team_id}/daily-summary")
        if r.status_code == 200:
            data = r.json()
            print(f"\n  {team_name} (ID: {team_id}):")
            print(f"    在岗人数: {data.get('on_duty_count', 0)}")
            print(f"    技能覆盖: {data.get('skill_coverage', [])}")
    
    print("\n4.3 按设备查某时段是否有人可操作")
    check_time = start_time + timedelta(hours=2)
    for dev_name, dev_id in device_ids.items():
        r = requests.get(f"{BASE_URL}/device-check/{dev_id}", 
                        params={"check_time": check_time.isoformat()})
        if r.status_code == 200:
            data = r.json()
            can_operate = data.get("has_available_staff", False)
            available_count = data.get("available_count", 0)
            print(f"  {dev_name} (ID: {dev_id}): {'可操作' if can_operate else '不可操作'}, "
                  f"可用人员: {available_count} 人")

def test_5_insertion_with_staffing(start_time):
    print("\n=== 测试5: 插单时的人员约束 ===")
    
    order_data_1 = {
        "order_no": "STAFF-TEST-020",
        "product_name": "人员测试产品B",
        "expected_start_time": start_time.isoformat(),
        "deadline": (start_time + timedelta(hours=12)).isoformat(),
        "total_quantity": 1,
        "priority": 5
    }
    
    r1 = requests.post(f"{BASE_URL}/orders/", json=order_data_1)
    if r1.status_code == 201:
        order1_id = r1.json()["order_id"]
        print("低优先级工单创建成功")
    
    order_data_high = {
        "order_no": "STAFF-TEST-021",
        "product_name": "人员测试产品B",
        "expected_start_time": start_time.isoformat(),
        "deadline": (start_time + timedelta(hours=8)).isoformat(),
        "total_quantity": 1,
        "priority": 3
    }
    
    r2 = requests.post(f"{BASE_URL}/orders/", json=order_data_high)
    if r2.status_code == 201:
        print("高优先级工单创建成功，准备插单")
        
        insertion_data = {
            "order_id": r2.json()["order_id"],
            "new_priority": 8,
            "operator": "测试员",
            "reason": "紧急订单插入"
        }
        
        r3 = requests.post(f"{BASE_URL}/insertion/orders", json=insertion_data)
        print_response("插单请求", r3)
        
        if r3.status_code == 200:
            result = r3.json()
            if result.get("success"):
                print(f"✓ 插单成功，受影响工单: {len(result.get('affected_orders', []))} 个")
            else:
                print(f"✗ 插单失败: {result.get('message')}")

def main():
    print("人员排班与技能约束模块测试")
    print("="*60)
    
    start_time = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    if start_time < datetime.now():
        start_time += timedelta(days=1)
    
    print(f"测试开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    device_ids, skill_ids, team_ids, employee_ids = setup_test_environment()
    
    create_shift_schedules(employee_ids, start_time)
    
    order_id_1 = test_1_basic_staffing_constraint(start_time)
    
    order_id_2 = test_2_night_shift_shortage(start_time)
    
    test_3_priority_based_assignment(start_time)
    
    test_4_query_interfaces(employee_ids, team_ids, device_ids, start_time)
    
    test_5_insertion_with_staffing(start_time)
    
    print("\n" + "="*60)
    print("测试完成！")
    print("="*60)

if __name__ == "__main__":
    main()
