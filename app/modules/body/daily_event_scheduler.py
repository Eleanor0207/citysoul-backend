"""
S10．當日情境排程觸發 ＋ 快取（issue #26；SDD §3.1）。

跟 B9（issue #20，`app.modules.brain.daily_event`）刻意拆分：那支模組只管
「怎麼生成內容」，不碰資料庫；這支模組管「什麼時候觸發生成、生成結果存
哪裡、對外端點怎麼保底」——v2.1 §6.4 的腦袋／身體分工原則。
"""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import DailyEventCache
from app.modules.body.quests import TAIPEI, taipei_today
from app.modules.brain.daily_event import generate_daily_event_content
from app.modules.brain.gemini import GeminiClient

# 快取有效到當天 Asia/Taipei 午夜（跨日就是新的一列，不是延長舊的一列）。
_CACHE_TTL_DAYS = 1


def _end_of_taipei_day_utc(event_date: date) -> datetime:
    next_midnight_taipei = datetime.combine(
        event_date + timedelta(days=_CACHE_TTL_DAYS), datetime.min.time(), TAIPEI
    )
    return next_midnight_taipei.astimezone(timezone.utc)


def trigger_daily_event_generation(
    db: Session,
    *,
    place_id: str,
    client: GeminiClient,
    now: datetime | None = None,
    official_event: str | None = None,
    curated_material: str | None = None,
) -> DailyEventCache | None:
    """
    每日排程的觸發點：呼叫 B9 生成內容，寫進 `daily_event_cache`。

    同一天對同一地標重複觸發**不產生重複列**（issue #26 AC）：先寫、撞到
    `(place_id, event_date)` 主鍵衝突就當作已經生成過，優雅回傳 `None`，
    不拋例外——同 issue #16 共鳴事件帳本的教訓：「先查再寫」在並行下擋不住
    兩個排程觸發同時通過查詢的競態，只有資料庫的主鍵約束擋得住。

    日界以 Asia/Taipei 午夜為準，沿用 `quests.taipei_today()`，不另寫一套
    （issue #26 AC；#15 已經在這裡踩過「跨了 UTC 日期線但還沒到台北午夜」
    的坑）。
    """
    now = now or datetime.now(timezone.utc)
    event_date = taipei_today(now)

    content = generate_daily_event_content(
        place_id, event_date, client,
        official_event=official_event, curated_material=curated_material,
    )

    row = DailyEventCache(
        place_id=place_id,
        event_date=event_date,
        content={"narrative_text": content.narrative_text},
        generated_at=now,
        expires_at=_end_of_taipei_day_utc(event_date),
    )

    try:
        with db.begin_nested():
            db.add(row)
        db.commit()
        return row
    except IntegrityError:
        db.rollback()
        return None
