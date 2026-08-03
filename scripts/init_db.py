"""
本機/開發環境一鍵初始化 DB：
    1. 建立 brain schema（persona_cards 放在裡面）
    2. 建立所有 ORM 定義的表
    3. 跑垂直切片 seed data

之後進到 Sprint2 開始有真的 schema 變動需求時，這支腳本要換成 Alembic
migration（現在先用 create_all 是為了讓你今天就能跑起來，不是長期方案）。

用法：
    python -m scripts.init_db
"""
from sqlalchemy import text

from app.core.database import Base, SessionLocal, engine

# 一定要 import 到，Base.metadata 才會知道這些表存在
from app.modules.body import models as body_models  # noqa: F401
from app.modules.brain import models as brain_models  # noqa: F401
from app.db.seed import seed_vertical_slice


def main():
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS brain"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # 給 Sprint2 的 pgvector 表先備好

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        seed_vertical_slice(db)
    finally:
        db.close()

    print("✅ DB schema 建立完成，垂直切片 seed data 已寫入。")


if __name__ == "__main__":
    main()
