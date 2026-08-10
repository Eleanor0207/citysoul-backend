"""
Ticket #32．配額機制與 429（usage_tiers）。

驗收標準對照見 GitHub issue #32。對真實 Postgres／Redis 跑，不 mock。

`player` fixture 透過 `/api/v1/players` 建立，自動拿到預設分級
`closed_beta`（migration 0004 已種好：`dialogue_calls_daily=50`、
`landmark_recognition_daily=10`）——這兩個數字跟本檔案的預設值測試直接對齊，
不是巧合，是刻意沿用既有種子資料，不用自己另外造一組。
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.modules.body.models import Player, UsageTier, UsageTierLimit
from app.modules.body.quota import QuotaExceededError, _quota_key, consume, current_usage

TAIPEI = ZoneInfo("Asia/Taipei")


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"])


@pytest.fixture(autouse=True)
def _redis_cleanup(player):
    yield
    # 用 scan 掃這個玩家所有資源／日期的 key，不用自己列舉——測試裡跨了
    # 8/6、8/7 兩天,列舉容易漏。
    for key in redis_client.scan_iter(match=f"quota:{player}:*"):
        redis_client.delete(key)


def _at_taipei(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TAIPEI)


# ── 超額擋下、未超額放行（AC1／AC2／AC3）───────────────────────────────

def test_exceeding_limit_raises_and_does_not_record(db_session, player):
    now = _at_taipei(2026, 8, 7, 12)
    for _ in range(50):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    assert current_usage(player, "dialogue_calls_daily", now=now) == 50


def test_under_limit_succeeds_and_accumulates(db_session, player):
    now = _at_taipei(2026, 8, 7, 12)
    for _ in range(3):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    result = consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)
    assert result == 4

    for _ in range(9):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)
    assert current_usage(player, "dialogue_calls_daily", now=now) == 13


def test_exactly_at_limit_boundary(db_session, player):
    """AC3：用滿不等於超過——第 50 次要放行，第 51 次才擋。"""
    now = _at_taipei(2026, 8, 7, 12)
    for _ in range(49):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    assert consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now) == 50

    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)
    assert current_usage(player, "dialogue_calls_daily", now=now) == 50


# ── 以 Asia/Taipei 午夜為界重置（AC4）───────────────────────────────────

def test_resets_at_taipei_midnight(db_session, player):
    yesterday_23 = _at_taipei(2026, 8, 6, 23)
    for _ in range(50):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=yesterday_23)
    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=yesterday_23)

    # 台北時間今天 07:00，其 UTC 仍是昨天 23:00——不能只靠 UTC 日期判斷。
    today_07 = _at_taipei(2026, 8, 7, 7)
    assert today_07.astimezone(timezone.utc).date() == yesterday_23.astimezone(timezone.utc).date()

    result = consume(db_session, player_id=player, resource="dialogue_calls_daily", now=today_07)
    assert result == 1


# ── 上限可設定（AC5）─────────────────────────────────────────────────────

@pytest.fixture
def custom_tier_player(db_session, player):
    """把既有玩家改配到一個上限只有 2 的自訂分級，測完還原。"""
    tier_id = f"tt-{uuid.uuid4().hex[:8]}"  # String(32) 上限，UUID 全長塞不下
    db_session.add(UsageTier(tier_id=tier_id, display_name="測試分級", is_default=False))
    db_session.flush()
    db_session.add(UsageTierLimit(tier_id=tier_id, resource_type="dialogue_calls_daily", limit_value=2))
    db_session.commit()

    row = db_session.query(Player).filter_by(player_id=player).first()
    original_tier_id = row.usage_tier_id
    row.usage_tier_id = tier_id
    db_session.commit()

    yield player

    row.usage_tier_id = original_tier_id
    db_session.commit()
    db_session.query(UsageTierLimit).filter_by(tier_id=tier_id).delete()
    db_session.query(UsageTier).filter_by(tier_id=tier_id).delete()
    db_session.commit()


def test_limit_is_configurable_per_tier(db_session, custom_tier_player):
    """呼叫端程式碼中不出現任何數字字面量：這裡的上限完全來自資料庫設定。"""
    now = _at_taipei(2026, 8, 7, 12)
    consume(db_session, player_id=custom_tier_player, resource="dialogue_calls_daily", now=now)
    consume(db_session, player_id=custom_tier_player, resource="dialogue_calls_daily", now=now)

    with pytest.raises(QuotaExceededError) as exc_info:
        consume(db_session, player_id=custom_tier_player, resource="dialogue_calls_daily", now=now)
    assert exc_info.value.limit == 2


# ── 併發不超賣（AC6）─────────────────────────────────────────────────────

def test_concurrent_consume_does_not_oversell(player):
    """
    玩家剩下 1 格額度，兩個執行緒各自獨立 DB session 同時消耗——恰好一個成功。

    真的用 ThreadPoolExecutor 實測，不是單執行緒模擬：#16／#32 都明訂單執行緒
    測試證明不了這件事。
    """
    now = _at_taipei(2026, 8, 7, 12)
    setup_session = SessionLocal()
    try:
        for _ in range(49):
            consume(setup_session, player_id=player, resource="dialogue_calls_daily", now=now)
    finally:
        setup_session.close()

    def _attempt():
        session = SessionLocal()
        try:
            consume(session, player_id=player, resource="dialogue_calls_daily", now=now)
            return "ok"
        except QuotaExceededError:
            return "blocked"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _attempt(), range(2)))

    assert sorted(results) == ["blocked", "ok"]
    assert current_usage(player, "dialogue_calls_daily", now=now) == 50


# ── 資源與玩家隔離（AC7／AC8）────────────────────────────────────────────

def test_different_resources_are_counted_independently(db_session, player):
    now = _at_taipei(2026, 8, 7, 12)
    for _ in range(50):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)
    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    # 對話用滿不影響拍照辨識額度。
    result = consume(db_session, player_id=player, resource="landmark_recognition_daily", now=now)
    assert result == 1


def test_quota_is_isolated_per_player(db_session, player, client):
    other_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    other_player = uuid.UUID(other_body["player_id"])
    try:
        now = _at_taipei(2026, 8, 7, 12)
        for _ in range(50):
            consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

        result = consume(db_session, player_id=other_player, resource="dialogue_calls_daily", now=now)
        assert result == 1
        assert current_usage(player, "dialogue_calls_daily", now=now) == 50
    finally:
        redis_client.delete(_quota_key(other_player, "dialogue_calls_daily", now.date()))


# ── 例外只帶自己的資訊（AC9 的機制面）────────────────────────────────────

def test_exception_carries_no_cross_player_information(db_session, player):
    """
    AC9 的落地在 API 層（#42），但機制本身就該讓那件事自然成立：例外物件
    公開屬性只有 `resource`／`limit`／`reset_at`，沒有任何其他玩家的
    `player_id`、全站統計或內部設定值可以被不小心序列化出去。
    """
    now = _at_taipei(2026, 8, 7, 12)
    for _ in range(50):
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    with pytest.raises(QuotaExceededError) as exc_info:
        consume(db_session, player_id=player, resource="dialogue_calls_daily", now=now)

    public_attrs = {k for k in vars(exc_info.value) if not k.startswith("_")}
    assert public_attrs == {"resource", "limit", "reset_at"}
