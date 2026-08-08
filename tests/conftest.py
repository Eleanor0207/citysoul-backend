"""
測試共用 fixture。

假設本機 docker-compose 起的 Postgres/Redis 已經在跑；schema 由這裡的
`_ensure_schema` fixture 跑 migration 建好，不需要先手動跑 `scripts.init_db`。
測試對真實服務跑，不 mock DB/Redis——這是 CONTEXT.md／SDD 反覆強調的
「後端確定性規則」精神的自然延伸：驗證的是真實行為，不是「這段程式碼呼叫了
正確的 mock」。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.modules.brain.gemini import FakeGeminiClient, get_gemini_client
from scripts.init_db import upgrade_to_head


@pytest.fixture(autouse=True)
def _no_real_gemini_calls_from_endpoints():
    """
    端點預設拿到 `FakeGeminiClient`，不是真的 Vertex AI client。

    ⚠️ 這條不只是「跑快一點」。少了它，任何打到會生成敘事的端點的測試都會
    真的去嘗試 ADC 認證：沒有憑證的機器上那是**每次呼叫等一輪逾時**（實測
    一個 20 個測試的檔案從 0.4 秒變成 42 秒），有憑證的機器上更糟——測試會
    真的花錢呼叫模型。README 說得很清楚：CI 不需要任何 GCP 憑證。

    需要特定行為（一叫就爆、記錄 DB 狀態）的測試自己再 override 一次，
    後設定的會蓋過這裡。
    """
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient()
    yield
    app.dependency_overrides.pop(get_gemini_client, None)


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    """
    把測試資料庫推到最新的 migration。

    這裡刻意**不用 `Base.metadata.create_all()`**。用 create_all 的話，測試永遠
    是對著「models 說應該長怎樣」跑，migration 寫錯了也不會有任何測試變紅——
    而正式環境拿到的是 migration 的結果，不是 models。改成跑 migration 之後，
    「migration 與 models 不一致」這件事會直接讓整套測試炸掉。
    """
    upgrade_to_head()


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
