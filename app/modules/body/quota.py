from datetime import datetime, timezone
import uuid
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.redis_client import redis_client
from app.modules.body.models import Player, UsageTier, UsageTierLimit

TAIPEI = ZoneInfo("Asia/Taipei")
QUOTA_TTL_SECONDS = 86400 * 2  # 快取保留 48 小時後自動清理

LUA_CONSUME_QUOTA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('get', key) or '0')
if current + amount > limit then
    return -1
end
local new_val = redis.call('incrby', key, amount)
if current == 0 then
    redis.call('expire', key, ttl)
end
return new_val
"""


class NoDefaultUsageTierError(RuntimeError):
    """
    資料庫裡沒有 `is_default = true` 的分級。

    這不是可以回退的執行期狀況，是資料庫沒有被正確初始化——`players.usage_tier_id`
    是 NOT NULL，沒有預設分級就沒辦法建立任何玩家。硬失敗好過偷偷塞一個
    寫死的 'closed_beta'：那樣的話，正式環境少了那一列時沒有人會發現，
    直到有人想調整預設分級卻發現改資料沒有效果。
    """


class QuotaExceededError(HTTPException):
    """配額超過上限時拋出的例外（對應 HTTP 429）。"""

    def __init__(self, resource: str, limit: int, current: int):
        super().__init__(
            status_code=429,
            detail=f"daily limit for {resource} reached (limit: {limit})",
        )
        self.resource = resource
        self.limit = limit
        self.current = current


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


def taipei_date_str(now: datetime | None = None) -> str:
    """計算以 Asia/Taipei 為基準的日期字串 (YYYY-MM-DD)。"""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(TAIPEI).strftime("%Y-%m-%d")


def _quota_key(player_id: str, resource: str, date_str: str) -> str:
    return f"quota:{player_id}:{resource}:{date_str}"


def get_quota_usage(
    player_id: uuid.UUID | str, resource: str, now: datetime | None = None
) -> int:
    """查詢玩家當日某項資源的使用量。"""
    date_str = taipei_date_str(now)
    key = _quota_key(str(player_id), resource, date_str)
    try:
        val = redis_client.get(key)
        return int(val) if val is not None else 0
    except Exception:
        return 0


def consume_quota(
    db: Session,
    player_id: uuid.UUID | str,
    resource: str,
    amount: int = 1,
    now: datetime | None = None,
) -> int:
    """
    原子化檢查並扣減配額（AC8／#32）。

    1. 讀取玩家 `usage_tier_id` 及其配額上限。
    2. 依 `Asia/Taipei` 日期產出 Redis 鍵值。
    3. 執行 Redis Lua 腳本原子化比對與累計。
    4. 若超過上限，拋出 `QuotaExceededError` (HTTP 429)，且用量不增加。
    """
    pid = uuid.UUID(str(player_id))
    player = db.query(Player).filter_by(player_id=pid).first()
    if player is None:
        raise HTTPException(status_code=404, detail="player not found")

    tier_limits = limits_for_tier(db, player.usage_tier_id)
    if resource not in tier_limits:
        # 未限制配額的資源種類放行
        return get_quota_usage(player_id, resource, now)

    limit = tier_limits[resource]
    date_str = taipei_date_str(now)
    key = _quota_key(str(pid), resource, date_str)

    try:
        res = redis_client.eval(
            LUA_CONSUME_QUOTA, 1, key, limit, amount, QUOTA_TTL_SECONDS
        )
    except Exception as err:
        # Redis 不可用時進行放行保護，不中斷體驗
        return get_quota_usage(player_id, resource, now)

    if res == -1:
        current_val = get_quota_usage(player_id, resource, now)
        raise QuotaExceededError(resource=resource, limit=limit, current=current_val)

    return int(res)

