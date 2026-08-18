"""
S10．當日情境的排程觸發、快取與讀取（issue #26）。

**內容生成不在這裡**——那是 B9（`brain/daily_event.py`，#20）。這個模組管的是
「什麼時候生成、存到哪、讀不到時怎麼辦」，刻意分屬不同模組（v2.1 §6.4）。

## 保底是硬要求，不是體貼

`GET /spirits/{placeId}/daily-event` **永遠不回空畫面**：

    今天的快取 → 沒有就回最近一次的（通常是昨天）→ 再沒有就回人工預寫保底

排程延遲、排程失敗、新地標剛上線——這些都會讓今天的快取不存在，而它們全都是
會發生的事。玩家不該因為我們的排程打嗝而看到空白。

⚠️ 注意跟 404 的分界：**地標不存在 → 404**；**地標存在但沒內容 → 200 ＋ 保底**。
前者是玩家問錯了東西，後者是我們還沒準備好，兩件事不該給同一個答案。

## 過期的內容仍然有用

`expires_at` 是給清理工作的訊號，**不是讀取時的過濾條件**。保底策略本來就是
「拿舊的來頂」，讀取時再依 `expires_at` 過濾一次，等於把保底自己關掉。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import DailyEventCache, Spirit
from app.modules.body.quests import taipei_today
from app.modules.brain.daily_event import (
    DailyEventInputs,
    fallback_content,
    generate_daily_event_content,
)
from app.modules.brain.loader import load_active_persona

logger = logging.getLogger(__name__)

# 快取的存活時間。比一天長一點，讓「今天沒生成就回昨天」這條保底路徑真的有東西
# 可以拿——設成剛好 24 小時的話，排程晚一小時，昨天的內容也剛好過期了。
CACHE_TTL_HOURS = 48


class SpiritNotFoundError(LookupError):
    """地標不存在。呼叫端要把它轉成 404——這跟「沒有內容」是兩件事。"""


def _now(now: datetime | None) -> datetime:
    """時間必須可注入。日界以 Asia/Taipei 為準（#15 已在這裡踩過坑）。"""
    return now or datetime.now(timezone.utc)


def _require_spirit(db: Session, place_id: str) -> Spirit:
    spirit = db.query(Spirit).filter_by(spirit_id=place_id).first()
    # `is_active` 一併檢查，對齊其他所有靈魂查詢端點的慣例：下架的靈魂對玩家
    # 來說就是不存在。
    if spirit is None or not spirit.is_active:
        raise SpiritNotFoundError(place_id)
    return spirit


def refresh_daily_event(
    db: Session,
    client,
    *,
    place_id: str,
    inputs: DailyEventInputs | None = None,
    now: datetime | None = None,
) -> DailyEventCache:
    """
    排程觸發：為某地標產生（或更新）今天的當日情境。

    ## 重複觸發是正常的，不是錯誤

    重試、多實例、手動補跑都會讓同一個 `(place_id, event_date)` 被觸發兩次。
    去重靠**主鍵**，不是靠排程自己記得——先寫、撞到就改成更新，同 #16 共鳴入帳
    與 #32 配額的處理。

    `inputs` 由呼叫端提供（B9 不自己抓資料，見 `brain/daily_event.py`）。
    """
    moment = _now(now)
    event_date = taipei_today(moment)

    _require_spirit(db, place_id)
    persona = load_active_persona(db, place_id)

    content = generate_daily_event_content(
        client,
        place_id=place_id,
        event_date=event_date,
        inputs=inputs,
        persona=persona,
    )
    payload = {
        "narrative_text": content.narrative_text,
        "is_fallback": content.is_fallback,
        "sources": content.sources,
    }
    expires_at = moment + timedelta(hours=CACHE_TTL_HOURS)

    row = DailyEventCache(
        place_id=place_id,
        event_date=event_date,
        content=payload,
        generated_at=moment,
        expires_at=expires_at,
    )

    try:
        with db.begin_nested():
            db.add(row)
        db.commit()
        return row
    except IntegrityError:
        # 這一天已經有一列了。更新它而不是失敗——重新觸發的意圖就是「用最新的
        # 內容覆蓋」，而 PK 衝突只代表「我們比自己早一步」。
        db.rollback()
        existing = (
            db.query(DailyEventCache)
            .filter_by(place_id=place_id, event_date=event_date)
            .one()
        )
        existing.content = payload
        existing.generated_at = moment
        existing.expires_at = expires_at
        db.commit()
        return existing


def get_daily_event(
    db: Session, *, place_id: str, now: datetime | None = None
) -> dict:
    """
    讀取當日情境。**永遠回傳內容，永不回空**。

    地標不存在時拋 `SpiritNotFoundError`（呼叫端轉 404）——那跟「沒有內容」
    是兩件事。
    """
    moment = _now(now)
    today = taipei_today(moment)

    _require_spirit(db, place_id)

    row = (
        db.query(DailyEventCache).filter_by(place_id=place_id, event_date=today).first()
    )
    if row is not None:
        return dict(row.content)

    # 今天沒有 → 拿最近的一筆（通常是昨天）。
    #
    # 刻意**不依 expires_at 過濾**：保底本來就是「拿舊的來頂」，再過濾一次
    # 等於把保底自己關掉。
    latest = (
        db.query(DailyEventCache)
        .filter(DailyEventCache.place_id == place_id, DailyEventCache.event_date < today)
        .order_by(DailyEventCache.event_date.desc())
        .first()
    )
    if latest is not None:
        logger.info("%s 今天（%s）沒有當日情境，回退到 %s 的內容", place_id, today, latest.event_date)
        return dict(latest.content)

    # 連一筆都沒有——新地標剛上線，或排程從沒跑過。
    logger.info("%s 完全沒有當日情境快取，使用人工預寫保底", place_id)
    content = fallback_content(place_id, today, persona=load_active_persona(db, place_id))
    return {
        "narrative_text": content.narrative_text,
        "is_fallback": True,
        "sources": [],
    }
