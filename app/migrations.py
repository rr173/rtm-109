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
            "actual_completion_time": "DATETIME"
        }
        
        for col_name, col_def in needed_schedule_cols.items():
            if col_name not in schedule_columns:
                conn.execute(text(f"ALTER TABLE schedule_entries ADD COLUMN {col_name} {col_def}"))
                conn.commit()
                print(f"[Migration] Added column {col_name} to schedule_entries")
        
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
    
    print("[Migration] Database migration completed")
