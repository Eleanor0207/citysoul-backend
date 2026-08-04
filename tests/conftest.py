"""
測試共用 fixture。

假設本機 docker-compose 起的 Postgres/Redis 已經在跑，且 `scripts.init_db`
已經跑過一次（建過 schema）。測試對真實服務跑，不 mock DB/Redis——這是
CONTEXT.md／SDD 反覆強調的「後端確定性規則」精神的自然延伸：驗證的是
真實行為，不是「這段程式碼呼叫了正確的 mock」。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import Base, SessionLocal, engine
from app.main import app

# 確保表存在（如果測試環境還沒跑過 scripts.init_db）
from app.modules.body import models as body_models  # noqa: F401
from app.modules.brain import models as brain_models  # noqa: F401


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS brain"))
        # brain.memory_embeddings 的 VECTOR 欄位需要 pgvector（B6）
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def unique_device_id():
    """每個測試用獨立的 device_id，避免測試之間互相污染既有資料列。"""
    return f"test-device-{uuid.uuid4()}"


@pytest.fixture
def unique_spirit_id():
    """每個測試用獨立的 spirit_id，避免測試資料跟 seed data 或其他測試互相污染。"""
    return f"test-spirit-{uuid.uuid4()}"
