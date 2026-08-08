"""
Ticket #32．配額機制與 429（usage_tiers）。

驗收標準對照見 GitHub issue #32。對真實 Postgres／Redis 跑，不 mock。

⚠️ 所有數字都從資料庫動態讀（`limits_for_tier`），不寫死商業分級——issue
preamble 明訂測試不得寫死 `closed_beta` 的實際數字，那些數字是產品決策
（Phase 7 才拍板），改資料庫不該讓這些測試變紅。
"""
import asyncio
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import pytest

from app.core.database import SessionLocal
from app.core.redis_client import redis_client
from app.main import quota_exceeded_handler
from app.modules.body import models
from app.modules.body.quests import TAIPEI, taipei_today
from app.modules.body.quota import (
    NoResourceLimitError,
    QuotaExceededError,
    _quota_key,
    consume,
    default_tier_id,
    limits_for_tier,
)

DIALOGUE = "dialogue_calls_daily"
LANDMARK = "landmark_recognition_daily"


@pytest.fixture
def player_id(client, unique_device_id):
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    player_id = uuid.UUID(body["player_id"])
    yield player_id
    for key in redis_client.keys(f"quota:{player_id}:*"):
        redis_client.delete(key)


def _dialogue_limit(db_session) -> int:
    return limits_for_tier(db_session, default_tier_id(db_session))[DIALOGUE]


# ── 超額拒絕，不記帳（AC1） ──────────────────────────────────────────────


def test_exceeding_limit_raises_and_does_not_record_the_blocked_attempt(
    db_session, player_id
):
    limit = _dialogue_limit(db_session)
    for _ in range(limit):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    # 被擋的那次沒有記帳：再消耗一次應該仍然被擋（用量沒有變成 limit+1 又
    # 悄悄漲到 limit+2），且下面 AC3 的邊界測試會再驗一次確切數字。
    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)


# ── 未超額累計（AC2） ────────────────────────────────────────────────────


def test_consume_below_limit_succeeds_and_accumulates(db_session, player_id):
    for _ in range(3):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    result = consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)
    assert result == 4

    for _ in range(9):
        result = consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)
    assert result == 13


# ── 剛好用完的邊界（AC3） ────────────────────────────────────────────────


def test_boundary_exact_limit_then_next_is_rejected(db_session, player_id):
    limit = _dialogue_limit(db_session)

    for _ in range(limit - 1):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    # 第 limit 次：剛好用完，應該成功。
    result = consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)
    assert result == limit

    # 第 limit+1 次：應該被擋，用量維持在 limit。
    with pytest.raises(QuotaExceededError) as exc_info:
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)
    assert exc_info.value.limit == limit


# ── 以 Asia/Taipei 午夜為界重置（AC4） ───────────────────────────────────


def test_resets_at_taipei_midnight_not_utc_midnight(db_session, player_id):
    """
    台北昨天 23:00 用滿額度；台北今天 07:00（其 UTC 仍是昨天）應該已經重置。
    時間全部注入，不靠真實時鐘——#15 曾經因為用真實時鐘而在台北午夜前後
    表現不一致。
    """
    limit = _dialogue_limit(db_session)
    yesterday_2300_taipei = datetime(2026, 1, 15, 23, 0, tzinfo=TAIPEI)
    today_0700_taipei = datetime(2026, 1, 16, 7, 0, tzinfo=TAIPEI)

    for _ in range(limit):
        consume(
            db_session,
            player_id=player_id,
            resource_type=DIALOGUE,
            amount=1,
            now=yesterday_2300_taipei,
        )

    result = consume(
        db_session, player_id=player_id, resource_type=DIALOGUE, amount=1, now=today_0700_taipei
    )
    assert result == 1


# ── 上限可設定，不寫死（AC5） ─────────────────────────────────────────────


def test_limit_is_configurable_via_database_not_hardcoded(db_session, player_id, client):
    """
    把這個玩家的分級換成一個上限=2 的自訂分級，驗證 `consume` 完全照著資料庫
    的值走——production 程式碼裡沒有任何數字字面量可以讓這個測試繞過去。
    """
    custom_tier_id = f"t-{uuid.uuid4().hex[:8]}"
    db_session.add(models.UsageTier(tier_id=custom_tier_id, display_name="測試用低額度"))
    db_session.add(
        models.UsageTierLimit(tier_id=custom_tier_id, resource_type=DIALOGUE, limit_value=2)
    )
    db_session.flush()  # tier 要先進資料庫，players 的外鍵才能指過去

    player = db_session.query(models.Player).filter_by(player_id=player_id).one()
    player.usage_tier_id = custom_tier_id
    db_session.commit()

    try:
        assert consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1) == 1
        assert consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1) == 2
        with pytest.raises(QuotaExceededError):
            consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)
    finally:
        # players 還外鍵指著這個自訂分級，要先讓玩家換回預設分級才能刪它。
        player.usage_tier_id = default_tier_id(db_session)
        db_session.commit()
        db_session.query(models.UsageTierLimit).filter_by(tier_id=custom_tier_id).delete()
        db_session.query(models.UsageTier).filter_by(tier_id=custom_tier_id).delete()
        db_session.commit()


def test_missing_limit_configuration_fails_loudly_not_silently_unlimited(
    db_session, player_id
):
    """加了新資源類型卻忘記設上限時，要硬失敗，不能被當成「這項無限額度」。"""
    with pytest.raises(NoResourceLimitError):
        consume(
            db_session,
            player_id=player_id,
            resource_type=f"made_up_{uuid.uuid4().hex[:8]}",
            amount=1,
        )


# ── 併發不超賣（AC6） ────────────────────────────────────────────────────


def test_concurrent_consume_does_not_oversell(db_session, player_id):
    limit = _dialogue_limit(db_session)

    for _ in range(limit - 1):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    def _attempt():
        session = SessionLocal()
        try:
            consume(session, player_id=player_id, resource_type=DIALOGUE, amount=1)
            return "ok"
        except QuotaExceededError:
            return "blocked"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_attempt) for _ in range(2)]
        wait(futures)
        outcomes = [f.result() for f in futures]

    assert sorted(outcomes) == ["blocked", "ok"]

    today = taipei_today(datetime.now(TAIPEI))
    final_value = int(redis_client.get(_quota_key(player_id, DIALOGUE, today)))
    assert final_value == limit


# ── 不同資源類型獨立（AC7） ───────────────────────────────────────────────


def test_different_resource_types_are_independent(db_session, player_id):
    limit = _dialogue_limit(db_session)
    for _ in range(limit):
        consume(db_session, player_id=player_id, resource_type=DIALOGUE, amount=1)

    result = consume(db_session, player_id=player_id, resource_type=LANDMARK, amount=1)
    assert result == 1


# ── 配額按玩家隔離（AC8） ─────────────────────────────────────────────────


def test_quota_is_isolated_per_player(db_session, client, unique_device_id):
    body_a = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    player_a = uuid.UUID(body_a["player_id"])

    body_b = client.post(
        "/api/v1/players", json={"device_id": f"other-{unique_device_id}"}
    ).json()
    player_b = uuid.UUID(body_b["player_id"])

    limit = _dialogue_limit(db_session)
    for _ in range(limit):
        consume(db_session, player_id=player_a, resource_type=DIALOGUE, amount=1)

    result = consume(db_session, player_id=player_b, resource_type=DIALOGUE, amount=1)
    assert result == 1

    with pytest.raises(QuotaExceededError):
        consume(db_session, player_id=player_a, resource_type=DIALOGUE, amount=1)

    for pid in (player_a, player_b):
        for key in redis_client.keys(f"quota:{pid}:*"):
            redis_client.delete(key)


# ── 429 不洩漏其他玩家資訊（AC9） ─────────────────────────────────────────


def test_429_response_only_contains_the_caller_s_own_status():
    exc = QuotaExceededError(
        resource_type=DIALOGUE, limit=50, reset_at=datetime(2026, 1, 16, 0, 0, tzinfo=TAIPEI)
    )

    response = asyncio.run(quota_exceeded_handler(None, exc))

    assert response.status_code == 429
    body = json.loads(response.body)

    assert body["resource_type"] == DIALOGUE
    assert body["limit"] == 50
    assert "reset_at" in body
    assert "player_id" not in body
    assert "tier_id" not in body
