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
            "changeover_minutes": "INTEGER DEFAULT 0",
            "prev_product_name": "VARCHAR"
        }
        
        for col_name, col_def in needed_schedule_cols.items():
            if col_name not in schedule_columns:
                conn.execute(text(f"ALTER TABLE schedule_entries ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to schedule_entries")
        
        process_step_columns = {col["name"] for col in inspector.get_columns("process_steps")}
        needed_process_step_cols = {
            "fixture_type_id": "INTEGER"
        }
        
        for col_name, col_def in needed_process_step_cols.items():
            if col_name not in process_step_columns:
                conn.execute(text(f"ALTER TABLE process_steps ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to process_steps")
        
        process_route_columns = {col["name"] for col in inspector.get_columns("process_routes")}
        needed_process_route_cols = {
            "product_family_id": "INTEGER"
        }
        for col_name, col_def in needed_process_route_cols.items():
            if col_name not in process_route_columns:
                conn.execute(text(f"ALTER TABLE process_routes ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to process_routes")
        
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
                    device_id INTEGER,
                    device_type VARCHAR,
                    from_product_family_id INTEGER,
                    to_product_family_id INTEGER,
                    from_product_name VARCHAR,
                    to_product_name VARCHAR,
                    changeover_type VARCHAR NOT NULL DEFAULT 'cross_family',
                    changeover_minutes INTEGER NOT NULL DEFAULT 0,
                    same_product_minutes INTEGER,
                    same_family_minutes INTEGER,
                    cross_family_minutes INTEGER,
                    priority INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(device_id) REFERENCES devices (id),
                    FOREIGN KEY(from_product_family_id) REFERENCES product_families (id),
                    FOREIGN KEY(to_product_family_id) REFERENCES product_families (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table changeover_rules")
        
        if "scenario_changeover_overrides" not in table_names:
            conn.execute(text("""
                CREATE TABLE scenario_changeover_overrides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id INTEGER NOT NULL,
                    changeover_rule_id INTEGER,
                    device_id INTEGER,
                    device_type VARCHAR,
                    from_product_name VARCHAR,
                    to_product_name VARCHAR,
                    override_type VARCHAR NOT NULL,
                    new_changeover_minutes INTEGER,
                    changeover_minutes INTEGER,
                    reason VARCHAR,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(scenario_id) REFERENCES scenarios (id),
                    FOREIGN KEY(changeover_rule_id) REFERENCES changeover_rules (id),
                    FOREIGN KEY(device_id) REFERENCES devices (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table scenario_changeover_overrides")
        else:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(scenario_changeover_overrides)"))]
            if "from_product_name" not in cols:
                conn.execute(text("ALTER TABLE scenario_changeover_overrides ADD COLUMN from_product_name VARCHAR"))
            if "to_product_name" not in cols:
                conn.execute(text("ALTER TABLE scenario_changeover_overrides ADD COLUMN to_product_name VARCHAR"))
            if "changeover_minutes" not in cols:
                conn.execute(text("ALTER TABLE scenario_changeover_overrides ADD COLUMN changeover_minutes INTEGER"))
            conn.commit()
    
    print("[Migration] Database migration completed")
