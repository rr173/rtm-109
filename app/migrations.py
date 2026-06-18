from sqlalchemy import inspect, text
from app.database import engine


def _remove_unique_constraint_safely(conn, table_name: str, column_name: str):
    try:
        result = conn.execute(text(f"PRAGMA index_list({table_name})"))
        indexes = result.fetchall()
        for idx in indexes:
            idx_name = idx[1]
            is_unique = idx[2] if len(idx) > 2 else 0
            if is_unique:
                cols_result = conn.execute(text(f"PRAGMA index_info({idx_name})"))
                cols = cols_result.fetchall()
                col_names = [c[2] for c in cols]
                if len(col_names) == 1 and col_names[0] == column_name:
                    conn.execute(text(f"DROP INDEX {idx_name}"))
                    conn.commit()
                    print(f"[Migration] Dropped unique index {idx_name} on {table_name}.{column_name}")
                    return True
    except Exception as e:
        print(f"[Migration] Warning: could not check/remove unique constraint: {e}")
    return False


def run_migrations():
    inspector = inspect(engine)
    
    with engine.connect() as conn:
        table_names = inspector.get_table_names()
        if "work_orders" in table_names:
            _remove_unique_constraint_safely(conn, "work_orders", "order_no")
    
    sub_batch_columns = {col["name"] for col in inspector.get_columns("sub_batches")}
    needed_sub_batch_cols = {
        "parent_sub_batch_id": "INTEGER",
        "is_replenishment": "BOOLEAN DEFAULT 0",
        "replenish_level": "INTEGER DEFAULT 0",
        "replenish_from_step": "INTEGER",
        "scenario_id": "INTEGER",
        "source_sub_batch_id": "INTEGER"
    }
    
    with engine.connect() as conn:
        for col_name, col_def in needed_sub_batch_cols.items():
            if col_name not in sub_batch_columns:
                conn.execute(text(f"ALTER TABLE sub_batches ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to sub_batches")
        
        schedule_columns = {col["name"] for col in inspector.get_columns("schedule_entries")}
        needed_schedule_cols = {
            "is_completed": "BOOLEAN DEFAULT 0",
            "actual_completion_time": "DATETIME",
            "migrated_from_device_id": "INTEGER",
            "is_migrated": "BOOLEAN DEFAULT 0",
            "fixture_id": "INTEGER",
            "fixture_turn_over_end_time": "DATETIME",
            "scenario_id": "INTEGER",
            "source_schedule_entry_id": "INTEGER",
            "changeover_start_time": "DATETIME",
            "changeover_end_time": "DATETIME",
            "changeover_minutes": "INTEGER DEFAULT 0",
            "changeover_type": "VARCHAR",
            "prev_product_name": "VARCHAR"
        }
        
        for col_name, col_def in needed_schedule_cols.items():
            if col_name not in schedule_columns:
                conn.execute(text(f"ALTER TABLE schedule_entries ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to schedule_entries")
        
        process_step_columns = {col["name"] for col in inspector.get_columns("process_steps")}
        needed_process_step_cols = {
            "fixture_type_id": "INTEGER",
            "is_outsource": "BOOLEAN DEFAULT 0",
            "outsource_process_type": "VARCHAR"
        }
        
        for col_name, col_def in needed_process_step_cols.items():
            if col_name not in process_step_columns:
                conn.execute(text(f"ALTER TABLE process_steps ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to process_steps")
        
        work_order_columns = {col["name"] for col in inspector.get_columns("work_orders")}
        needed_work_order_cols = {
            "is_blocked": "BOOLEAN DEFAULT 0",
            "blocked_reason": "VARCHAR",
            "scenario_id": "INTEGER",
            "source_order_id": "INTEGER"
        }
        
        for col_name, col_def in needed_work_order_cols.items():
            if col_name not in work_order_columns:
                conn.execute(text(f"ALTER TABLE work_orders ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to work_orders")
        
        conflict_columns = {col["name"] for col in inspector.get_columns("conflict_records")}
        needed_conflict_cols = {
            "scenario_id": "INTEGER"
        }
        for col_name, col_def in needed_conflict_cols.items():
            if col_name not in conflict_columns:
                conn.execute(text(f"ALTER TABLE conflict_records ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to conflict_records")
        
        mat_lock_columns = {col["name"] for col in inspector.get_columns("material_locks")}
        needed_mat_lock_cols = {
            "scenario_id": "INTEGER"
        }
        for col_name, col_def in needed_mat_lock_cols.items():
            if col_name not in mat_lock_columns:
                conn.execute(text(f"ALTER TABLE material_locks ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to material_locks")
        
        device_fault_columns = {col["name"] for col in inspector.get_columns("device_faults")}
        needed_device_fault_cols = {
            "scenario_id": "INTEGER"
        }
        for col_name, col_def in needed_device_fault_cols.items():
            if col_name not in device_fault_columns:
                conn.execute(text(f"ALTER TABLE device_faults ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to device_faults")
        
        step_progress_columns = {col["name"] for col in inspector.get_columns("sub_batch_step_progress")}
        needed_step_progress_cols = {
            "scenario_id": "INTEGER"
        }
        for col_name, col_def in needed_step_progress_cols.items():
            if col_name not in step_progress_columns:
                conn.execute(text(f"ALTER TABLE sub_batch_step_progress ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to sub_batch_step_progress")
        
        table_names = inspector.get_table_names()
        
        if "sub_batch_step_progress" not in table_names:
            conn.execute(text("""
                CREATE TABLE sub_batch_step_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sub_batch_id INTEGER NOT NULL,
                    step_order INTEGER NOT NULL,
                    step_name VARCHAR NOT NULL,
                    step_id INTEGER NOT NULL,
                    is_completed BOOLEAN DEFAULT 0,
                    actual_completion_time DATETIME,
                    good_quantity INTEGER DEFAULT 0,
                    scrap_quantity INTEGER DEFAULT 0,
                    reported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scenario_id INTEGER,
                    FOREIGN KEY(sub_batch_id) REFERENCES sub_batches (id),
                    FOREIGN KEY(step_id) REFERENCES process_steps (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table sub_batch_step_progress")
        
        if "device_faults" not in table_names:
            conn.execute(text("""
                CREATE TABLE device_faults (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    fault_time DATETIME NOT NULL,
                    expected_recovery_time DATETIME NOT NULL,
                    actual_recovery_time DATETIME,
                    status VARCHAR NOT NULL DEFAULT 'active',
                    description VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    resolved_at DATETIME,
                    scenario_id INTEGER,
                    FOREIGN KEY(device_id) REFERENCES devices (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table device_faults")
        
        if "fixture_types" not in table_names:
            conn.execute(text("""
                CREATE TABLE fixture_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL UNIQUE,
                    description VARCHAR,
                    turn_over_minutes INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            print("[Migration] Created table fixture_types")
        
        if "fixtures" not in table_names:
            conn.execute(text("""
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code VARCHAR NOT NULL UNIQUE,
                    fixture_type_id INTEGER NOT NULL,
                    compatible_device_types VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'available',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(fixture_type_id) REFERENCES fixture_types (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table fixtures")
        
        if "scenarios" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenarios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL,
                    description VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'draft',
                    created_by VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    published_at DATETIME,
                    published_by VARCHAR,
                    baseline_hash VARCHAR,
                    baseline_timestamp DATETIME
                )
            """))
            conn.commit()
            print("[Migration] Created table scenarios")
        
        if "scenario_audit_logs" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    action VARCHAR NOT NULL,
                    operator VARCHAR,
                    details VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_audit_logs")
        
        if "scenario_maintenance_overrides" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_maintenance_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    maintenance_plan_id INTEGER,
                    device_id INTEGER NOT NULL,
                    override_type VARCHAR NOT NULL,
                    new_start_time VARCHAR,
                    new_end_time VARCHAR,
                    new_day_of_week INTEGER,
                    description VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
                    FOREIGN KEY(maintenance_plan_id) REFERENCES maintenance_plans (id),
                    FOREIGN KEY(device_id) REFERENCES devices (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_maintenance_overrides")
        
        if "scenario_device_overrides" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_device_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    device_id INTEGER NOT NULL,
                    override_type VARCHAR NOT NULL,
                    effective_from DATETIME,
                    effective_to DATETIME,
                    reason VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
                    FOREIGN KEY(device_id) REFERENCES devices (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_device_overrides")
        
        if "scenario_fixture_overrides" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_fixture_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    fixture_type_id INTEGER,
                    fixture_id INTEGER,
                    override_type VARCHAR NOT NULL,
                    quantity_change INTEGER DEFAULT 0,
                    temp_fixture_code VARCHAR,
                    temp_status VARCHAR,
                    effective_from DATETIME,
                    effective_to DATETIME,
                    reason VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
                    FOREIGN KEY(fixture_type_id) REFERENCES fixture_types (id),
                    FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_fixture_overrides")
        
        if "product_families" not in table_names:
            conn.execute(text("""
                CREATE TABLE product_families (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL UNIQUE,
                    description VARCHAR
                )
            """))
            conn.commit()
            print("[Migration] Created table product_families")
        
        if "changeover_rules" not in table_names:
            conn.execute(text("""
                CREATE TABLE changeover_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_type VARCHAR NOT NULL,
                    device_id INTEGER,
                    from_product_family_id INTEGER,
                    to_product_family_id INTEGER,
                    from_product_name VARCHAR,
                    to_product_name VARCHAR,
                    changeover_minutes INTEGER NOT NULL,
                    changeover_type VARCHAR NOT NULL DEFAULT 'cross_family',
                    description VARCHAR,
                    FOREIGN KEY(device_id) REFERENCES devices (id),
                    FOREIGN KEY(from_product_family_id) REFERENCES product_families (id),
                    FOREIGN KEY(to_product_family_id) REFERENCES product_families (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table changeover_rules")
        
        if "process_routes" in table_names:
            route_columns = {col["name"] for col in inspector.get_columns("process_routes")}
            if "product_family_id" not in route_columns:
                conn.execute(text("ALTER TABLE process_routes ADD COLUMN product_family_id INTEGER"))
                conn.commit()
                print("[Migration] Added column product_family_id to process_routes")
        
        if "changeover_rules" in table_names:
            changeover_columns = {col["name"] for col in inspector.get_columns("changeover_rules")}
            if "description" not in changeover_columns:
                conn.execute(text("ALTER TABLE changeover_rules ADD COLUMN description VARCHAR"))
                conn.commit()
                print("[Migration] Added column description to changeover_rules")
        
        work_order_columns = {col["name"] for col in inspector.get_columns("work_orders")}
        if "priority" not in work_order_columns:
            conn.execute(text("ALTER TABLE work_orders ADD COLUMN priority INTEGER DEFAULT 5"))
            conn.commit()
            print("[Migration] Added column priority to work_orders")
        if "last_insertion_at" not in work_order_columns:
            conn.execute(text("ALTER TABLE work_orders ADD COLUMN last_insertion_at DATETIME"))
            conn.commit()
            print("[Migration] Added column last_insertion_at to work_orders")
        
        table_names = inspector.get_table_names()
        
        if "insertion_histories" not in table_names:
            conn.execute(text("""
                CREATE TABLE insertion_histories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    order_no VARCHAR NOT NULL,
                    old_priority INTEGER NOT NULL,
                    new_priority INTEGER NOT NULL,
                    operator VARCHAR,
                    reason VARCHAR,
                    affected_orders_count INTEGER DEFAULT 0,
                    delayed_orders_count INTEGER DEFAULT 0,
                    blocked_orders_count INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scenario_id INTEGER,
                    FOREIGN KEY(order_id) REFERENCES work_orders (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table insertion_histories")
        
        if "insertion_affected_orders" not in table_names:
            conn.execute(text("""
                CREATE TABLE insertion_affected_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insertion_history_id INTEGER NOT NULL,
                    affected_order_id INTEGER NOT NULL,
                    affected_order_no VARCHAR NOT NULL,
                    impact_type VARCHAR NOT NULL,
                    delay_minutes INTEGER DEFAULT 0,
                    blocked_reason VARCHAR,
                    original_start_time DATETIME,
                    new_start_time DATETIME,
                    FOREIGN KEY(insertion_history_id) REFERENCES insertion_histories (id),
                    FOREIGN KEY(affected_order_id) REFERENCES work_orders (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table insertion_affected_orders")
        
        if "optimization_tasks" not in table_names:
            conn.execute(text("""
                CREATE TABLE optimization_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_ids VARCHAR NOT NULL,
                    objective VARCHAR NOT NULL,
                    max_duration_seconds INTEGER NOT NULL DEFAULT 300,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    explored_count INTEGER DEFAULT 0,
                    current_best_value INTEGER,
                    baseline_value INTEGER,
                    result_schedule_json VARCHAR,
                    baseline_schedule_json VARCHAR,
                    started_at DATETIME,
                    finished_at DATETIME,
                    cancelled_at DATETIME,
                    cancelled_by VARCHAR,
                    created_by VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_applied BOOLEAN DEFAULT 0,
                    applied_at DATETIME,
                    applied_by VARCHAR,
                    baseline_hash VARCHAR,
                    baseline_timestamp DATETIME,
                    error_message VARCHAR
                )
            """))
            conn.commit()
            print("[Migration] Created table optimization_tasks")
        
        if "optimization_trajectories" not in table_names:
            conn.execute(text("""
                CREATE TABLE optimization_trajectories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    iteration INTEGER NOT NULL,
                    objective_value INTEGER NOT NULL,
                    is_best BOOLEAN DEFAULT 0,
                    recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(task_id) REFERENCES optimization_tasks (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table optimization_trajectories")

        if "capacity_reservations" not in table_names:
            conn.execute(text("""
                CREATE TABLE capacity_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_no VARCHAR NOT NULL UNIQUE,
                    product_name VARCHAR NOT NULL,
                    quantity INTEGER NOT NULL,
                    customer_name VARCHAR,
                    sales_person VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'active',
                    expire_at DATETIME NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    released_at DATETIME,
                    release_reason VARCHAR,
                    trial_earliest_delivery DATETIME,
                    trial_expected_delivery DATETIME,
                    trial_can_meet_deadline BOOLEAN DEFAULT 1,
                    trial_bottleneck_type VARCHAR,
                    trial_bottleneck_step VARCHAR,
                    trial_bottleneck_detail VARCHAR
                )
            """))
            conn.commit()
            print("[Migration] Created table capacity_reservations")

        if "capacity_reservation_slots" not in table_names:
            conn.execute(text("""
                CREATE TABLE capacity_reservation_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reservation_id INTEGER NOT NULL,
                    device_id INTEGER NOT NULL,
                    fixture_id INTEGER,
                    step_order INTEGER NOT NULL,
                    step_name VARCHAR NOT NULL,
                    start_time DATETIME NOT NULL,
                    end_time DATETIME NOT NULL,
                    fixture_turn_over_end_time DATETIME,
                    FOREIGN KEY(reservation_id) REFERENCES capacity_reservations (id),
                    FOREIGN KEY(device_id) REFERENCES devices (id),
                    FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table capacity_reservation_slots")
        
        sub_batch_columns = {col["name"] for col in inspector.get_columns("sub_batches")}
        needed_delivery_sub_batch_cols = {
            "delivery_plan_id": "INTEGER",
            "delivered_quantity": "INTEGER DEFAULT 0"
        }
        for col_name, col_def in needed_delivery_sub_batch_cols.items():
            if col_name not in sub_batch_columns:
                conn.execute(text(f"ALTER TABLE sub_batches ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to sub_batches")
        
        schedule_columns = {col["name"] for col in inspector.get_columns("schedule_entries")}
        if "is_delivered_locked" not in schedule_columns:
            conn.execute(text("ALTER TABLE schedule_entries ADD COLUMN is_delivered_locked BOOLEAN DEFAULT 0"))
            conn.commit()
            print("[Migration] Added column is_delivered_locked to schedule_entries")
        
        if "delivery_plans" not in table_names:
            conn.execute(text("""
                CREATE TABLE delivery_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    plan_index INTEGER NOT NULL,
                    planned_quantity INTEGER NOT NULL,
                    expected_delivery_date DATETIME NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    scenario_id INTEGER,
                    FOREIGN KEY(order_id) REFERENCES work_orders (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table delivery_plans")
        
        if "batch_delivery_records" not in table_names:
            conn.execute(text("""
                CREATE TABLE batch_delivery_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    delivery_plan_id INTEGER NOT NULL,
                    actual_quantity INTEGER NOT NULL,
                    delivered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    accepted_by VARCHAR,
                    accepted_at DATETIME,
                    status VARCHAR NOT NULL DEFAULT 'delivered',
                    remarks VARCHAR,
                    scenario_id INTEGER,
                    FOREIGN KEY(order_id) REFERENCES work_orders (id),
                    FOREIGN KEY(delivery_plan_id) REFERENCES delivery_plans (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table batch_delivery_records")
        
        process_step_columns = {col["name"] for col in inspector.get_columns("process_steps")}
        needed_process_step_cols = {
            "required_skill_id": "INTEGER",
            "required_skill_level": "INTEGER"
        }
        for col_name, col_def in needed_process_step_cols.items():
            if col_name not in process_step_columns:
                conn.execute(text(f"ALTER TABLE process_steps ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to process_steps")
        
        schedule_entry_columns = {col["name"] for col in inspector.get_columns("schedule_entries")}
        if "operator_id" not in schedule_entry_columns:
            conn.execute(text("ALTER TABLE schedule_entries ADD COLUMN operator_id INTEGER"))
            conn.commit()
            print("[Migration] Added column operator_id to schedule_entries")
        
        if "teams" not in table_names:
            conn.execute(text("""
                CREATE TABLE teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL UNIQUE,
                    description VARCHAR,
                    leader_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(leader_id) REFERENCES employees (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table teams")
        
        if "skills" not in table_names:
            conn.execute(text("""
                CREATE TABLE skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR NOT NULL UNIQUE,
                    code VARCHAR NOT NULL UNIQUE,
                    description VARCHAR,
                    compatible_device_types VARCHAR NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            print("[Migration] Created table skills")
        
        if "employees" not in table_names:
            conn.execute(text("""
                CREATE TABLE employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_no VARCHAR NOT NULL UNIQUE,
                    name VARCHAR NOT NULL,
                    team_id INTEGER,
                    phone VARCHAR,
                    email VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'active',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(team_id) REFERENCES teams (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table employees")
        
        if "employee_skills" not in table_names:
            conn.execute(text("""
                CREATE TABLE employee_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    skill_id INTEGER NOT NULL,
                    skill_level INTEGER NOT NULL DEFAULT 1,
                    certification_date DATETIME,
                    expiry_date DATETIME,
                    notes VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(employee_id) REFERENCES employees (id),
                    FOREIGN KEY(skill_id) REFERENCES skills (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table employee_skills")
        
        if "shift_schedules" not in table_names:
            conn.execute(text("""
                CREATE TABLE shift_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL,
                    effective_date DATE NOT NULL,
                    end_date DATE,
                    day_0 VARCHAR,
                    day_1 VARCHAR,
                    day_2 VARCHAR,
                    day_3 VARCHAR,
                    day_4 VARCHAR,
                    day_5 VARCHAR,
                    day_6 VARCHAR,
                    status VARCHAR DEFAULT 'active',
                    is_temporary BOOLEAN DEFAULT 0,
                    notes VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scenario_id INTEGER,
                    FOREIGN KEY(employee_id) REFERENCES employees (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table shift_schedules")
        
        if "schedule_entry_employees" not in table_names:
            conn.execute(text("""
                CREATE TABLE schedule_entry_employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_entry_id INTEGER NOT NULL,
                    employee_id INTEGER NOT NULL,
                    assignment_type VARCHAR NOT NULL DEFAULT 'primary',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scenario_id INTEGER,
                    FOREIGN KEY(schedule_entry_id) REFERENCES schedule_entries (id),
                    FOREIGN KEY(employee_id) REFERENCES employees (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table schedule_entry_employees")
        
        if "scenario_staffing_overrides" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_staffing_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    employee_id INTEGER,
                    skill_id INTEGER,
                    shift_schedule_id INTEGER,
                    override_type VARCHAR NOT NULL,
                    new_shift_type VARCHAR,
                    new_start_time VARCHAR,
                    new_end_time VARCHAR,
                    new_is_rest_day BOOLEAN,
                    effective_from DATETIME,
                    effective_to DATETIME,
                    reason VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
                    FOREIGN KEY(employee_id) REFERENCES employees (id),
                    FOREIGN KEY(skill_id) REFERENCES skills (id),
                    FOREIGN KEY(shift_schedule_id) REFERENCES shift_schedules (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_staffing_overrides")

        if "schedule_groups" not in table_names:
            conn.execute(text("""
                CREATE TABLE schedule_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_code VARCHAR NOT NULL UNIQUE,
                    product_family_id INTEGER,
                    device_id INTEGER NOT NULL,
                    group_type VARCHAR NOT NULL DEFAULT 'auto',
                    is_forced BOOLEAN DEFAULT 0,
                    status VARCHAR NOT NULL DEFAULT 'active',
                    estimated_savings_minutes INTEGER DEFAULT 0,
                    created_by VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    scenario_id INTEGER,
                    FOREIGN KEY(product_family_id) REFERENCES product_families (id),
                    FOREIGN KEY(device_id) REFERENCES devices (id),
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table schedule_groups")

        schedule_entry_columns = {col["name"] for col in inspector.get_columns("schedule_entries")}
        if "group_id" not in schedule_entry_columns:
            conn.execute(text("ALTER TABLE schedule_entries ADD COLUMN group_id INTEGER"))
            conn.commit()
            print("[Migration] Added column group_id to schedule_entries")

        process_step_columns = {col["name"] for col in inspector.get_columns("process_steps")}
        if "requires_inspection" not in process_step_columns:
            conn.execute(text("ALTER TABLE process_steps ADD COLUMN requires_inspection BOOLEAN DEFAULT 0"))
            conn.commit()
            print("[Migration] Added column requires_inspection to process_steps")

        step_progress_columns = {col["name"] for col in inspector.get_columns("sub_batch_step_progress")}
        if "inspection_status" not in step_progress_columns:
            conn.execute(text("ALTER TABLE sub_batch_step_progress ADD COLUMN inspection_status VARCHAR DEFAULT 'not_required'"))
            conn.commit()
            print("[Migration] Added column inspection_status to sub_batch_step_progress")

        if "rework_tasks" not in table_names:
            conn.execute(text("""
                CREATE TABLE rework_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    sub_batch_id INTEGER,
                    parent_rework_task_id INTEGER,
                    rework_sub_batch_id INTEGER,
                    step_order INTEGER NOT NULL,
                    from_step_order INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    rework_count INTEGER DEFAULT 1,
                    status VARCHAR DEFAULT 'pending',
                    is_blocked BOOLEAN DEFAULT 0,
                    blocked_reason VARCHAR,
                    scrap_reason VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    FOREIGN KEY(order_id) REFERENCES work_orders (id),
                    FOREIGN KEY(sub_batch_id) REFERENCES sub_batches (id),
                    FOREIGN KEY(parent_rework_task_id) REFERENCES rework_tasks (id),
                    FOREIGN KEY(rework_sub_batch_id) REFERENCES sub_batches (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table rework_tasks")

        if "quality_inspections" not in table_names:
            conn.execute(text("""
                CREATE TABLE quality_inspections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    sub_batch_id INTEGER,
                    step_order INTEGER NOT NULL,
                    step_id INTEGER NOT NULL,
                    conclusion VARCHAR NOT NULL,
                    qualified_quantity INTEGER DEFAULT 0,
                    unqualified_quantity INTEGER DEFAULT 0,
                    inspector VARCHAR,
                    inspected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    notes VARCHAR,
                    rework_task_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(order_id) REFERENCES work_orders (id),
                    FOREIGN KEY(sub_batch_id) REFERENCES sub_batches (id),
                    FOREIGN KEY(step_id) REFERENCES process_steps (id),
                    FOREIGN KEY(rework_task_id) REFERENCES rework_tasks (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table quality_inspections")
    
    print("[Migration] Database migration completed")
