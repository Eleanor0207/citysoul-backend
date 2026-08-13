"""
塞開發測試用的額外靈魂（目前只有剝皮寮）。

跟 `scripts.init_db` 分開的理由見 `app/db/seed_dev.py` 的模組註解：那支跑的是
垂直切片資料，而垂直切片刻意只有一個靈魂。

這支**不跑 migration**——schema 由 `init_db` 或 Cloud Run 的 citysoul-migrate
負責，這裡只塞資料。DB 還沒建過的話先跑：

    uv run python -m scripts.init_db

用法：

    uv run python -m scripts.seed_dev
"""
from app.core.database import SessionLocal
from app.db.seed_dev import seed_dev_extras


def main():
    db = SessionLocal()
    try:
        seed_dev_extras(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
