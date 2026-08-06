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


def _add_missing_columns():
    """
    補上 `create_all` 加不了的欄位。

    `create_all` 只會建**不存在的表**，對已經存在的表完全不動——所以在既有的
    開發資料庫上新增欄位時，它幫不上忙，而症狀是執行期的
    `UndefinedColumn: column spirits.sense_radius_m does not exist`。

    這是一個明確的權宜之計。正確做法是 Alembic 遷移（#31），在那之前，
    每次新增欄位都要在這裡補一行 idempotent 的 ALTER。**這個清單只會愈長愈醜**
    ——那正是它應該推動 #31 落地的原因，不要習慣它。

    `IF NOT EXISTS` ＋ `DEFAULT` 讓既有資料列直接拿到預設值，不需要重建資料庫。
    """
    statements = [
        "ALTER TABLE spirits ADD COLUMN IF NOT EXISTS "
        "sense_radius_m INTEGER NOT NULL DEFAULT 150",
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def main():
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS brain"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))  # 給 Sprint2 的 pgvector 表先備好

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

    db = SessionLocal()
    try:
        seed_vertical_slice(db)
    finally:
        db.close()

    print("✅ DB schema 建立完成，垂直切片 seed data 已寫入。")


if __name__ == "__main__":
    main()
