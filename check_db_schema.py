import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.models import Base
from app.database import engine
from sqlalchemy import inspect

inspector = inspect(engine)

print("=== 检查数据库表和模型的差异 ===\n")

for table_name in Base.metadata.tables.keys():
    try:
        db_columns = {col['name'] for col in inspector.get_columns(table_name)}
        model_columns = {col.name for col in Base.metadata.tables[table_name].columns}
        
        missing = model_columns - db_columns
        extra = db_columns - model_columns
        
        if missing or extra:
            print(f"表: {table_name}")
            if missing:
                print(f"  缺少列: {missing}")
            if extra:
                print(f"  多余列: {extra}")
    except Exception as e:
        print(f"表: {table_name} - 错误: {e}")

print("\n=== 检查完成 ===")
