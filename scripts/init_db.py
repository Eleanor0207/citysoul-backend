"""
本機/開發環境一鍵初始化 DB：

    1. 跑 Alembic migration 到最新版（建 schema、extension、所有表）
    2. 跑垂直切片 seed data

用法：
    python -m scripts.init_db

**schema 只有一個來源：`migrations/versions/`。** 這支腳本不再呼叫
`create_all()`，也不再有那份手寫的 `ALTER TABLE ... IF NOT EXISTS` 清單
（#31 之前的權宜之計，每加一個欄位就長一行）。要改表就寫一支 migration；
這裡只負責「跑到最新版，然後塞開發用資料」。

已經有資料的舊資料庫第一次接上 Alembic 時，先宣告目前狀態再往前跑：

    uv run python -m alembic stamp 0001
    uv run python -m scripts.init_db
"""
from alembic import command
from alembic.config import Config

from app.core.database import SessionLocal
from app.db.seed import seed_vertical_slice

# 專案根目錄的 alembic.ini（scripts/ 的上一層）
_ALEMBIC_INI = "alembic.ini"


def upgrade_to_head():
    command.upgrade(Config(_ALEMBIC_INI), "head")


def main():
    upgrade_to_head()

    db = SessionLocal()
    try:
        seed_vertical_slice(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
