"""
S10．當日情境排程觸發＋快取＋對外查詢（#26，SDD §3.1，與 B9 刻意拆分）。

跟 B9（#20）的邊界：B9 只管「怎麼生成內容」，這裡管「排程什麼時候觸發、
怎麼存、對外怎麼給」。`get_daily_event_for_spirit` 是被動的——它只讀快取，
**不會**臨時觸發生成；玩家不該因為排程延遲或失敗而多等一次 Gemini 呼叫，
沒有今日快取時的補救辦法是「回前一天」或「回人工保底」，不是「現在馬上
生一份」。
"""
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import DailyEventCache, Spirit
from app.modules.body.quests import TAIPEI, taipei_today
from app.modules.brain.daily_event_content import generate_daily_event_content
from app.modules.brain.gemini import GeminiClient

# 連前一天的快取都沒有時的人工預寫保底。跟 B9 自己的「無合格輸入」回退是
# 不同語境的保底：B9 那句回答的是「今天沒素材」，這句回答的是「排程從來
# 沒跑過、整個快取是空的」，刻意分開持有，不共用同一句話。
_NO_CACHE_AT_ALL_FALLBACK = "（城市靈魂的今日絮語還在路上，先靜靜感受這裡的氣息吧。）"


class DailyEventResult:
    """
    `get_daily_event_for_spirit` 的回傳值。刻意不是 pydantic model——這是
    內部傳輸物件，wire contract 的形狀交給 router 那層的 `schemas` 決定
    （同 `quests.QuestState` 的做法）。
    """

    def __init__(self, *, place_id: str, event_date: date, narrative_text: str, source: str):
        self.place_id = place_id
        self.event_date = event_date
        self.narrative_text = narrative_text
        self.source = source


def trigger_daily_event_generation(
    db: Session,
    spirit_id: str,
    *,
    now: datetime | None = None,
    festival_name: str | None = None,
    official_events: Sequence[str] = (),
    reviewed_material: Sequence[str] = (),
    gemini_client: GeminiClient | None = None,
) -> DailyEventCache:
    """
    排程呼叫的入口：呼叫 B9 產生今天的內容，寫進快取。

    同一天同一地標重複觸發不會產生第二列，也不會拋例外：先嘗試 INSERT，
    撞到主鍵衝突就當作已經有了，回讀現有那一列。這是「先寫、撞到約束才
    知道重複」，不是「先查再寫」——兩個並行觸發都先查「還沒有」再各自
    寫入，是一個看起來安全、其實有競態的做法（同 #16 共鳴入帳的教訓）。
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)

    generated = generate_daily_event_content(
        spirit_id,
        today,
        festival_name=festival_name,
        official_events=official_events,
        reviewed_material=reviewed_material,
        gemini_client=gemini_client,
    )

    row = DailyEventCache(
        place_id=spirit_id,
        event_date=today,
        content={"narrative_text": generated.narrative_text},
        expires_at=_next_taipei_midnight(today),
    )
    db.add(row)
    try:
        db.commit()
        return row
    except IntegrityError:
        db.rollback()
        return db.query(DailyEventCache).filter_by(place_id=spirit_id, event_date=today).one()


def get_daily_event_for_spirit(
    db: Session, spirit_id: str, *, now: datetime | None = None
) -> DailyEventResult | None:
    """
    對外查詢的核心邏輯。`spirit_id` 不存在時回 `None`（呼叫端轉 404）；
    地標存在但沒有任何內容時，仍回一個可用的結果——玩家不該因為排程延遲
    或失敗看到空畫面，這是硬要求，不是體驗上的加分。

    保底鏈路只看兩層：今天 → 昨天 → 人工預寫，不會往更早以前找。
    """
    spirit = db.query(Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None:
        return None

    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)

    today_row = (
        db.query(DailyEventCache).filter_by(place_id=spirit_id, event_date=today).first()
    )
    if today_row is not None:
        return DailyEventResult(
            place_id=spirit_id,
            event_date=today,
            narrative_text=today_row.content["narrative_text"],
            source="cached_today",
        )

    yesterday = today - timedelta(days=1)
    yesterday_row = (
        db.query(DailyEventCache).filter_by(place_id=spirit_id, event_date=yesterday).first()
    )
    if yesterday_row is not None:
        return DailyEventResult(
            place_id=spirit_id,
            event_date=today,
            narrative_text=yesterday_row.content["narrative_text"],
            source="cached_previous_day",
        )

    return DailyEventResult(
        place_id=spirit_id,
        event_date=today,
        narrative_text=_NO_CACHE_AT_ALL_FALLBACK,
        source="fallback",
    )


def _next_taipei_midnight(today: date) -> datetime:
    return datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=TAIPEI)
