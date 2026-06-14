import unittest
from datetime import datetime, timedelta, time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Device, ProcessRoute, ProcessStep, WorkOrder, Material,
    OutsourcingFactory, OutsourcingCapability, StepOutsourcingConfig,
    OutsourcingScheduleEntry, ProcessStep as ProcessStepModel
)
from app.schemas import (
    OutsourcingFactoryCreate, OutsourcingCapability as OutsourcingCapabilitySchema,
    StepOutsourcingConfig as StepOutsourcingConfigSchema
)
from app.outsourcing_service import (
    schedule_outsourcing_step,
    get_order_outsourcing_status,
    get_factory_load,
    detect_outsourcing_bottlenecks,
    create_outsourcing_schedule_entries,
    delete_outsourcing_entries_for_order,
    calculate_outsourcing_process_duration,
    count_concurrent_at_time
)
from app.scheduler import schedule_order, reschedule_unlocked_orders

SQLALCHEMY_DATABASE_URL = "sqlite:///./test_outsourcing.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def setup_test_data(db):
    device_types = ["CNC", "Grinder", "QA", "Packaging"]
    for i, dt in enumerate(device_types):
        for j in range(2):
            device = Device(
                name=f"{dt}-{j+1:02d}",
                device_type=dt,
                daily_start="08:00",
                daily_end="20:00",
                max_batch_size=100
            )
            db.add(device)

    mat1 = Material(name="钢材A", total_quantity=1000, unit="pcs")
    mat2 = Material(name="润滑剂", total_quantity=500, unit="L")
    db.add(mat1)
    db.add(mat2)
    db.flush()

    factory1 = OutsourcingFactory(
        name="外协精工厂一",
        code="OUT001",
        contact_person="张经理",
        contact_phone="13800000001",
        address="某市A区1号",
        daily_start="08:30",
        daily_end="18:30",
        max_concurrent_jobs=3,
        transport_to_minutes=90,
        transport_back_minutes=90,
        waiting_before_process_minutes=30,
        is_active=True,
        description="专业精密加工外协厂"
    )
    factory2 = OutsourcingFactory(
        name="外协表面处理厂",
        code="OUT002",
        contact_person="李经理",
        contact_phone="13800000002",
        address="某市B区2号",
        daily_start="09:00",
        daily_end="21:00",
        max_concurrent_jobs=2,
        transport_to_minutes=60,
        transport_back_minutes=60,
        waiting_before_process_minutes=20,
        is_active=True,
        description="表面处理专业厂"
    )
    db.add(factory1)
    db.add(factory2)
    db.flush()

    cap1 = OutsourcingCapability(
        factory_id=factory1.id,
        process_type="precision_machining",
        base_duration_minutes=60,
        duration_per_unit_minutes=5,
        efficiency_factor=95,
        min_batch_quantity=10,
        max_batch_quantity=200,
        quality_grade="A",
        notes="适合高精度要求加工"
    )
    cap2 = OutsourcingCapability(
        factory_id=factory2.id,
        process_type="anodizing",
        base_duration_minutes=120,
        duration_per_unit_minutes=2,
        efficiency_factor=90,
        min_batch_quantity=5,
        max_batch_quantity=500,
        quality_grade="A",
        notes="阳极氧化处理"
    )
    db.add(cap1)
    db.add(cap2)
    db.flush()

    route = ProcessRoute(
        product_name="外协测试产品",
        product_family_id=None
    )
    db.add(route)
    db.flush()

    step1 = ProcessStepModel(
        route_id=route.id,
        step_order=1,
        step_name="粗加工",
        device_type="CNC",
        duration_minutes=45,
        min_gap_after=5,
        is_outsource=False
    )
    db.add(step1)
    db.flush()

    step2 = ProcessStepModel(
        route_id=route.id,
        step_order=2,
        step_name="精密加工(外协)",
        device_type="OUTSOURCE",
        duration_minutes=60,
        min_gap_after=10,
        is_outsource=True,
        outsource_process_type="precision_machining"
    )
    db.add(step2)
    db.flush()

    osc2 = StepOutsourcingConfig(
        step_id=step2.id,
        factory_id=factory1.id,
        priority=1,
        is_preferred=True
    )
    db.add(osc2)

    step3 = ProcessStepModel(
        route_id=route.id,
        step_order=3,
        step_name="打磨",
        device_type="Grinder",
        duration_minutes=30,
        min_gap_after=5,
        is_outsource=False
    )
    db.add(step3)
    db.flush()

    step4 = ProcessStepModel(
        route_id=route.id,
        step_order=4,
        step_name="阳极氧化(外协)",
        device_type="OUTSOURCE",
        duration_minutes=120,
        min_gap_after=15,
        is_outsource=True,
        outsource_process_type="anodizing"
    )
    db.add(step4)
    db.flush()

    osc4 = StepOutsourcingConfig(
        step_id=step4.id,
        factory_id=factory2.id,
        priority=1,
        is_preferred=True
    )
    db.add(osc4)

    step5 = ProcessStepModel(
        route_id=route.id,
        step_order=5,
        step_name="质检",
        device_type="QA",
        duration_minutes=20,
        min_gap_after=0,
        is_outsource=False
    )
    db.add(step5)
    db.flush()

    step6 = ProcessStepModel(
        route_id=route.id,
        step_order=6,
        step_name="包装",
        device_type="Packaging",
        duration_minutes=15,
        min_gap_after=0,
        is_outsource=False
    )
    db.add(step6)

    db.commit()
    return {
        "factory1_id": factory1.id,
        "factory2_id": factory2.id,
        "mat_ids": (mat1.id, mat2.id)
    }


class TestOutsourcingModule(unittest.TestCase):

    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.data = setup_test_data(self.db)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)

    def test_01_outsourcing_duration_calculation(self):
        print("\n=== Test 1: 外协工序时长计算 ===")
        factory = self.db.query(OutsourcingFactory).filter(
            OutsourcingFactory.code == "OUT001"
        ).first()
        self.assertIsNotNone(factory)

        capability = factory.capabilities[0]
        duration = calculate_outsourcing_process_duration(factory, capability, 50)
        print(f"  外协工序时长 (数量50): {duration}分钟")
        self.assertGreater(duration, 0)

        expected = capability.base_duration_minutes + (50 * capability.duration_per_unit_minutes)
        expected_adjusted = int(expected * 100 / capability.efficiency_factor)
        self.assertEqual(duration, expected_adjusted)

    def test_02_single_outsourcing_step_scheduling(self):
        print("\n=== Test 2: 单个外协工序排程 ===")
        factory = self.db.query(OutsourcingFactory).filter(
            OutsourcingFactory.code == "OUT001"
        ).first()
        step = self.db.query(ProcessStepModel).filter(
            ProcessStepModel.step_name == "精密加工(外协)"
        ).first()
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order = WorkOrder(
            order_no="WO-TEST-001",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 10, 8, 0, 0),
            deadline=datetime(2025, 1, 20, 20, 0, 0),
            total_quantity=100
        )
        self.db.add(order)
        self.db.flush()

        earliest_start = datetime(2025, 1, 10, 12, 0, 0)
        success, nodes, selected_factory, bn_type, bn_msg = schedule_outsourcing_step(
            self.db, order, None, step,
            quantity=100,
            earliest_start=earliest_start,
            deadline=order.deadline,
            exclude_order_id=order.id
        )

        self.assertTrue(success, f"排程失败: {bn_msg}")
        self.assertIsNotNone(nodes)
        self.assertEqual(len(nodes), 5, "应有5个节点（出厂等待、去程、加工、回程、回厂待工）")
        self.assertIsNotNone(selected_factory)

        print(f"  选中外协厂: {selected_factory.name}")
        for node in nodes:
            print(f"  {node['node_type']}: {node['start_time']} -> {node['end_time']} "
                  f"({node['description']})")

        node_types = [n["node_type"] for n in nodes]
        self.assertIn("waiting_to_ship", node_types)
        self.assertIn("in_transit_to", node_types)
        self.assertIn("outsourcing_process", node_types)
        self.assertIn("in_transit_back", node_types)
        self.assertIn("returned_waiting", node_types)

        for i in range(1, len(nodes)):
            self.assertGreaterEqual(
                nodes[i]["start_time"],
                nodes[i-1]["end_time"],
                f"节点 {i} 开始时间早于节点 {i-1} 结束时间"
            )

    def test_03_full_order_scheduling_with_outsource(self):
        print("\n=== Test 3: 完整工单排产（含外协工序） ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order = WorkOrder(
            order_no="WO-TEST-002",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 13, 8, 0, 0),
            deadline=datetime(2025, 1, 25, 20, 0, 0),
            total_quantity=50,
            status="pending"
        )
        self.db.add(order)
        self.db.flush()

        result = schedule_order(self.db, order)

        self.assertTrue(result["success"], f"排产失败: {result.get('message')}")
        self.assertEqual(order.status, "scheduled")

        schedule_entries = order.schedule_entries
        outsourcing_entries = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order.id
        ).all()

        print(f"  厂内工序排产条目数: {len(schedule_entries)}")
        print(f"  外协排产条目数: {len(outsourcing_entries)}")

        outsource_steps_count = sum(1 for s in route.steps if s.is_outsource)
        expected_outsourcing_entries = outsource_steps_count * 5
        self.assertEqual(
            len(outsourcing_entries), expected_outsourcing_entries,
            f"外协条目数应为 {expected_outsourcing_entries}"
        )

        for step in sorted(route.steps, key=lambda s: s.step_order):
            if step.is_outsource:
                step_outsourcing = [
                    e for e in outsourcing_entries if e.step_order == step.step_order
                ]
                self.assertEqual(len(step_outsourcing), 5)
                for se in step_outsourcing:
                    print(f"    工序 {step.step_order} ({step.step_name}) - "
                          f"{se.node_type}: {se.start_time} -> {se.end_time}")
            else:
                step_inhouse = [
                    e for e in schedule_entries if e.step_order == step.step_order
                ]
                self.assertGreaterEqual(len(step_inhouse), 1)
                for se in step_inhouse:
                    print(f"    工序 {step.step_order} ({step.step_name}): "
                          f"{se.start_time} -> {se.end_time} (设备: {se.device.name})")

        all_entries = list(schedule_entries) + list(outsourcing_entries)
        all_entries.sort(key=lambda e: (e.step_order, getattr(e, 'node_sequence', 0)))

        prev_end = None
        prev_step_order = None
        for e in all_entries:
            step_order = e.step_order
            start_time = e.start_time
            end_time = e.end_time
            if prev_end is not None and prev_step_order != step_order:
                self.assertGreaterEqual(
                    start_time, prev_end,
                    f"工序 {step_order} 开始时间早于上一工序结束时间"
                )
            if prev_step_order == step_order:
                pass
            else:
                prev_end = end_time
            prev_step_order = step_order

    def test_04_order_outsourcing_status_tracking(self):
        print("\n=== Test 4: 工单外协状态追踪 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order = WorkOrder(
            order_no="WO-TEST-003",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 14, 8, 0, 0),
            deadline=datetime(2025, 1, 28, 20, 0, 0),
            total_quantity=80,
            status="pending"
        )
        self.db.add(order)
        self.db.flush()

        result = schedule_order(self.db, order)
        self.assertTrue(result["success"])

        status = get_order_outsourcing_status(self.db, order.id)
        self.assertIsNotNone(status)

        print(f"  工单号: {status['order_no']}")
        print(f"  综合状态: {status['overall_status']}")
        print(f"  当前阶段: {status.get('current_stage', 'N/A')}")
        print(f"  当前描述: {status.get('current_description', 'N/A')}")
        print(f"  外协节点数: {len(status['outsourcing_nodes'])}")

        for node in status["outsourcing_nodes"]:
            print(f"    {node['node_type']} (seq {node['node_sequence']}): "
                  f"{node['start_time']} -> {node['end_time']} - {node['description']}")

        valid_statuses = ["in_factory", "in_house", "in_transit", "outsourcing", "returned_waiting", "completed"]
        self.assertIn(status["overall_status"], valid_statuses)

    def test_05_factory_load_analysis(self):
        print("\n=== Test 5: 外协厂负载分析 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        for i in range(3):
            order = WorkOrder(
                order_no=f"WO-LOAD-{i+1:02d}",
                product_name=route.product_name,
                expected_start_time=datetime(2025, 1, 13 + i, 8, 0, 0),
                deadline=datetime(2025, 2, 5 + i, 20, 0, 0),
                total_quantity=30 + i * 20,
                status="pending"
            )
            self.db.add(order)
            self.db.flush()
            result = schedule_order(self.db, order)
            self.assertTrue(result["success"], f"工单 {order.order_no} 排产失败: {result.get('message')}")

        factory = self.db.query(OutsourcingFactory).filter(
            OutsourcingFactory.code == "OUT001"
        ).first()

        load = get_factory_load(self.db, factory.id, look_ahead_days=7)
        self.assertIsNotNone(load)

        print(f"  外协厂: {load['factory_name']} ({load['factory_code']})")
        print(f"  展望天数: {load['look_ahead_days']} 天")
        print(f"  在制加工数: {load['in_process_count']}")
        print(f"  在途去程: {load['in_transit_to_count']}")
        print(f"  在途回程: {load['in_transit_back_count']}")
        print(f"  回厂待工: {load['returned_waiting_count']}")
        print(f"  排队等待: {load['queued_count']}")

        for day in load["days"]:
            print(f"\n    日期 {day['date']}:")
            print(f"      已排程分钟: {day['total_scheduled_minutes']}")
            print(f"      可用分钟: {day['available_minutes']}")
            print(f"      利用率: {day['utilization_rate']:.1%}")
            print(f"      并发峰值: {day['concurrent_peak']}/{day['max_concurrent']}")
            print(f"      条目数: {len(day['entries'])}")
            for entry in day["entries"][:2]:
                print(f"        - {entry['step_name']} ({entry['node_type']}): "
                      f"{entry['batch_no']}")

    def test_06_outsourcing_bottleneck_detection(self):
        print("\n=== Test 6: 外协瓶颈检测 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        factory_small = OutsourcingFactory(
            name="小产能外协厂",
            code="OUT003",
            daily_start="09:00",
            daily_end="17:00",
            max_concurrent_jobs=1,
            transport_to_minutes=30,
            transport_back_minutes=30,
            waiting_before_process_minutes=10,
            is_active=True
        )
        self.db.add(factory_small)
        self.db.flush()

        cap_small = OutsourcingCapability(
            factory_id=factory_small.id,
            process_type="precision_machining",
            base_duration_minutes=480,
            duration_per_unit_minutes=10,
            efficiency_factor=100,
            min_batch_quantity=1
        )
        self.db.add(cap_small)
        self.db.commit()

        outsource_step = self.db.query(ProcessStepModel).filter(
            ProcessStepModel.step_name == "精密加工(外协)"
        ).first()
        osc_small = StepOutsourcingConfig(
            step_id=outsource_step.id,
            factory_id=factory_small.id,
            priority=2,
            is_preferred=False
        )
        self.db.add(osc_small)
        self.db.commit()

        bottlenecks = detect_outsourcing_bottlenecks(self.db, look_ahead_days=14)
        self.assertIsInstance(bottlenecks, list)

        print(f"  检测到瓶颈数: {len(bottlenecks)}")
        for b in bottlenecks:
            print(f"    [{b['bottleneck_type']}] {b['factory_name']}: {b['description']}")

        valid_types = ["outsourcing_concurrent", "outsourcing_timewindow", "outsourcing_capability"]
        for b in bottlenecks:
            self.assertIn(b["bottleneck_type"], valid_types)

    def test_07_concurrent_limit_enforcement(self):
        print("\n=== Test 7: 并发上限验证 ===")
        factory_small = OutsourcingFactory(
            name="并发测试厂",
            code="OUT004",
            daily_start="08:00",
            daily_end="20:00",
            max_concurrent_jobs=1,
            transport_to_minutes=0,
            transport_back_minutes=0,
            waiting_before_process_minutes=0,
            is_active=True
        )
        self.db.add(factory_small)
        self.db.flush()

        cap_small = OutsourcingCapability(
            factory_id=factory_small.id,
            process_type="test_process",
            base_duration_minutes=120,
            duration_per_unit_minutes=0,
            efficiency_factor=100,
            min_batch_quantity=1
        )
        self.db.add(cap_small)
        self.db.flush()

        route = ProcessRoute(product_name="并发测试产品")
        self.db.add(route)
        self.db.flush()

        step1 = ProcessStepModel(
            route_id=route.id, step_order=1, step_name="厂内前工序",
            device_type="CNC", duration_minutes=30, is_outsource=False
        )
        step2 = ProcessStepModel(
            route_id=route.id, step_order=2, step_name="外协工序",
            device_type="OUTSOURCE", duration_minutes=120,
            is_outsource=True, outsource_process_type="test_process"
        )
        step3 = ProcessStepModel(
            route_id=route.id, step_order=3, step_name="厂内后工序",
            device_type="QA", duration_minutes=20, is_outsource=False
        )
        self.db.add_all([step1, step2, step3])
        self.db.flush()

        osc = StepOutsourcingConfig(
            step_id=step2.id, factory_id=factory_small.id,
            priority=1, is_preferred=True
        )
        self.db.add(osc)
        self.db.commit()

        for i in range(3):
            order = WorkOrder(
                order_no=f"WO-CONC-{i+1:02d}",
                product_name="并发测试产品",
                expected_start_time=datetime(2025, 1, 15, 8, 0, 0),
                deadline=datetime(2025, 1, 25, 20, 0, 0),
                total_quantity=10,
                status="pending"
            )
            self.db.add(order)
            self.db.flush()
            result = schedule_order(self.db, order)
            if result["success"]:
                print(f"  工单 {order.order_no}: 排产成功")
            else:
                print(f"  工单 {order.order_no}: 排产失败 - {result.get('bottleneck_type')}")

        all_outsourcing = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.factory_id == factory_small.id,
            OutsourcingScheduleEntry.node_type == "outsourcing_process",
            OutsourcingScheduleEntry.scenario_id.is_(None)
        ).all()

        times_to_check = []
        slots = [(e.start_time, e.end_time) for e in all_outsourcing]
        for e in all_outsourcing:
            start = e.start_time
            for offset_min in range(0, 120, 10):
                check_time = start + timedelta(minutes=offset_min)
                times_to_check.append(check_time)

        for t in times_to_check:
            count = count_concurrent_at_time(slots, t)
            self.assertLessEqual(
                count, factory_small.max_concurrent_jobs,
                f"时间 {t} 并发数 {count} 超过上限 {factory_small.max_concurrent_jobs}"
            )

        print(f"  验证完成: 所有时间点并发数都不超过上限 {factory_small.max_concurrent_jobs}")

    def test_08_order_cancellation_cleanup(self):
        print("\n=== Test 8: 工单撤销清理 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order = WorkOrder(
            order_no="WO-CANCEL-001",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 16, 8, 0, 0),
            deadline=datetime(2025, 1, 30, 20, 0, 0),
            total_quantity=60,
            status="pending"
        )
        self.db.add(order)
        self.db.flush()

        result = schedule_order(self.db, order)
        self.assertTrue(result["success"])

        outsourcing_before = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order.id
        ).count()
        print(f"  撤销前外协条目数: {outsourcing_before}")
        self.assertGreater(outsourcing_before, 0)

        deleted = delete_outsourcing_entries_for_order(self.db, order.id)
        self.db.commit()
        print(f"  已删除外协条目数: {deleted}")

        outsourcing_after = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order.id
        ).count()
        print(f"  撤销后外协条目数: {outsourcing_after}")
        self.assertEqual(outsourcing_after, 0)

    def test_09_reschedule_updates_outsourcing(self):
        print("\n=== Test 9: 重排更新外协节点 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order1 = WorkOrder(
            order_no="WO-RESCH-001",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 17, 8, 0, 0),
            deadline=datetime(2025, 2, 1, 20, 0, 0),
            total_quantity=40,
            status="pending"
        )
        self.db.add(order1)
        self.db.flush()
        self.assertTrue(schedule_order(self.db, order1)["success"])

        entries_before = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order1.id,
            OutsourcingScheduleEntry.node_type == "outsourcing_process"
        ).all()

        times_before = [(e.start_time, e.end_time) for e in sorted(entries_before, key=lambda x: x.step_order)]
        print(f"  重排前外协加工时间: {times_before}")

        order1.is_locked = False
        order2 = WorkOrder(
            order_no="WO-RESCH-002",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 17, 8, 0, 0),
            deadline=datetime(2025, 2, 1, 20, 0, 0),
            total_quantity=25,
            status="pending",
            is_locked=True
        )
        self.db.add(order2)
        self.db.flush()
        self.assertTrue(schedule_order(self.db, order2)["success"])
        reschedule_unlocked_orders(self.db, exclude_order_id=order2.id)

        entries_after = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order1.id,
            OutsourcingScheduleEntry.node_type == "outsourcing_process"
        ).all()

        times_after = [(e.start_time, e.end_time) for e in sorted(entries_after, key=lambda x: x.step_order)]
        print(f"  重排后外协加工时间: {times_after}")

        self.assertEqual(len(times_before), len(times_after))
        self.assertEqual(len(times_after), 2, "应有2个外协加工步骤")

    def test_10_no_equipment_conflict_during_outsource(self):
        print("\n=== Test 10: 外协期间不占用厂内设备 ===")
        route = self.db.query(ProcessRoute).filter(
            ProcessRoute.product_name == "外协测试产品"
        ).first()

        order = WorkOrder(
            order_no="WO-CONFLICT-001",
            product_name=route.product_name,
            expected_start_time=datetime(2025, 1, 20, 8, 0, 0),
            deadline=datetime(2025, 2, 10, 20, 0, 0),
            total_quantity=70,
            status="pending"
        )
        self.db.add(order)
        self.db.flush()
        result = schedule_order(self.db, order)
        self.assertTrue(result["success"])

        inhouse_entries = sorted(order.schedule_entries, key=lambda e: (e.sub_batch_id, e.step_order))
        outsourcing_entries = self.db.query(OutsourcingScheduleEntry).filter(
            OutsourcingScheduleEntry.order_id == order.id,
            OutsourcingScheduleEntry.scenario_id.is_(None)
        ).order_by(
            OutsourcingScheduleEntry.sub_batch_id,
            OutsourcingScheduleEntry.step_order,
            OutsourcingScheduleEntry.node_sequence
        ).all()

        outsource_process_nodes = [
            e for e in outsourcing_entries
            if e.node_type in ["waiting_to_ship", "in_transit_to", "outsourcing_process", "in_transit_back", "returned_waiting"]
        ]

        print(f"  厂内工序排产条目: {len(inhouse_entries)}")
        print(f"  外协流程节点: {len(outsource_process_nodes)}")

        sub_batches = set(e.sub_batch_id for e in inhouse_entries)
        for sb_id in sub_batches:
            sb_inhouse = sorted(
                [e for e in inhouse_entries if e.sub_batch_id == sb_id],
                key=lambda e: e.step_order
            )
            sb_outsourcing = sorted(
                [e for e in outsourcing_entries if e.sub_batch_id == sb_id],
                key=lambda e: (e.step_order, e.node_sequence)
            )

            outsource_step_orders = set(e.step_order for e in sb_outsourcing)
            for osc_step in sorted(outsource_step_orders):
                osc_nodes = [n for n in sb_outsourcing if n.step_order == osc_step]
                if not osc_nodes:
                    continue

                osc_start = min(n.start_time for n in osc_nodes)
                osc_end = max(n.end_time for n in osc_nodes)
                print(f"    子批次 {sb_id} 外协工序 {osc_step}: "
                      f"{osc_start} -> {osc_end}")

                conflicting = [
                    e for e in sb_inhouse
                    if e.step_order > 0
                    and e.start_time < osc_end
                    and e.end_time > osc_start
                ]
                if conflicting:
                    for c in conflicting:
                        if c.step_order < osc_step:
                            self.assertLessEqual(
                                c.end_time, osc_start,
                                f"子批次 {sb_id}: 厂内前工序 {c.step_order} 与外协工序 {osc_step} 时间重叠"
                            )
                        elif c.step_order > osc_step:
                            self.assertGreaterEqual(
                                c.start_time, osc_end,
                                f"子批次 {sb_id}: 厂内后工序 {c.step_order} 与外协工序 {osc_step} 时间重叠"
                            )

        print("  验证完成: 外协期间未与厂内设备占用时间冲突")


if __name__ == "__main__":
    unittest.main(verbosity=2)
