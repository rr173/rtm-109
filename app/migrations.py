from sqlalchemy import inspect, text
from app.database import engine


def run_migrations():
    inspector = inspect(engine)
    
    sub_batch_columns = {col["name"] for col in inspector.get_columns("sub_batches")}
    needed_sub_batch_cols = {
        "parent_sub_batch_id": "INTEGER",
        "is_replenishment": "BOOLEAN DEFAULT 0",
        "replenish_level": "INTEGER DEFAULT 0",
        "replenish_from_step": "INTEGER"
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
            "is_migrated": "BOOLEAN DEFAULT 0"
        }
        
        for col_name, col_def in needed_schedule_cols.items():
            if col_name not in schedule_columns:
                conn.execute(text(f"ALTER TABLE schedule_entries ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to schedule_entries")
        
        work_order_columns = {col["name"] for col in inspector.get_columns("work_orders")}
        needed_work_order_cols = {
            "is_blocked": "BOOLEAN DEFAULT 0",
            "blocked_reason": "VARCHAR"
        }
        
        for col_name, col_def in needed_work_order_cols.items():
            if col_name not in work_order_columns:
                conn.execute(text(f"ALTER TABLE work_orders ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to work_orders")
        
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
                    FOREIGN KEY(sub_batch_id) REFERENCES sub_batches (id),
                    FOREIGN KEY(step_id) REFERENCES process_steps (id)
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
                    FOREIGN KEY(device_id) REFERENCES devices (id)
                )
            """))
            conn.commit()
            print("[Migration] Created table device_faults")
    
    print("[Migration] Database migration completed")
