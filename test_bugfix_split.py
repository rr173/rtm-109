import requests
import json
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api"

def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def assert_eq(actual, expected, msg=""):
    ok = actual == expected
    mark = "✓" if ok else "✗"
    print(f"  {mark} {msg}: expected={expected}, actual={actual}")
    if not ok:
        raise AssertionError(f"{msg}: expected={expected}, actual={actual}")

def assert_true(cond, msg=""):
    mark = "✓" if cond else "✗"
    print(f"  {mark} {msg}")
    if not cond:
        raise AssertionError(msg)

def setup_single_device_scenario():
    print_section("Setup: 只有一台CNC设备的场景 + 物料")
    
    for d in ["CNC-SOLO", "抛光-SOLO", "清洗-SOLO", "检验-SOLO"]:
        requests.delete(f"{BASE_URL}/devices/{d}")
    
    resp = requests.post(f"{BASE_URL}/devices/", json={
        "name": "CNC-SOLO", "device_type": "CNC-SOLO",
        "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 2
    })
    print(f"  CNC设备(max_batch=2): {resp.status_code}")
    
    resp = requests.post(f"{BASE_URL}/devices/", json={
        "name": "抛光-SOLO", "device_type": "抛光-SOLO",
        "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10
    })
    print(f"  抛光设备(max_batch=10): {resp.status_code}")
    
    resp = requests.post(f"{BASE_URL}/devices/", json={
        "name": "清洗-SOLO", "device_type": "清洗-SOLO",
        "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10
    })
    print(f"  清洗设备(max_batch=10): {resp.status_code}")
    
    resp = requests.post(f"{BASE_URL}/devices/", json={
        "name": "检验-SOLO", "device_type": "检验-SOLO",
        "daily_start": "08:00", "daily_end": "20:00", "max_batch_size": 10
    })
    print(f"  检验设备(max_batch=10): {resp.status_code}")
    
    resp = requests.post(f"{BASE_URL}/inventory/materials/", json={
        "name": "螺丝", "unit": "个", "initial_quantity": 100
    })
    screw_id = None
    if resp.status_code == 201:
        screw_id = resp.json()["id"]
        print(f"  创建物料螺丝(id={screw_id}): 100个")
    else:
        resp = requests.get(f"{BASE_URL}/inventory/materials/")
        for m in resp.json():
            if m["name"] == "螺丝":
                screw_id = m["id"]
                break
    
    route_data = {
        "product_name": "单设备测试产品",
        "steps": [
            {"step_order": 1, "step_name": "CNC加工", "device_type": "CNC-SOLO",
             "duration_minutes": 60, "min_gap_after": 10,
             "material_requirements": [{"material_id": screw_id, "quantity": 2}] if screw_id else []},
            {"step_order": 2, "step_name": "表面抛光", "device_type": "抛光-SOLO",
             "duration_minutes": 30, "min_gap_after": 5},
            {"step_order": 3, "step_name": "清洗烘干", "device_type": "清洗-SOLO",
             "duration_minutes": 20, "min_gap_after": 5},
            {"step_order": 4, "step_name": "质量检验", "device_type": "检验-SOLO",
             "duration_minutes": 15, "min_gap_after": 0},
        ]
    }
    resp = requests.post(f"{BASE_URL}/routes/", json=route_data)
    print(f"  创建工艺路线: {resp.status_code}")
    return screw_id


def test_bug1_single_device_no_overlap():
    print_section("Bug 1 验证: 单设备时子批次不应在同一时间重叠")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=5)
    
    resp = requests.post(f"{BASE_URL}/orders/", json={
        "order_no": "BUG1-TEST-001",
        "product_name": "单设备测试产品",
        "total_quantity": 5,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    })
    data = resp.json()
    print(f"  创建工单 status={resp.status_code}, success={data.get('success')}")
    print(f"  拆批情况: is_split={data.get('is_split')}, batches={data.get('total_sub_batches')}")
    assert_true(data.get("success"), "工单排产应该成功")
    assert_true(data.get("is_split"), "工单应该被拆批")
    assert_eq(data.get("total_sub_batches"), 3, "拆批数量")  # 5/2=ceil=3批
    
    sub_batches = data.get("sub_batches", [])
    assert_eq(len(sub_batches), 3, "返回子批次数量")
    
    cnc_times = []
    for sb in sub_batches:
        print(f"\n  子批次 {sb['batch_no']} (qty={sb['quantity']}):")
        for se in sb["schedule_entries"]:
            print(f"    {se['step_name']}: {se['start_time']} ~ {se['end_time']} @ 设备{se['device_id']} ({se.get('device_name','')})")
            if se["step_name"] == "CNC加工":
                cnc_times.append((se["device_id"], se["start_time"], se["end_time"], sb["batch_no"]))
    
    print(f"\n  CNC工序时间段汇总:")
    for dev_id, s, e, bn in cnc_times:
        print(f"    {bn}: {s} ~ {e} on dev {dev_id}")
    
    for i in range(len(cnc_times)):
        for j in range(i+1, len(cnc_times)):
            _, s1, e1, bn1 = cnc_times[i]
            _, s2, e2, bn2 = cnc_times[j]
            t1 = datetime.fromisoformat(s1.replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(e1.replace("Z", "+00:00"))
            t3 = datetime.fromisoformat(s2.replace("Z", "+00:00"))
            t4 = datetime.fromisoformat(e2.replace("Z", "+00:00"))
            overlap = t1 < t4 and t3 < t2
            print(f"  {'✗' if overlap else '✓'} {bn1} vs {bn2} overlap={overlap}")
            assert_true(not overlap, f"Bug1: {bn1} 和 {bn2} 在单台CNC设备上时间不应重叠!")
    
    return data["order_id"]


def test_bug2_reschedule_no_duplicate(order_id):
    print_section("Bug 2 验证: 重新排产后子批次数不变、编号不重复")
    
    resp1 = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    batches_before = resp1.json()
    print(f"  重排前子批次数: {len(batches_before)}")
    for sb in batches_before:
        print(f"    {sb['batch_no']}: qty={sb['quantity']}, status={sb['status']}")
    
    before_bnos = [sb["batch_no"] for sb in batches_before]
    
    resp = requests.post(f"{BASE_URL}/orders/{order_id}/reschedule")
    data = resp.json()
    print(f"\n  重排产 status={resp.status_code}, success={data.get('success')}")
    print(f"  is_split={data.get('is_split')}, total_sub_batches={data.get('total_sub_batches')}")
    assert_true(data.get("success"), "重新排产应该成功")
    assert_eq(data.get("total_sub_batches"), 3, "重排产后子批次数仍应为3")
    
    batches_after = data.get("sub_batches", [])
    print(f"\n  重排产后子批次数: {len(batches_after)}")
    after_bnos = [sb["batch_no"] for sb in batches_after]
    for sb in batches_after:
        print(f"    {sb['batch_no']}: qty={sb['quantity']}, entries={len(sb['schedule_entries'])}")
    
    assert_eq(len(batches_after), len(batches_before), "前后子批次数量一致")
    
    for bn in after_bnos:
        cnt = after_bnos.count(bn)
        print(f"  {'✗' if cnt > 1 else '✓'} 编号 {bn} 出现次数={cnt}")
        assert_true(cnt == 1, f"Bug2: 子批次编号 {bn} 重复出现{cnt}次!")
    
    db_check = requests.get(f"{BASE_URL}/orders/{order_id}/sub-batches")
    db_batches = db_check.json()
    print(f"\n  数据库查询子批次数: {len(db_batches)}")
    assert_eq(len(db_batches), 3, "数据库中也应该只有3个批次")
    db_bnos = [sb["batch_no"] for sb in db_batches]
    for bn in db_bnos:
        assert_true(db_bnos.count(bn) == 1, f"Bug2: 数据库中编号 {bn} 也不应重复")


def test_bug3_material_multiplied(screw_id):
    print_section("Bug 3 验证: 拆成多批后物料扣减应倍增")
    
    if not screw_id:
        print("  跳过：无物料ID")
        return
    
    inv_before = requests.get(f"{BASE_URL}/inventory/inventory/all")
    screw_before = None
    for m in inv_before.json():
        if m["id"] == screw_id:
            screw_before = m
            break
    print(f"  领料前库存: total={screw_before['total_quantity']}, locked={screw_before['locked_quantity']}, available={screw_before['available_quantity']}")
    
    tomorrow = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    deadline = tomorrow + timedelta(days=5)
    
    resp = requests.post(f"{BASE_URL}/orders/", json={
        "order_no": "BUG3-TEST-001",
        "product_name": "单设备测试产品",
        "total_quantity": 5,
        "expected_start_time": tomorrow.isoformat(),
        "deadline": deadline.isoformat()
    })
    data = resp.json()
    print(f"  创建工单 success={data.get('success')}, batches={data.get('total_sub_batches')}")
    assert_true(data.get("success"), "工单应排产成功")
    num_batches = data.get("total_sub_batches")
    
    inv_after = requests.get(f"{BASE_URL}/inventory/inventory/all")
    screw_after = None
    for m in inv_after.json():
        if m["id"] == screw_id:
            screw_after = m
            break
    print(f"  领料后库存: total={screw_after['total_quantity']}, locked={screw_after['locked_quantity']}, available={screw_after['available_quantity']}")
    
    locked_diff = screw_after["locked_quantity"] - screw_before["locked_quantity"]
    expected_locked = 2 * num_batches  # 每批要2个螺丝 * N批
    print(f"\n  新增锁定数量: {locked_diff}")
    print(f"  预期锁定数量: 2 * {num_batches} = {expected_locked}")
    assert_eq(locked_diff, expected_locked, "Bug3: 拆批后物料锁扣数量应等于单批需求×子批次数")


def run_all_bugfix_tests():
    print("\n" + "#"*70)
    print("#" + " "*18 + "Bug修复验证测试套件" + " "*27 + "#")
    print("#"*70)
    
    try:
        screw_id = setup_single_device_scenario()
        
        order_id = test_bug1_single_device_no_overlap()
        test_bug2_reschedule_no_duplicate(order_id)
        test_bug3_material_multiplied(screw_id)
        
        print_section("✓✓✓ 全部 Bug 修复验证通过 ✓✓✓")
        
    except Exception as e:
        print(f"\n✗ 测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_all_bugfix_tests()
