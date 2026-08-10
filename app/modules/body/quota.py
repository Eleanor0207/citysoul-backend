"""
配額分級的讀取與扣用（AC8／issue #32）。

分級（哪個玩家屬於哪一級、每一級每種資源的上限）是唯一真相都在 Postgres 的
`usage_tiers`／`usage_tier_limits`（見 migration 0004）。**計數器不落地
Postgres，走 Redis**——同一份資料被寫入的頻率（每次對話）遠高於被讀取分級
設定的頻率，且不需要跨請求的交易保證，Redis 的原子 `INCRBY` 已經足夠。

## 這裡實作的是「每日」配額，不是任何週期的配額

`usage_tier_limits` 目前有 5 種 `resource_type`，但不是每一種都適合套用這支
模組的重置規則：`dialogue_calls_daily`／`landmark_recognition_daily` 是以
Asia/Taipei 午夜為界重置的每日配額，正是 `consume()` 要處理的對象；
`api_rate_per_minute` 是**分鐘**級速率限制，`prompt_max_chars` 是單次請求的
靜態長度上限（已經由 `DialogueRequest` 的 pydantic 驗證擋著）——兩者都不是
「累計用量、按時間週期重置」這種形狀，套用 `consume()` 的每日重置邏輯在語意
上是錯的，需要另外的機制，不屬於本票範圍。

## 併發安全靠 Redis `INCRBY` 是原子操作，不是先查再寫

跟 `resonance.py`／`quests.py` 的教訓同一個道理：「先讀目前用量、比對上限、
沒超過才寫入」在並行下會超賣——兩個請求可以同時讀到「還有額度」。這裡反過來
做：**先加、加完才檢查**。`INCRBY` 本身是 Redis 單執行緒模型保證的原子操作，
兩個並行呼叫一定會拿到不同的回傳值（例如 50 與 51），不會有兩者都拿到 50 的
情況。加完發現超過上限，用 `DECRBY` 補回去再拒絕——補償式回退，而不是先鎖
再判斷。
"""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.redis_client import redis_client
from app.modules.body.models import Player, UsageTier, UsageTierLimit
from app.modules.body.quests import TAIPEI, taipei_today

# key 的存活時間，只是避免舊 key 無限累積，不影響配額的正確性——正確性完全
# 靠「key 本身就帶著日期」：換日之後是全新的 key，用量自然從 0 開始，不需要
# 任何重置動作。兩天的緩衝讓時區交界前後的除錯／觀測還讀得到前一天的數字。
_KEY_TTL_SECONDS = 2 * 24 * 60 * 60


class NoDefaultUsageTierError(RuntimeError):
    """
    資料庫裡沒有 `is_default = true` 的分級。

    這不是可以回退的執行期狀況，是資料庫沒有被正確初始化——`players.usage_tier_id`
    是 NOT NULL，沒有預設分級就沒辦法建立任何玩家。硬失敗好過偷偷塞一個
    寫死的 'closed_beta'：那樣的話，正式環境少了那一列時沒有人會發現，
    直到有人想調整預設分級卻發現改資料沒有效果。
    """


class QuotaExceededError(Exception):
    """
    配額專用例外（issue #32 AC1）：API 層（未來 #42／#45）能依此轉成 `429`。

    只帶這個玩家自己的資訊——`limit` 與 `reset_at`，不含其他玩家的
    `player_id`、全站統計或內部設定值（AC9），呼叫端可以直接拿這兩個欄位
    組回應，不需要另外查一次資料庫，也就不會不小心多帶出不該洩漏的東西。
    """

    def __init__(self, *, resource: str, limit: int, reset_at: datetime) -> None:
        self.resource = resource
        self.limit = limit
        self.reset_at = reset_at
        super().__init__(f"配額已用滿：{resource}（上限 {limit}，{reset_at} 重置）")


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


def _limit_for_player(db: Session, *, player_id: uuid.UUID | str, resource: str) -> int:
    """
    這個玩家、這種資源的上限。上限值完全來自 `usage_tier_limits`，呼叫端
    程式碼裡不出現任何數字字面量（issue #32 AC5）。

    玩家的分級沒有設定這個 `resource_type` 上限列，視為「這一級不能用這個
    資源」——直接當作上限 0，而不是預設放行。配額表是白名單，不是黑名單：
    忘記幫某個分級開某種資源的權限，後果應該是被擋下來去補資料，不是悄悄
    無限放行。
    """
    player = db.query(Player).filter_by(player_id=uuid.UUID(str(player_id))).first()
    if player is None:
        raise ValueError(f"找不到玩家：{player_id}")
    return limits_for_tier(db, player.usage_tier_id).get(resource, 0)


def _quota_key(player_id: uuid.UUID | str, resource: str, today: date) -> str:
    return f"quota:{player_id}:{resource}:{today.isoformat()}"


def _next_taipei_midnight_utc(today: date) -> datetime:
    """換算成 UTC 時間，方便呼叫端直接拿去跟其他 UTC 時間比較或序列化。"""
    next_midnight_taipei = datetime.combine(today + timedelta(days=1), datetime.min.time(), TAIPEI)
    return next_midnight_taipei.astimezone(timezone.utc)


def current_usage(player_id: uuid.UUID | str, resource: str, *, now: datetime | None = None) -> int:
    """查詢目前用量，不消耗額度。用量不存在（今天還沒用過）時回傳 0。"""
    now = now or datetime.now(timezone.utc)
    key = _quota_key(player_id, resource, taipei_today(now))
    raw = redis_client.get(key)
    return int(raw) if raw is not None else 0


def consume(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    resource: str,
    amount: int = 1,
    now: datetime | None = None,
) -> int:
    """
    消耗一筆配額。成功回傳消耗後的用量；超過上限拋 `QuotaExceededError`，
    用量維持在拋出例外之前的值，不會把這次沒被允許的請求也算進去（AC1）。

    `now` 可注入（AC4）：時區判定沿用 `quests.taipei_today()`，不另寫一套；
    真的用系統時間跑測試的話，行為會因為測試執行的當下是台北時間幾點而不同
    ——#15 已經踩過這個坑。
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)
    limit = _limit_for_player(db, player_id=player_id, resource=resource)
    key = _quota_key(player_id, resource, today)

    new_value = redis_client.incrby(key, amount)
    # NX：key 已經有 TTL 的話不覆蓋。每次 consume 都呼叫一次，而不是只在
    # 「剛建立」時呼叫一次，是因為 INCRBY 的回傳值不能拿來可靠判斷「這個 key
    # 是不是剛建立的」——`new_value == amount` 這種判斷在用量剛好等於 amount
    # 的巧合下會誤判。EXPIRE NX 本身是原子操作，重複呼叫沒有副作用，比起
    # 額外一次 TTL 查詢還更省。
    redis_client.expire(key, _KEY_TTL_SECONDS, nx=True)

    if new_value > limit:
        redis_client.decrby(key, amount)
        raise QuotaExceededError(
            resource=resource, limit=limit, reset_at=_next_taipei_midnight_utc(today)
        )

    return new_value
