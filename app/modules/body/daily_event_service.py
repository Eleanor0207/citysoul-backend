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

from app.modules.body import landmark_events_service
from app.modules.body.models import DailyEventCache, Spirit
from app.modules.body.quests import taipei_today
from app.modules.brain.daily_event import (
    DailyEventInputs,
    fallback_content,
    generate_daily_event_content,
)

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
    official_event: dict | None = None,
    now: datetime | None = None,
) -> DailyEventCache:
    """
    排程觸發：為某地標產生（或更新）今天的當日情境。

    ## 重複觸發是正常的，不是錯誤

    重試、多實例、手動補跑都會讓同一個 `(place_id, event_date)` 被觸發兩次。
    去重靠**主鍵**，不是靠排程自己記得——先寫、撞到就改成更新，同 #16 共鳴入帳
    與 #32 配額的處理。

    `inputs` 由呼叫端提供（B9 不自己抓資料，見 `brain/daily_event.py`）。

    ## `official_event` 是**跟著這一天的快取一起存下來的快照**

    活動卡不在讀取時現查，理由是保底路徑：今天沒生成就回昨天的內容，而昨天的
    敘事是從昨天那場活動生出來的。現查的話玩家會看到「昨天的敘事＋今天的活動
    卡」，兩段文字互相矛盾而且沒有任何錯誤訊息。

    存快照還有第二個好處：活動被收回放行（`revoke`）之後，已經生成的那天仍然
    自洽——我們不會回頭竄改已經發生過的一天。

    呼叫端要負責讓它跟 `inputs.official_events` 對應。要免於這種對應錯誤，用
    `refresh_from_landmark_events()`，它兩邊都從同一筆資料組出來。
    """
    moment = _now(now)
    event_date = taipei_today(moment)

    _require_spirit(db, place_id)

    content = generate_daily_event_content(
        client, place_id=place_id, event_date=event_date, inputs=inputs
    )
    payload = {
        "narrative_text": content.narrative_text,
        "is_fallback": content.is_fallback,
        "sources": content.sources,
    }
    # 沒有活動時**不寫這個 key**，而不是寫 null。舊的快取列本來就沒有它，
    # 兩種情況因此在讀取端長得一樣——`DailyEventResponse.official_event` 的
    # 預設值同時涵蓋「這天沒活動」與「這列是加欄位之前寫的」。
    if official_event:
        payload["official_event"] = official_event
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


def refresh_from_landmark_events(
    db: Session, client, *, place_id: str, now: datetime | None = None
) -> DailyEventCache:
    """
    排程用的入口：從 `landmark_events` 取今天那場**已放行**的活動，生成當日情境。

    ## 為什麼是一支新函式，而不是讓 refresh_daily_event 自己查

    `refresh_daily_event(inputs=...)` 的既有語意是「呼叫端決定餵什麼」，那條
    邊界有測試守著（`test_service_delegates_generation_to_b9`）。改成「沒給就
    自己查」會讓那些測試的意義變得模糊，而且日後想手動補跑一個指定內容的日子
    就得先想辦法繞過自動查詢。

    分成兩支之後：這一支負責「從我們自己的表組出輸入」，那一支負責「拿到輸入
    之後怎麼生成與存放」。**`official_events` 與 `official_event` 都從同一筆
    資料組出來**，對應錯誤在這裡結構上不可能發生。

    ## 沒有活動不是錯誤

    大多數地標大多數日子沒有展覽。B9 拿到空輸入會走人工預寫保底，而那**仍然
    要寫進快取**（見 `test_no_qualifying_input_still_caches_the_fallback`）。
    """
    moment = _now(now)
    today = taipei_today(moment)

    _require_spirit(db, place_id)

    row = landmark_events_service.featured_event(db, spirit_id=place_id, on_date=today)

    # 同一列資料的兩種形狀：prompt 要簡介，活動卡不要。兩者都從 `row` 出來，
    # 所以「敘事講的是 A 展、卡片印的是 B 展」在結構上不可能發生。
    inputs = DailyEventInputs(
        official_events=[landmark_events_service.prompt_text(row)] if row else []
    )
    official_event = landmark_events_service.event_view(row) if row else None

    return refresh_daily_event(
        db,
        client,
        place_id=place_id,
        inputs=inputs,
        official_event=official_event,
        now=moment,
    )


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
    content = fallback_content(place_id, today)
    return {
        "narrative_text": content.narrative_text,
        "is_fallback": True,
        "sources": [],
    }
