"""
#26．S10 當日情境排程、快取與對外 API。

B9 的內容生成以 fake 注入——**不需要 GCP 憑證**。對真實 Postgres 跑。

⚠️ 這裡最重要的一組是**保底**：端點永遠不回空畫面。排程延遲、排程失敗、新地標
剛上線都會讓今天的快取不存在，而它們全都是會發生的事。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.body import models
from app.modules.body.daily_event_service import (
    SpiritNotFoundError,
    get_daily_event,
    refresh_daily_event,
)
from app.modules.body.quests import taipei_today
from app.modules.brain.daily_event import DailyEventInputs
from app.modules.brain.gemini import FakeGeminiClient

_LAT, _LON = 25.0373983, 121.4997318
_GENERATED = "今天廟埕比平常安靜，只有幾個老人坐在樹下。"

# 台北 = UTC+8。這兩個時間點的 **UTC 日期相同**，但台北日期差一天——
# 用 UTC 算日界的實作會在這裡失敗（#15 踩過的坑）。
_TAIPEI_YESTERDAY_2300 = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)  # 台北 3/10 23:00
_TAIPEI_TODAY_0700 = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)  # 台北 3/11 07:00


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.DailyEventCache).filter_by(place_id=unique_spirit_id).delete()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def brain():
    return FakeGeminiClient(response=_GENERATED)


def _inputs():
    """有合格輸入，否則 B9 會直接走人工預寫保底而不呼叫模型。"""
    return DailyEventInputs(festival="中元節")


def _cache_rows(db_session, place_id):
    db_session.expire_all()
    return db_session.query(models.DailyEventCache).filter_by(place_id=place_id).all()


# ── 表結構 ─────────────────────────────────────────────────────────────

def test_table_has_the_expected_shape(db_session):
    """AC：欄位與型別符合 SDD §3.1，PK 為 `(place_id, event_date)`。"""
    from sqlalchemy import inspect

    inspector = inspect(db_session.bind)
    columns = {c["name"] for c in inspector.get_columns("daily_event_cache")}
    pk = set(inspector.get_pk_constraint("daily_event_cache")["constrained_columns"])
    fks = inspector.get_foreign_keys("daily_event_cache")

    assert columns == {"place_id", "event_date", "content", "generated_at", "expires_at"}
    assert pk == {"place_id", "event_date"}
    assert any(fk["referred_table"] == "spirits" for fk in fks)


# ── 排程觸發 ───────────────────────────────────────────────────────────

def test_refresh_creates_a_cache_row(db_session, spirit, brain):
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    rows = _cache_rows(db_session, spirit.spirit_id)
    assert len(rows) == 1
    assert rows[0].content["narrative_text"] == _GENERATED


def test_refresh_twice_does_not_create_duplicate_rows(db_session, spirit, brain):
    """
    🔒 AC：同一天連續觸發兩次 → **1 列**，且第二次不拋例外。

    重試、多實例、手動補跑都會讓同一個 `(place_id, event_date)` 被觸發兩次。
    去重靠**主鍵**，不是靠排程自己記得——同 #16 與 #32 的處理。
    """
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    assert len(_cache_rows(db_session, spirit.spirit_id)) == 1


def test_refresh_updates_the_content_on_retrigger(db_session, spirit):
    """
    重新觸發的意圖是「用最新的內容覆蓋」，不是「什麼都不做」。

    PK 衝突只代表「我們比自己早一步」，不代表這次的內容該被丟掉。
    """
    refresh_daily_event(
        db_session, FakeGeminiClient(response="第一版"), place_id=spirit.spirit_id, inputs=_inputs()
    )
    refresh_daily_event(
        db_session, FakeGeminiClient(response="第二版"), place_id=spirit.spirit_id, inputs=_inputs()
    )

    rows = _cache_rows(db_session, spirit.spirit_id)
    assert len(rows) == 1
    assert rows[0].content["narrative_text"] == "第二版"


def test_refresh_uses_taipei_midnight_as_the_day_boundary(db_session, spirit, brain):
    """
    🔒 AC：注入台北今天 07:00（其 UTC 仍是昨天）→ `event_date` 為**台北的今天**。

    ⚠️ 兩個測試時間點的 UTC 日期相同、台北日期差一天。用 UTC 算日界的實作會在
    這裡失敗，而用真實時鐘的測試則會在 UTC 16:00 前後給出不同結果（#15 的坑）。
    """
    refresh_daily_event(
        db_session, brain, place_id=spirit.spirit_id, inputs=_inputs(), now=_TAIPEI_TODAY_0700
    )

    rows = _cache_rows(db_session, spirit.spirit_id)
    assert rows[0].event_date == taipei_today(_TAIPEI_TODAY_0700)
    # 台北 3/11，不是 UTC 的 3/10。
    assert rows[0].event_date.isoformat() == "2026-03-11"


def test_refresh_on_unknown_spirit_raises(db_session, brain):
    with pytest.raises(SpiritNotFoundError):
        refresh_daily_event(db_session, brain, place_id="no-such-place", inputs=_inputs())


def test_expiry_outlives_a_single_day(db_session, spirit, brain):
    """
    TTL 比一天長，讓「今天沒生成就回昨天」真的有東西可以拿。

    設成剛好 24 小時的話，排程晚一小時，昨天的內容也剛好過期了。
    """
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    row = _cache_rows(db_session, spirit.spirit_id)[0]
    assert row.expires_at - row.generated_at > timedelta(hours=24)


# ── 讀取：當日內容 ─────────────────────────────────────────────────────

def test_returns_todays_content(client, db_session, spirit, brain):
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    response = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert response.status_code == 200
    assert response.json()["narrative_text"] == _GENERATED


def test_endpoint_requires_no_token(client, db_session, spirit, brain):
    """AC：公開世界狀態，不需要任何 token。"""
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    response = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert response.status_code == 200


# ── 保底 ───────────────────────────────────────────────────────────────

def test_falls_back_to_yesterday_with_200(client, db_session, spirit, brain):
    """
    🔒 AC：今天無快取、昨天有 → `200` ＋ **昨天的內容**。不是 404、不是空 body。

    排程延遲或失敗時，玩家不該看到空畫面——這是硬要求。

    AC 指定要做 mutation 驗證的其中一條。
    """
    yesterday = taipei_today(datetime.now(timezone.utc)) - timedelta(days=1)
    db_session.add(
        models.DailyEventCache(
            place_id=spirit.spirit_id,
            event_date=yesterday,
            content={"narrative_text": "昨天的內容", "is_fallback": False, "sources": []},
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert response.status_code == 200
    assert response.json()["narrative_text"] == "昨天的內容"


def test_expired_content_is_still_served_as_fallback(client, db_session, spirit):
    """
    過期的內容仍然拿來頂。

    `expires_at` 是給清理工作的訊號，**不是讀取時的過濾條件**——保底本來就是
    「拿舊的來頂」，讀取時再過濾一次等於把保底自己關掉。
    """
    long_ago = taipei_today(datetime.now(timezone.utc)) - timedelta(days=30)
    db_session.add(
        models.DailyEventCache(
            place_id=spirit.spirit_id,
            event_date=long_ago,
            content={"narrative_text": "很久以前的內容", "is_fallback": False, "sources": []},
            expires_at=datetime.now(timezone.utc) - timedelta(days=25),
        )
    )
    db_session.commit()

    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event").json()

    assert body["narrative_text"] == "很久以前的內容"


def test_falls_back_to_prewritten_content_when_nothing_exists(client, spirit):
    """
    🔒 AC：連前一天都沒有 → `200` ＋ 人工預寫保底。**仍不是 404**。

    新地標剛上線、或排程從沒跑過時會走到這裡。

    AC 指定要做 mutation 驗證的其中一條。
    """
    response = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert response.status_code == 200
    body = response.json()
    assert body["narrative_text"].strip()
    assert body["is_fallback"] is True


def test_fallback_body_is_never_empty(client, spirit):
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event").json()

    assert len(body["narrative_text"].strip()) > 10


# ── 404 的分界 ────────────────────────────────────────────────────────

def test_unknown_place_returns_404(client):
    """
    ⚠️ 跟保底的分界：**地標不存在 → 404**；**地標存在但沒內容 → 200 ＋ 保底**。

    前者是玩家問錯了東西，後者是我們還沒準備好。兩件事不該給同一個答案。
    """
    assert client.get("/api/v1/spirits/no-such-place/daily-event").status_code == 404


def test_inactive_spirit_returns_404(client, db_session, spirit):
    """下架的靈魂比照其他端點回 404。"""
    spirit.is_active = False
    db_session.commit()

    assert client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event").status_code == 404


# ── 模組邊界 ───────────────────────────────────────────────────────────

def test_service_delegates_generation_to_b9(db_session, spirit):
    """
    內容生成屬 B9（#20），這個模組只管排程與快取（v2.1 §6.4）。

    驗證方式是「B9 的 fake 收到了呼叫」——如果 S10 自己組了 prompt，這裡會看到
    不一樣的東西。
    """
    brain = FakeGeminiClient(response=_GENERATED)

    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=_inputs())

    assert brain.call_count == 1
    assert "中元節" in brain.prompts[0]


def test_no_qualifying_input_still_caches_the_fallback(db_session, spirit, brain):
    """
    沒有合格輸入時 B9 回人工預寫保底，而那**仍然要寫進快取**。

    不寫的話，端點每次都得走一次「完全沒有快取」的路徑，而且排程看起來像從沒
    成功過——那會掩蓋掉真正的排程故障。
    """
    refresh_daily_event(db_session, brain, place_id=spirit.spirit_id, inputs=DailyEventInputs())

    rows = _cache_rows(db_session, spirit.spirit_id)
    assert len(rows) == 1
    assert rows[0].content["is_fallback"] is True
    assert brain.call_count == 0  # B9 沒有合格輸入時不呼叫模型
