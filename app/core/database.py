"""
資料庫連線設定。

對應 WBS-API 決策4：身體與腦袋的表分開管理（腦袋的表放在 `brain` schema），
但 MVP 階段可以共用同一個 Postgres instance，用 Postgres schema 隔開就好，
不需要真的拆兩個資料庫執行個體。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency：每個 request 一個 session，用完自動關閉。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
