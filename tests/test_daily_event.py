"""
Ticket #26．當日情境排程／快取／對外 API（S10）。

驗收標準對照見 GitHub issue #26。`trigger_daily_event_generation` 直接呼叫
測服務層，`GET /spirits/{placeId}/daily-event` 走 `TestClient` 測 API 層。
B9（#20）以 fake 注入，不需要 GCP 憑證。
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.modules.body import models
from app.modules.body.daily_event_scheduler import trigger_daily_event_generation
from app.modules.brain.daily_event import DAILY_EVENT_FALLBACK
from app.modules.brain.gemini import FakeGeminiClient

TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}", display_name="測試地標",
        latitude=25.0955, longitude=121.5186, summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.DailyEventCache).filter_by(place_id=row.spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.commit()


def _at_taipei(y, m, d, hh) -> datetime:
    return datetime(y, m, d, hh, tzinfo=TAIPEI)


def _get_daily_event(client, spirit_id):
    return client.get(f"/api/v1/spirits/{spirit_id}/daily-event")


# ── daily_event_cache 表（issue #26 AC1，透過 ORM 間接驗證）───────────────

def test_trigger_writes_a_cache_row(db_session, spirit):
    fake = FakeGeminiClient(response="今天廟埕比較安靜。")
    now = _at_taipei(2026, 8, 7, 6)

    row = trigger_daily_event_generation(
        db_session, place_id=spirit.spirit_id, client=fake, now=now,
        official_event="今日有法會活動",
    )

    assert row is not None
    assert row.place_id == spirit.spirit_id
    assert row.event_date == date(2026, 8, 7)
    assert row.content == {"narrative_text": "今天廟埕比較安靜。"}


# ── 回傳當日快取內容（issue #26 AC2）─────────────────────────────────────

def test_returns_todays_cached_content_no_token_required(client, db_session, spirit):
    fake = FakeGeminiClient(response="今天的內容。")
    now = datetime.now(timezone.utc)
    trigger_daily_event_generation(
        db_session, place_id=spirit.spirit_id, client=fake, now=now,
        official_event="今日有法會活動",
    )

    response = _get_daily_event(client, spirit.spirit_id)

    assert response.status_code == 200
    assert response.json() == {"narrative_text": "今天的內容。"}


# ── 今天沒有時回昨天的內容（issue #26 AC3）───────────────────────────────

def test_falls_back_to_yesterdays_content_when_today_missing(client, db_session, spirit):
    """
    `GET /spirits/{placeId}/daily-event` 內部用真實時鐘算「今天」（一支
    對外公開的唯讀端點沒有理由接受「假裝是哪一天」這種參數）。要測「今天
    沒有、昨天有」而不綁死在寫死的日期上，這裡用真實時鐘反推「台北的昨天」
    是哪一天，寫那個日期的快取列——不管測試在哪一天實際執行都成立，不是
    賭執行日剛好是某個寫死的日期。
    """
    from app.modules.body.quests import taipei_today

    yesterday_date = taipei_today(datetime.now(timezone.utc)) - timedelta(days=1)
    yesterday_noon_taipei = datetime.combine(yesterday_date, datetime.min.time(), TAIPEI).replace(hour=12)

    fake = FakeGeminiClient(response="昨天的內容。")
    trigger_daily_event_generation(
        db_session, place_id=spirit.spirit_id, client=fake, now=yesterday_noon_taipei,
        official_event="昨日有法會活動",
    )

    response = _get_daily_event(client, spirit.spirit_id)

    assert response.status_code == 200
    assert response.json() == {"narrative_text": "昨天的內容。"}


def test_falls_back_to_yesterday_with_explicit_dates(db_session, spirit):
    """
    直接操作 `trigger_daily_event_generation` 與資料庫查詢，不依賴真實時鐘
    ——比對 API 層那條測試更精準地鎖定「今天沒有、昨天有」這個情境本身。
    """
    fake = FakeGeminiClient(response="昨天的內容。")
    yesterday = _at_taipei(2026, 8, 6, 12)
    trigger_daily_event_generation(
        db_session, place_id=spirit.spirit_id, client=fake, now=yesterday,
        official_event="昨日有法會活動",
    )

    rows = (
        db_session.query(models.DailyEventCache)
        .filter_by(place_id=spirit.spirit_id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].event_date == date(2026, 8, 6)
    assert rows[0].content == {"narrative_text": "昨天的內容。"}


# ── 連前一天都沒有時回保底文字（issue #26 AC4）───────────────────────────

def test_falls_back_to_hardcoded_text_when_nothing_cached(client, spirit):
    response = _get_daily_event(client, spirit.spirit_id)

    assert response.status_code == 200
    assert response.json() == {"narrative_text": DAILY_EVENT_FALLBACK}


# ── placeId 不存在回 404（issue #26 AC5）─────────────────────────────────

def test_nonexistent_place_returns_404(client):
    response = _get_daily_event(client, "no-such-place")
    assert response.status_code == 404


def test_existing_place_with_no_content_is_200_not_404(client, spirit):
    """對照上一條：地標存在但沒內容 → 200 ＋ 保底，不是 404。"""
    response = _get_daily_event(client, spirit.spirit_id)
    assert response.status_code == 200


# ── 重複觸發不產生重複列（issue #26 AC6）─────────────────────────────────

def test_repeated_trigger_does_not_create_duplicate_rows(db_session, spirit):
    now = _at_taipei(2026, 8, 7, 6)
    fake1 = FakeGeminiClient(response="第一次生成。")
    fake2 = FakeGeminiClient(response="第二次生成。")

    first = trigger_daily_event_generation(db_session, place_id=spirit.spirit_id, client=fake1, now=now)
    second = trigger_daily_event_generation(db_session, place_id=spirit.spirit_id, client=fake2, now=now)

    assert first is not None
    assert second is None  # 第二次不拋例外，優雅回傳 None 表示已存在

    count = (
        db_session.query(models.DailyEventCache)
        .filter_by(place_id=spirit.spirit_id, event_date=date(2026, 8, 7))
        .count()
    )
    assert count == 1


# ── 日界以 Asia/Taipei 午夜為準（issue #26 AC7）──────────────────────────

def test_event_date_uses_taipei_calendar_day(db_session, spirit):
    """
    台北今天 07:00，其 UTC 仍是昨天——event_date 必須是台北的今天。
    """
    now = _at_taipei(2026, 8, 7, 7)
    assert now.astimezone(timezone.utc).date() == date(2026, 8, 6)  # UTC 仍是昨天

    fake = FakeGeminiClient()
    row = trigger_daily_event_generation(db_session, place_id=spirit.spirit_id, client=fake, now=now)

    assert row.event_date == date(2026, 8, 7)  # 台北的今天，不是 UTC 的昨天
