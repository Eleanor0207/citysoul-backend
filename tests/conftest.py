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
from app.modules.body.router import get_gemini_client, get_tts_client
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.tts import FakeTTSClient
from scripts.init_db import upgrade_to_head


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


@pytest.fixture(autouse=True)
def _fake_external_clients():
    """
    dialogue 端點（issue #42）無條件呼叫 Gemini 與 TTS——即使命中預寫招呼也會
    合成語音。預設全部換成不打真實 GCP 的 fake，任何測試都不會因為忘記手動
    覆寫而意外觸發真實網路呼叫（甚至產生費用）。

    需要驗證特定 Gemini／TTS 行為（例如「命中招呼時 Gemini 不該被呼叫」的
    spy 檢查，或模擬合成失敗）的測試，在測試本身用
    `app.dependency_overrides[get_gemini_client] = ...` 蓋掉這裡的值即可——
    autouse fixture 只負責提供一個安全的預設，不是不能覆寫。
    """
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient()
    app.dependency_overrides[get_tts_client] = lambda: FakeTTSClient()
    yield
    app.dependency_overrides.pop(get_gemini_client, None)
    app.dependency_overrides.pop(get_tts_client, None)


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
