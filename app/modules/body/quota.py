"""
配額機制（AC8／#32）。

分級與上限住在 Postgres（`usage_tiers` / `usage_tier_limits`），**計數器走
Redis**，key 含 Asia/Taipei 日期。這個分工在 migration `0004` 就定了：上限是
需要被 review 的設定，用量是每天丟掉的高頻計數。

## 位置：`/dialogue` 的第一道關卡

排在 **B4 安全檢查之前**（SDD v1 §3 `check_and_consume_quota` 的註解）。
理由跟 B4 排在 B2 之前一樣——被擋下的請求不該讓下游付出任何成本，而 B4 自己
就要呼叫一次模型。

## 原子性：Lua，不是 INCR 後再比對

`INCR` 之後才發現超額的話，那一次被擋下的請求**已經記帳了**，用量會從 50 變成
51。AC 明訂「擋下的請求不該記帳」，所以檢查與累加必須在同一個原子操作裡。

Redis 的 Lua script 執行期間不會被其他指令插入，所以 `_CONSUME_SCRIPT` 就是那個
原子操作。這也是「併發不超賣」的依靠——**不可以「先查再寫」**，兩個請求會同時
查到「還有額度」然後雙雙通過（同 #16 共鳴入帳的教訓）。

## ⚠️ 資源名稱與 issue #32 的 AC 不同

AC 寫 `dialogue_turns` / `dialogue_turns_daily` / `landmark_recognition_calls`，
但 migration `0004` 實際寫進 `usage_tier_limits.resource_type` 的是：

    dialogue_calls_daily · daily_tokens · prompt_max_chars
    api_rate_per_minute · landmark_recognition_daily

**以資料庫為準。** 上限值的真相是那張表，程式碼裡不該有第二套名字——那樣的話
改資料就不會生效，而這張表的整個設計目的就是「改資料不用重新部署」。
"""
from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.redis_client import redis_client
from app.modules.body.models import UsageTier, UsageTierLimit
from app.modules.body.taipei import TAIPEI, taipei_today

# 資源名稱常數。呼叫端用這些而不是字面字串，避免打錯字造成「查不到上限」
# 而被靜默當成無限制。
RESOURCE_DIALOGUE = "dialogue_calls_daily"

# ⚠️ 2026-08-19：地標拍照辨識整條路已拆除（`/landmarks/{id}/photo` 端點與
# `landmark_recognition.py` 都刪了），**這個常數已經沒有呼叫端**。
#
# 名字刻意留著不刪：`usage_tier_limits` 裡仍然有以這個字串為 key 的列，而
# `consume()` 對「查不到上限」的處置是**靜默當成無限制**。哪天有人重新接上
# 影像類的端點、順手寫了一個拼法不同的資源名，那道上限就等於不存在，而且
# 不會有任何錯誤訊息。留著這個常數是為了讓那個人先撞到它。
#
# 真要清掉的話，得連 `usage_tier_limits` 的資料一起遷移，那是一次獨立的決定。
RESOURCE_LANDMARK_RECOGNITION = "landmark_recognition_daily"


class NoDefaultUsageTierError(RuntimeError):
    """
    資料庫裡沒有 `is_default = true` 的分級。

    這不是可以回退的執行期狀況，是資料庫沒有被正確初始化——`players.usage_tier_id`
    是 NOT NULL，沒有預設分級就沒辦法建立任何玩家。硬失敗好過偷偷塞一個
    寫死的 'closed_beta'：那樣的話，正式環境少了那一列時沒有人會發現，
    直到有人想調整預設分級卻發現改資料沒有效果。
    """


def default_tier_id(db: Session) -> str:
    """
    新玩家要指派的分級。

    唯一真相是 `usage_tiers.is_default`，不是程式碼裡的常數。「只能有一筆
    is_default」由 partial unique index 保證（見 0004），所以這裡不需要處理
    「查到兩筆」的情況——那在資料庫層級就寫不進去。
    """
    tier = db.query(UsageTier).filter_by(is_default=True).first()
    if tier is None:
        raise NoDefaultUsageTierError(
            "usage_tiers 沒有 is_default 的列；請確認 migration 0004 已經跑過"
        )
    return tier.tier_id


def limits_for_tier(db: Session, tier_id: str) -> dict[str, int]:
    """某個分級底下所有資源的上限，`{resource_type: limit_value}`。"""
    rows = db.query(UsageTierLimit).filter_by(tier_id=tier_id).all()
    return {row.resource_type: row.limit_value for row in rows}


class QuotaExceededError(Exception):
    """
    配額用盡。**專用型別而不是泛用 Exception**——API 層要靠型別把它轉成 429，
    而 `except Exception` 會連帶吞掉真正的錯誤。

    攜帶的資訊刻意只有「這個玩家自己的狀態」：哪一種資源、什麼時候重置。
    **不含上限值**——那是分級設定，屬於內部組態，玩家不需要也不該從 429 回應
    推敲出我們的商業分級。
    """

    def __init__(self, resource_type: str, reset_at: datetime):
        self.resource_type = resource_type
        self.reset_at = reset_at
        super().__init__(f"quota exhausted for {resource_type}")


class UnknownQuotaResourceError(RuntimeError):
    """
    這個分級底下查不到該資源的上限。

    **硬失敗，不當成無限制放行。** 查不到上限最可能的原因是打錯資源名稱或
    migration 沒跑完；當成無限制的話，一個 typo 就會把整層配額靜默關掉，而
    沒有任何東西會變紅。
    """


# 檢查與累加的原子操作。
#
# 回傳新的用量；若這次消耗會超過上限則回傳 -1，**且不改變任何值**。
#
# EXPIRE 每次都下：key 的生命週期跟著「距離台北午夜還有多久」走，不是固定 24
# 小時。固定 24 小時的話，台北時間 23:59 建立的 key 會活到隔天 23:59，跨過
# 一次午夜卻沒有重置。
_CONSUME_SCRIPT = """
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', KEYS[1]) or '0')

if current + amount > limit then
  return -1
end

local updated = redis.call('INCRBY', KEYS[1], amount)
redis.call('EXPIRE', KEYS[1], ttl)
return updated
"""


def _now(now: datetime | None) -> datetime:
    """
    時間必須可注入。

    ⚠️ 用真實時鐘的話，跨日重置的測試在 UTC 16:00 前後行為會不一樣——#15 就
    踩過這個坑（測試寫好當天全綠，過了台北午夜才爆）。
    """
    return now or datetime.now(timezone.utc)


def _quota_key(player_id, resource_type: str, day) -> str:
    return f"quota:{player_id}:{resource_type}:{day.isoformat()}"


def next_taipei_midnight(now: datetime) -> datetime:
    """下一個台北午夜（含時區）。配額重置的時間點。"""
    local = now.astimezone(TAIPEI)
    tomorrow = local.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=TAIPEI)


def limit_for(db: Session, player_id, resource_type: str) -> int:
    """
    這個玩家目前分級底下，該資源的上限。

    分級從玩家身上查，不從參數傳——呼叫端不該有機會傳錯分級而拿到別人的額度。
    """
    from app.modules.body.models import Player

    player = db.query(Player).filter_by(player_id=uuid_module.UUID(str(player_id))).first()
    if player is None:
        raise UnknownQuotaResourceError(f"找不到玩家 {player_id}")

    row = (
        db.query(UsageTierLimit)
        .filter_by(tier_id=player.usage_tier_id, resource_type=resource_type)
        .first()
    )
    if row is None:
        raise UnknownQuotaResourceError(
            f"分級 {player.usage_tier_id} 沒有 {resource_type} 的上限設定"
        )
    return row.limit_value


def current_usage(player_id, resource_type: str, *, now: datetime | None = None) -> int:
    """今日（Asia/Taipei）已使用量。沒有紀錄時是 0。"""
    day = taipei_today(_now(now))
    raw = redis_client.get(_quota_key(player_id, resource_type, day))
    return int(raw) if raw is not None else 0


def consume(
    db: Session,
    player_id,
    resource_type: str,
    amount: int = 1,
    *,
    now: datetime | None = None,
) -> int:
    """
    消耗配額，回傳消耗後的用量。

    超過上限時拋 `QuotaExceededError`，**且用量維持原值**——被擋下的請求不記帳。

    「用滿」不等於「超過」：上限 50 時第 50 次會成功，第 51 次才被擋。
    """
    moment = _now(now)
    day = taipei_today(moment)
    limit = limit_for(db, player_id, resource_type)

    reset_at = next_taipei_midnight(moment)
    ttl_seconds = max(int((reset_at - moment).total_seconds()), 1)

    updated = redis_client.eval(
        _CONSUME_SCRIPT,
        1,
        _quota_key(player_id, resource_type, day),
        limit,
        amount,
        ttl_seconds,
    )

    if int(updated) < 0:
        raise QuotaExceededError(resource_type=resource_type, reset_at=reset_at)

    return int(updated)
