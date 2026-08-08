"""
#32．配額機制與 429。

對真實 Postgres ＋ 真實 Redis 跑，不 mock（沿用 conftest 夾具）。

⚠️ **測試不寫死商業分級。** 每個測試自己把上限調成它需要的數字，因為
`usage_tier_limits` 的值是產品決策（v2.1 §14 列為 Phase 7 才拍板），
今天寫死 50，那些數字被調整的那天整組測試會無意義地變紅。
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.main import quota_exceeded_response
from app.modules.body import models
from app.modules.body.quota import (
    RESOURCE_DIALOGUE,
    RESOURCE_LANDMARK_RECOGNITION,
    QuotaExceededError,
    UnknownQuotaResourceError,
    consume,
    current_usage,
    default_tier_id,
    next_taipei_midnight,
)

# 台北 = UTC+8。這兩個時間點刻意選在「台北已經是新的一天，UTC 還是昨天」的
# 區間裡——跨日重置的 bug 只在這個區間才看得出來。
_TAIPEI_YESTERDAY_2300 = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)  # 台北 3/10 23:00
_TAIPEI_TODAY_0700 = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)  # 台北 3/11 07:00


@pytest.fixture
def tier(db_session):
    """
    每個測試用自己的分級，上限由測試自己設定。

    共用 `closed_beta` 的話，一個測試改了上限就會影響其他測試——而且那筆資料是
    migration 建的正式設定，測試不該去動它。
    """
    tier_id = f"test-tier-{uuid.uuid4().hex[:8]}"
    row = models.UsageTier(tier_id=tier_id, display_name="測試分級", is_default=False)
    db_session.add(row)
    db_session.commit()
    yield tier_id
    db_session.query(models.UsageTierLimit).filter_by(tier_id=tier_id).delete()
    db_session.query(models.UsageTier).filter_by(tier_id=tier_id).delete()
    db_session.commit()


def _set_limit(db_session, tier_id: str, resource: str, value: int) -> None:
    existing = (
        db_session.query(models.UsageTierLimit)
        .filter_by(tier_id=tier_id, resource_type=resource)
        .first()
    )
    if existing:
        existing.limit_value = value
    else:
        db_session.add(
            models.UsageTierLimit(tier_id=tier_id, resource_type=resource, limit_value=value)
        )
    db_session.commit()


@pytest.fixture
def player(db_session, tier, unique_device_id):
    row = models.Player(device_id=unique_device_id, usage_tier_id=tier)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture(autouse=True)
def _clear_quota_keys():
    """
    每個測試前後清掉配額 key。

    Redis 的計數器有 TTL 但活到台北午夜，測試之間會互相污染。
    """
    yield
    for key in redis_client.scan_iter("quota:*"):
        redis_client.delete(key)


# ── 基本累計 ───────────────────────────────────────────────────────────

def test_consume_increments_usage(db_session, tier, player):
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 100)

    for _ in range(3):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    assert current_usage(player.player_id, RESOURCE_DIALOGUE) == 3

    for _ in range(10):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    assert current_usage(player.player_id, RESOURCE_DIALOGUE) == 13


def test_consume_returns_the_new_usage(db_session, tier, player):
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 10)

    assert consume(db_session, player.player_id, RESOURCE_DIALOGUE) == 1
    assert consume(db_session, player.player_id, RESOURCE_DIALOGUE) == 2


# ── 邊界：用滿 ≠ 超過 ─────────────────────────────────────────────────

def test_exactly_reaching_the_limit_is_allowed(db_session, tier, player):
    """
    AC：上限 N 時第 N 次成功、第 N+1 次被擋。這條擋的是 off-by-one。
    """
    limit = 50
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, limit)

    for _ in range(limit - 1):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)
    assert current_usage(player.player_id, RESOURCE_DIALOGUE) == limit - 1

    # 第 50 次：剛好用滿，應該成功。
    assert consume(db_session, player.player_id, RESOURCE_DIALOGUE) == limit

    # 第 51 次：超過，被擋。
    with pytest.raises(QuotaExceededError):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)


def test_blocked_request_is_not_counted(db_session, tier, player):
    """
    🔒 被擋下的請求**不記帳**。

    這條是 Lua 腳本存在的理由。用 `INCR` 之後再比對的話，這裡會看到 3——
    被拒絕的那次仍然把計數器推上去了，玩家等於被多扣一格。
    """
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 2)

    consume(db_session, player.player_id, RESOURCE_DIALOGUE)
    consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    with pytest.raises(QuotaExceededError):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    assert current_usage(player.player_id, RESOURCE_DIALOGUE) == 2


# ── 上限來自設定 ───────────────────────────────────────────────────────

def test_limit_comes_from_the_database_not_the_code(db_session, tier, player):
    """
    AC：上限值可由設定調整。把上限設成 2，第三次就該被擋。

    這也是那張表的整個設計目的——改資料就生效，不用重新部署。
    """
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 2)

    consume(db_session, player.player_id, RESOURCE_DIALOGUE)
    consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    with pytest.raises(QuotaExceededError):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)


def test_missing_limit_configuration_fails_loudly(db_session, tier, player):
    """
    查不到上限時硬失敗，**不當成無限制放行**。

    當成無限制的話，一個資源名稱的 typo 就會把整層配額靜默關掉，而不會有任何
    東西變紅——那是最糟的失效方式。
    """
    with pytest.raises(UnknownQuotaResourceError):
        consume(db_session, player.player_id, "a_resource_nobody_configured")


# ── 跨日重置（Asia/Taipei）─────────────────────────────────────────────

def test_quota_resets_at_taipei_midnight(db_session, tier, player):
    """
    AC：以 Asia/Taipei 午夜為界重置。

    ⚠️ 兩個注入的時間點**其 UTC 日期是同一天**（3/10 15:00Z 與 3/10 23:00Z），
    但台北日期分別是 3/10 與 3/11。用 UTC 日期算的實作在這裡會失敗，而用真實
    時鐘的測試則會在 UTC 16:00 前後給出不同結果——#15 就踩過這個坑。
    """
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 2)

    consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_YESTERDAY_2300)
    consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_YESTERDAY_2300)

    with pytest.raises(QuotaExceededError):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_YESTERDAY_2300)

    # 台北已經是隔天了，額度重置。
    assert consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_TODAY_0700) == 1
    assert current_usage(player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_TODAY_0700) == 1


def test_yesterdays_usage_is_still_visible_under_yesterdays_date(db_session, tier, player):
    """跨日不是把舊計數清掉，是換一個 key。舊的那天仍然查得到，直到 TTL 到期。"""
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 5)

    consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_YESTERDAY_2300)
    consume(db_session, player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_TODAY_0700)

    assert current_usage(player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_YESTERDAY_2300) == 1
    assert current_usage(player.player_id, RESOURCE_DIALOGUE, now=_TAIPEI_TODAY_0700) == 1


def test_next_taipei_midnight_is_the_upcoming_one():
    reset = next_taipei_midnight(_TAIPEI_YESTERDAY_2300)

    # 台北 3/10 23:00 的下一個午夜是 3/11 00:00（一小時後）。
    assert reset.astimezone(timezone.utc) == datetime(2026, 3, 10, 16, 0, tzinfo=timezone.utc)


# ── 隔離 ───────────────────────────────────────────────────────────────

def test_resources_are_counted_independently(db_session, tier, player):
    """AC：對話用滿不影響拍照辨識額度。"""
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 1)
    _set_limit(db_session, tier, RESOURCE_LANDMARK_RECOGNITION, 1)

    consume(db_session, player.player_id, RESOURCE_DIALOGUE)
    with pytest.raises(QuotaExceededError):
        consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    assert consume(db_session, player.player_id, RESOURCE_LANDMARK_RECOGNITION) == 1


def test_quota_is_isolated_per_player(db_session, tier, unique_device_id):
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 1)

    player_a = models.Player(device_id=f"{unique_device_id}-a", usage_tier_id=tier)
    player_b = models.Player(device_id=f"{unique_device_id}-b", usage_tier_id=tier)
    db_session.add_all([player_a, player_b])
    db_session.commit()

    try:
        consume(db_session, player_a.player_id, RESOURCE_DIALOGUE)
        with pytest.raises(QuotaExceededError):
            consume(db_session, player_a.player_id, RESOURCE_DIALOGUE)

        assert consume(db_session, player_b.player_id, RESOURCE_DIALOGUE) == 1
        assert current_usage(player_a.player_id, RESOURCE_DIALOGUE) == 1
    finally:
        db_session.delete(player_a)
        db_session.delete(player_b)
        db_session.commit()


# ── 併發不超賣 ─────────────────────────────────────────────────────────

def test_concurrent_consume_does_not_oversell(db_session, tier, player):
    """
    🔒 剩 1 格額度、兩個執行緒同時消耗 → **恰好一個成功**。

    ⚠️ 這條必須用真的執行緒。單執行緒測試證明不了任何事——「先查再寫」的實作
    在單執行緒下永遠是對的，只有在真的併發時才會雙雙查到「還有額度」然後一起
    通過（同 #16 共鳴入帳的教訓）。

    每個執行緒用**自己的 DB session**：SQLAlchemy 的 session 不是 thread-safe，
    共用一個的話這個測試會因為 session 狀態混亂而失敗，看起來像配額有問題，
    其實是測試自己寫錯。
    """
    _set_limit(db_session, tier, RESOURCE_DIALOGUE, 3)

    # 先用掉 2 格，剩下 1 格。
    consume(db_session, player.player_id, RESOURCE_DIALOGUE)
    consume(db_session, player.player_id, RESOURCE_DIALOGUE)

    def attempt() -> bool:
        session = SessionLocal()
        try:
            consume(session, player.player_id, RESOURCE_DIALOGUE)
            return True
        except QuotaExceededError:
            return False
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(results) == [False, True], f"應該恰好一成一敗，實際是 {results}"
    assert current_usage(player.player_id, RESOURCE_DIALOGUE) == 3


# ── 429 回應 ───────────────────────────────────────────────────────────

def test_quota_error_maps_to_429():
    reset_at = datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc)
    response = quota_exceeded_response(
        QuotaExceededError(resource_type=RESOURCE_DIALOGUE, reset_at=reset_at)
    )

    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_429_body_does_not_leak_other_players_or_internal_config():
    """
    AC：回應只含該玩家自己的狀態。

    不含其他 `player_id`、全站統計，**也不含上限值**——上限是分級設定，回傳它
    等於讓任何人用一次超額請求就問出我們的商業分級。
    """
    import json

    reset_at = datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc)
    response = quota_exceeded_response(
        QuotaExceededError(resource_type=RESOURCE_DIALOGUE, reset_at=reset_at)
    )
    body = json.loads(response.body)

    assert set(body) == {"detail", "resource", "reset_at"}
    for leaky in ["player_id", "limit", "tier", "usage", "total"]:
        assert leaky not in body


# ── 既有行為不回歸 ─────────────────────────────────────────────────────

def test_default_tier_still_resolves(db_session):
    """`default_tier_id` 是既有行為，這次改動不該動到它。"""
    assert default_tier_id(db_session)
