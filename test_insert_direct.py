import sys
sys.path.insert(0, '/Users/chengjie/bytedance/newcj/rtm-109')

from app.database import SessionLocal
from app.scheduler import insert_order_with_priority

def test():
    db = SessionLocal()
    try:
        print("测试 insert_order_with_priority 函数...")
        result = insert_order_with_priority(db, order_id=2, new_priority=9, operator="test", reason="test")
        print(f"结果: {result}")
    except Exception as e:
        import traceback
        print(f"错误: {e}")
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    test()
