"""
配額分級的讀取與扣用（AC8／#32）。

配額分級（`usage_tiers`）的實際數字屬產品決策（v2.1 §14，Phase 7 才拍板），
這支只建立機制與可設定的預設值，不決定商業分級——測試與呼叫端都不該把
任何分級數字寫死。

計數器走 Redis，不落地 Postgres：key 用 `quota:{player_id}:{resource_type}:
{Asia/Taipei 日期}` 命名，換日自然變成新 key，不需要排程 job 去重置
（跟 `redis_client.py` 對話 session 的命名慣例是同一個精神）。
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.redis_client import redis_client
from app.modules.body.models import Player, UsageTier, UsageTierLimit
from app.modules.body.quests import TAIPEI, taipei_today

# Redis key 的存活時間，純粹是記憶體整理用——實際的「重置」靠日期進 key
# 名稱達成，跟這個 TTL 無關。設兩天是為了給時鐘漂移留餘裕，數字本身不重要。
_QUOTA_KEY_TTL_SECONDS = 60 * 60 * 48


class QuotaExceededError(Exception):
    """
    可被 API 層轉為 `429` 的例外（見 `app.main` 的 exception handler）。

    只帶「這個玩家自己」看得到的資訊——資源類型、他自己這個分級的上限、
    重置時間。**不帶** `tier_id`、`player_id` 或任何其他玩家的數字，
    429 回應內容不該洩漏這些（AC：不洩漏其他玩家用量資訊）。
    """

    def __init__(self, *, resource_type: str, limit: int, reset_at: datetime):
        self.resource_type = resource_type
        self.limit = limit
        self.reset_at = reset_at
        super().__init__(f"quota exceeded for {resource_type} (limit={limit})")


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


class NoResourceLimitError(RuntimeError):
    """
    這個分級沒有設定該資源類型的上限。

    跟 `NoDefaultUsageTierError` 同一個立場：硬失敗好過偷偷放行無限額度。
    加一種新配額只需要 INSERT 一筆 `usage_tier_limits`（見既有測試），忘了
    這一步不該被消化成「這個玩家對這項資源沒有限制」。
    """


def _quota_key(player_id: uuid.UUID | str, resource_type: str, today) -> str:
    return f"quota:{player_id}:{resource_type}:{today.isoformat()}"


def _next_taipei_midnight(today) -> datetime:
    return datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=TAIPEI)


def consume(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    resource_type: str,
    amount: int = 1,
    now: datetime | None = None,
) -> int:
    """
    扣用一筆配額，回傳扣用後的當日累計用量。

    超額時拋 `QuotaExceededError`，**用量維持扣用前的值**——擋下的請求不
    記帳。做法是「先原子遞增、超過才回滾」，不是「先查再寫」：`INCRBY` 在
    Redis 裡本身是原子操作，兩個並行呼叫會被序列化執行，所以只會有一個
    看到「超過」而回滾，不會出現雙雙通過的競態（同 #16 共鳴入帳的教訓，
    那裡是靠資料庫約束，這裡是靠 Redis 單執行緒的原子指令）。
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)

    player = db.query(Player).filter_by(player_id=uuid.UUID(str(player_id))).first()
    if player is None:
        raise ValueError(f"player {player_id} 不存在")

    limit = limits_for_tier(db, player.usage_tier_id).get(resource_type)
    if limit is None:
        raise NoResourceLimitError(
            f"分級 {player.usage_tier_id} 沒有設定 {resource_type} 的上限"
        )

    key = _quota_key(player_id, resource_type, today)
    new_value = redis_client.incrby(key, amount)
    redis_client.expire(key, _QUOTA_KEY_TTL_SECONDS)

    if new_value > limit:
        redis_client.decrby(key, amount)
        raise QuotaExceededError(
            resource_type=resource_type, limit=limit, reset_at=_next_taipei_midnight(today)
        )

    return new_value
