"""
配額分級的讀取（AC8／#32）。

這支目前只做**分級的指派與查詢**，還沒有做扣配額。計數器走 Redis
（key 含 Asia/Taipei 日期），是另一件事，等 dialogue 端點成形時再接。
"""
from sqlalchemy.orm import Session

from app.modules.body.models import UsageTier, UsageTierLimit


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
