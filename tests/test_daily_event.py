"""
Ticket #26．當日情境排程觸發＋快取＋對外 API（S10）。

驗收標準對照見 GitHub issue #26。B9 的部分用 `FakeGeminiClient` 注入，
完全不需要 GCP 憑證；跟其他端點測試一樣對真實 Postgres 跑，不 mock。
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.body.daily_event import (
    get_daily_event_for_spirit,
    trigger_daily_event_generation,
)
from app.modules.body.models import DailyEventCache, Spirit
from app.modules.body.quests import TAIPEI, taipei_today
from app.modules.brain.gemini import FakeGeminiClient


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(DailyEventCache).filter_by(place_id=unique_spirit_id).delete()
    db_session.delete(row)
    db_session.commit()


# ── schema（AC1） ────────────────────────────────────────────────────────


def test_place_id_has_foreign_key_to_spirits(db_session, unique_spirit_id):
    row = DailyEventCache(
        place_id=unique_spirit_id,  # 沒有對應的 spirits 列
        event_date=date(2026, 1, 1),
        content={"narrative_text": "x"},
    )
    db_session.add(row)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_primary_key_is_place_id_and_event_date(db_session, spirit):
    row_a = DailyEventCache(
        place_id=spirit.spirit_id, event_date=date(2026, 1, 1), content={"narrative_text": "a"}
    )
    db_session.add(row_a)
    db_session.commit()

    row_b = DailyEventCache(
        place_id=spirit.spirit_id, event_date=date(2026, 1, 1), content={"narrative_text": "b"}
    )
    db_session.add(row_b)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── 回傳今天的快取（AC：回傳當日快取內容） ────────────────────────────────


def test_returns_todays_cached_content(client, db_session, spirit):
    today = taipei_today(datetime.now(timezone.utc))
    db_session.add(
        DailyEventCache(
            place_id=spirit.spirit_id,
            event_date=today,
            content={"narrative_text": "今天廟埕有陣頭表演。"},
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative_text"] == "今天廟埕有陣頭表演。"
    assert body["source"] == "cached_today"


def test_daily_event_requires_no_token(client, db_session, spirit):
    """公開世界狀態——不帶 Authorization header 也該成功，不是 401。"""
    resp = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")
    assert resp.status_code != 401


# ── 今天沒有時回前一天（AC） ──────────────────────────────────────────────


def test_falls_back_to_previous_day_when_today_missing(client, db_session, spirit):
    today = taipei_today(datetime.now(timezone.utc))
    yesterday = today - timedelta(days=1)
    db_session.add(
        DailyEventCache(
            place_id=spirit.spirit_id,
            event_date=yesterday,
            content={"narrative_text": "昨天的內容"},
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative_text"] == "昨天的內容"
    assert body["source"] == "cached_previous_day"


# ── 連前一天都沒有時回人工保底（AC） ──────────────────────────────────────


def test_falls_back_to_static_content_when_no_cache_at_all(client, spirit):
    resp = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert resp.status_code == 200
    body = resp.json()
    assert body["narrative_text"]
    assert body["source"] == "fallback"


# ── placeId 不存在回 404（AC，跟上一條的差別） ────────────────────────────


def test_unknown_place_id_returns_404(client):
    resp = client.get("/api/v1/spirits/no-such-place/daily-event")
    assert resp.status_code == 404


# ── 排程重複觸發不產生重複列（AC） ────────────────────────────────────────


def test_repeated_trigger_does_not_create_duplicate_rows(db_session, spirit):
    fake = FakeGeminiClient(response="今天的內容")

    trigger_daily_event_generation(
        db_session, spirit.spirit_id, official_events=["某活動"], gemini_client=fake
    )
    trigger_daily_event_generation(
        db_session, spirit.spirit_id, official_events=["某活動"], gemini_client=fake
    )  # 第二次不該拋例外

    today = taipei_today(datetime.now(timezone.utc))
    count = (
        db_session.query(DailyEventCache)
        .filter_by(place_id=spirit.spirit_id, event_date=today)
        .count()
    )
    assert count == 1


# ── 日界以 Asia/Taipei 午夜為準（AC） ──────────────────────────────────────


def test_trigger_uses_taipei_day_boundary_not_utc(db_session, spirit):
    """
    注入時間為台北今天 07:00（其 UTC 仍是昨天）——`event_date` 要是台北的
    今天，不是 UTC 的昨天。沿用 `quests.taipei_today()`，不另寫一套。
    """
    now_taipei_early_morning = datetime(2026, 1, 16, 7, 0, tzinfo=TAIPEI)
    fake = FakeGeminiClient(response="早上的內容")

    trigger_daily_event_generation(
        db_session,
        spirit.spirit_id,
        now=now_taipei_early_morning,
        official_events=["某活動"],
        gemini_client=fake,
    )

    row = (
        db_session.query(DailyEventCache)
        .filter_by(place_id=spirit.spirit_id)
        .one()
    )
    assert row.event_date == date(2026, 1, 16)


# ── 整合：get_daily_event_for_spirit 直接測（不透過 HTTP） ────────────────


def test_get_daily_event_for_spirit_returns_none_for_unknown_spirit(db_session):
    assert get_daily_event_for_spirit(db_session, "no-such-place") is None


def test_trigger_then_read_round_trip(db_session, spirit):
    fake = FakeGeminiClient(response="剛生成的內容")

    trigger_daily_event_generation(
        db_session, spirit.spirit_id, official_events=["某活動"], gemini_client=fake
    )
    result = get_daily_event_for_spirit(db_session, spirit.spirit_id)

    assert result.narrative_text == "剛生成的內容"
    assert result.source == "cached_today"
