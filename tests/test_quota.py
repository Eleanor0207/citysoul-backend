"""
Ticket #32．配額機制與 429 (usage_tiers) 單元與整合測試。
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from zoneinfo import ZoneInfo

from app.core.redis_client import redis_client
from app.modules.body import models
from app.modules.body.quota import (
    QuotaExceededError,
    consume_quota,
    get_quota_usage,
    taipei_date_str,
)

TAIPEI = ZoneInfo("Asia/Taipei")
_RESOURCE_DIALOGUE = "dialogue_turns"
_RESOURCE_LANDMARK = "landmark_recognition_calls"


@pytest.fixture
def custom_tier_and_player(db_session, unique_device_id):
    """建立測試用 UsageTier (limit=2) 與 Player。"""
    tier_id = f"tier_{uuid.uuid4().hex[:8]}"
    tier = models.UsageTier(tier_id=tier_id, display_name="測試配額分級", is_default=False)
    db_session.add(tier)
    db_session.commit()

    limit_dialogue = models.UsageTierLimit(tier_id=tier_id, resource_type=_RESOURCE_DIALOGUE, limit_value=2)
    limit_landmark = models.UsageTierLimit(tier_id=tier_id, resource_type=_RESOURCE_LANDMARK, limit_value=5)
    db_session.add_all([limit_dialogue, limit_landmark])
    db_session.commit()

    player = models.Player(device_id=unique_device_id, usage_tier_id=tier_id)
    db_session.add(player)
    db_session.commit()

    yield player, tier_id

    # 清理測試 Redis key
    today_str = taipei_date_str()
    redis_client.delete(f"quota:{player.player_id}:{_RESOURCE_DIALOGUE}:{today_str}")
    redis_client.delete(f"quota:{player.player_id}:{_RESOURCE_LANDMARK}:{today_str}")

    db_session.delete(player)
    db_session.delete(limit_dialogue)
    db_session.delete(limit_landmark)
    db_session.delete(tier)
    db_session.commit()


# ── 1. 成功扣款與累計測試 ──────────────────────────────────────────────────

def test_quota_normal_increment(db_session, custom_tier_and_player):
    player, _ = custom_tier_and_player
    pid = player.player_id

    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 0
    val1 = consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)
    assert val1 == 1
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 1

    val2 = consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)
    assert val2 == 2
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 2


# ── 2. 邊界測試與超額 429 例外 ─────────────────────────────────────────────

def test_quota_boundary_and_exceeded_error(db_session, custom_tier_and_player):
    player, _ = custom_tier_and_player
    pid = player.player_id

    # 上限為 2
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)  # 剛好達上限 2
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 2

    # 第 3 次應拋出 QuotaExceededError (429)
    with pytest.raises(QuotaExceededError) as exc_info:
        consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)

    err = exc_info.value
    assert err.status_code == 429
    assert "dialogue_turns" in err.detail

    # 驗證被擋下的請求不記帳，用量仍維持 2
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 2


# ── 3. Asia/Taipei 午夜重置測試 (時間注入) ─────────────────────────────────

def test_quota_taipei_midnight_reset(db_session, custom_tier_and_player):
    player, _ = custom_tier_and_player
    pid = player.player_id

    # 注入時間：台北 yesterday 23:00
    now_taipei_yesterday = datetime.now(TAIPEI) - timedelta(days=1)
    yesterday_str = now_taipei_yesterday.strftime("%Y-%m-%d")

    # 用滿昨天配額 (limit=2)
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1, now=now_taipei_yesterday)
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1, now=now_taipei_yesterday)
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE, now=now_taipei_yesterday) == 2

    # 今天呼叫應重置為 1
    now_taipei_today = datetime.now(TAIPEI)
    today_val = consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1, now=now_taipei_today)
    assert today_val == 1
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE, now=now_taipei_today) == 1

    # 清理舊 Redis key
    redis_client.delete(f"quota:{pid}:{_RESOURCE_DIALOGUE}:{yesterday_str}")


# ── 4. 資源類型獨立性與玩家隔離測試 ─────────────────────────────────────────

def test_quota_resource_type_independence(db_session, custom_tier_and_player):
    player, _ = custom_tier_and_player
    pid = player.player_id

    # 對話用語滿 (limit=2)
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 2)

    # 地標辨識應可正常呼叫 (limit=5)
    val = consume_quota(db_session, pid, _RESOURCE_LANDMARK, 1)
    assert val == 1
    assert get_quota_usage(pid, _RESOURCE_LANDMARK) == 1


def test_quota_player_isolation(db_session, custom_tier_and_player, unique_device_id):
    player_a, tier_id = custom_tier_and_player
    pid_a = player_a.player_id

    # 建立 Player B 屬於同一 Tier
    player_b = models.Player(device_id=f"dev-b-{uuid.uuid4()}", usage_tier_id=tier_id)
    db_session.add(player_b)
    db_session.commit()

    pid_b = player_b.player_id

    # A 用滿配額
    consume_quota(db_session, pid_a, _RESOURCE_DIALOGUE, 2)

    # B 不受影響
    val_b = consume_quota(db_session, pid_b, _RESOURCE_DIALOGUE, 1)
    assert val_b == 1
    assert get_quota_usage(pid_b, _RESOURCE_DIALOGUE) == 1

    # 清理 B
    today_str = taipei_date_str()
    redis_client.delete(f"quota:{pid_b}:{_RESOURCE_DIALOGUE}:{today_str}")
    db_session.delete(player_b)
    db_session.commit()


# ── 5. 併發防超賣原子性測試 ────────────────────────────────────────────────

def test_quota_concurrency_atomic_protection(db_session, custom_tier_and_player):
    player, _ = custom_tier_and_player
    pid = player.player_id

    # 先消耗 1 格（剩 1 格）
    consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)

    success_count = 0
    fail_count = 0

    def _try_consume():
        try:
            # 每個線程使用新的相依或全域 consume
            consume_quota(db_session, pid, _RESOURCE_DIALOGUE, 1)
            return True
        except QuotaExceededError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_try_consume)
        f2 = executor.submit(_try_consume)
        r1, r2 = f1.result(), f2.result()

    results = [r1, r2]
    assert results.count(True) == 1, "併發扣款恰好應有一筆成功"
    assert results.count(False) == 1, "併發扣款恰好應有一筆被擋"
    assert get_quota_usage(pid, _RESOURCE_DIALOGUE) == 2, "最終用量剛好達到上限 2，無超賣"
