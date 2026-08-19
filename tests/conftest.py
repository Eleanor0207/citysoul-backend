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
def _never_call_real_cloud_services():
    """
    🔒 對話端點的模型與語音預設一律注入 fake。

    ⚠️ 這條是被實際踩到才加的：`/dialogue` 接上 B1／B10 之後，本機因為有 ADC，
    測試**真的打到了 Google Cloud TTS**（回了「API 未啟用」的錯誤才被發現）。
    CI 上沒有憑證所以會安靜地走 fallback，本機卻在花錢也在等網路。

    預設注入 fake 之後，要碰真實服務必須在測試裡明確覆寫回去——安全的方向是
    預設不連外，而不是每支測試各自記得要 mock。

    ⚠️ 2026-08-19：`get_landmark_recognizer` 已隨拍照端點一起移除。
    🆕 `/summon` 現在也會呼叫 Gemini（跨門檻時生成解鎖敘事），所以這條 fixture
    對召喚測試同樣是必要的——那條路徑以前不碰模型。
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


@pytest.fixture
def purge_spirit_child_rows(db_session):
    """
    回傳一支 `purge(spirit_id)`，刪掉該測試地標的所有子表列，好讓
    `db_session.delete(spirit)` 不會撞外鍵。

    ## 🔴 為什麼需要這個

    `resonance` 與 `resonance_events` 都對 `spirits.spirit_id` 有外鍵。
    2026-08-19 起 `/summon` **每次成功召喚都會寫這兩張表**（每日共鳴 +10），
    所以任何「召喚過再刪 spirit」的夾具都會撞上約束——而在那之前，召喚不寫
    任何共鳴列，這些夾具一直是對的。

    ⚠️ 教訓收斂在這裡而不是複製到每個測試檔：**下一個在召喚路徑上寫新表的人**
    只要改這一支，就不必去翻哪些夾具漏了清理。漏掉的症狀是
    `ForeignKeyViolation`，而它會出現在**別的測試的 teardown** 裡，很難查。

    ## ⚠️ 做成 fixture，不是可以 `from conftest import` 的函式

    2026-08-19 第一版寫成模組層級函式，測試檔用 `from conftest import ...`
    ——那在 collection 階段就會 `ModuleNotFoundError`。conftest.py 是 pytest
    自己載入的，**不在 sys.path 上**，不該被當成一般模組匯入。
    共用的東西一律走 fixture 注入。

    ⚠️ 有幾個較早的測試檔（`test_summon.py`、`test_resonance.py`、
    `test_quest_complete.py`）自己寫了同樣的兩行。那些是對的，沒有一起改成
    用這一支，只是為了讓這一批的 diff 停在「修好壞掉的」。
    """
    from app.modules.body import models

    def _purge(spirit_id):
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=spirit_id).delete()
        db_session.commit()

    return _purge
