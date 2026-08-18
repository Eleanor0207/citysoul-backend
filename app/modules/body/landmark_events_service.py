"""
地標官方公開活動的資料庫存取：寫入、審核、挑選。

規則本身在 `landmark_events.py`（純函式，零 DB）。這裡只做「存到哪、怎麼查」
——跟 B9／S10 同一種分法（v2.1 §6.4）。命名慣例跟 `daily_event_service.py`、
`collections_service.py` 一致。

## 🔒 這個模組不會把 active 設成 true，除非呼叫端明確要求審核

`upsert_events()` 是抓取用的，它**只寫內容**。放行是 `approve()`，只有
`scripts/review_landmark_events.py` 這條人工路徑會呼叫它。

兩支分開不是形式主義：合成一支的話，「順手在抓取時自動放行熟悉的場館」會是一個
看起來很合理的三行改動，而審核閘就這樣沒了。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.body import landmark_events as rules
from app.modules.body.models import LandmarkEvent, Spirit

logger = logging.getLogger(__name__)


# ⚠️ `active` / `reviewed_by` / `reviewed_at` 的 CASE 都比對**舊的** content_hash。
# Postgres 的 DO UPDATE SET 所有運算式都對舊列求值，所以三條 CASE 看到的是同一個
# 舊值，不受 SET 的順序影響。
_UPSERT = text(
    """
    INSERT INTO landmark_events
        (event_id, spirit_id, title, summary, venue_name, start_date, end_date,
         source, source_url, content_hash, active, fetched_at)
    VALUES
        (:event_id, :spirit_id, :title, :summary, :venue_name, :start_date, :end_date,
         :source, :source_url, :content_hash, false, :fetched_at)
    ON CONFLICT (event_id) DO UPDATE SET
        spirit_id    = EXCLUDED.spirit_id,
        title        = EXCLUDED.title,
        summary      = EXCLUDED.summary,
        venue_name   = EXCLUDED.venue_name,
        start_date   = EXCLUDED.start_date,
        end_date     = EXCLUDED.end_date,
        source       = EXCLUDED.source,
        source_url   = EXCLUDED.source_url,
        content_hash = EXCLUDED.content_hash,
        fetched_at   = EXCLUDED.fetched_at,
        -- 🔒 內容變了就退回未審核。內容換了而簽名留著，等於讓上一次的審核替
        -- 這一次的文字背書——比一開始就沒有審核更糟，因為它看起來是有審核的。
        -- 與 `import_landmarks.UPSERT_DISTRICT` 同一個道理。
        active      = CASE WHEN landmark_events.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                           THEN false ELSE landmark_events.active END,
        reviewed_by = CASE WHEN landmark_events.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                           THEN NULL ELSE landmark_events.reviewed_by END,
        reviewed_at = CASE WHEN landmark_events.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                           THEN NULL ELSE landmark_events.reviewed_at END
    RETURNING (xmax = 0) AS inserted, active
    """
)


def spirit_candidates(db: Session, *, only=None) -> list[tuple[str, float, float]]:
    """
    可以被對上的地標。

    兩道過濾：

    1. `is_active=true` —— 下架的地標不該累積之後要清理的資料
    2. `only` —— 白名單，預設是 `rules.VERIFIED_VENUE_SPIRITS`

    ## 為什麼要白名單

    地理比對只能回答「這場活動辦在附近嗎」，回答不了「這場活動是這個地標辦的
    嗎」。霞海城隍廟 42 公尺外就是大稻埕戲苑——比西門紅樓到自己的劇場還近。
    理由與實測數字寫在 `rules.VERIFIED_VENUE_SPIRITS` 的註解。

    傳 `only=()` 可以關掉白名單（診斷用）。**正式流程不要這樣呼叫**——那會讓
    每個地標開始講隔壁機構的活動，而且審核時光看標題不見得看得出來。
    """
    allowed = rules.VERIFIED_VENUE_SPIRITS if only is None else tuple(only)

    query = db.query(Spirit.spirit_id, Spirit.latitude, Spirit.longitude).filter(
        Spirit.is_active.is_(True)
    )
    if allowed:
        query = query.filter(Spirit.spirit_id.in_(allowed))

    rows = query.order_by(Spirit.spirit_id).all()
    return [(r[0], float(r[1]), float(r[2])) for r in rows]


def upsert_events(db: Session, records, *, fetched_at: datetime | None = None) -> dict:
    """
    寫入或更新一批活動。回傳計數，供腳本印出來。

    ⚠️ **整批一個交易。** 一半寫進去一半沒寫的狀態沒有人想收拾，而且從資料庫
    外面看不出來它是半套的——跟 `import_landmarks.py` 同一個理由。

    `active` 一律寫 false（見模組 docstring）；已存在且內容沒變的列會保留原本的
    審核狀態，這正是 `_UPSERT` 那三條 CASE 在做的事。
    """
    moment = fetched_at or datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    revoked = 0

    for record in records:
        result = db.execute(
            _UPSERT,
            {
                "event_id": record["event_id"],
                "spirit_id": record["spirit_id"],
                "title": record["title"],
                "summary": record.get("summary"),
                "venue_name": record.get("venue_name"),
                "start_date": record.get("start_date"),
                "end_date": record.get("end_date"),
                "source": record.get("source", rules.SOURCE_ICULTURE),
                "source_url": record.get("source_url"),
                "content_hash": record["content_hash"],
                "fetched_at": moment,
            },
        ).first()

        # ⚠️ 不要用 `rowcount` 判斷。psycopg3 在 INSERT ... ON CONFLICT 上會回 -1，
        # 而 `bool(-1)` 是 True——2026-08-18 在 collections_service 踩過同一個坑。
        was_inserted = bool(result[0])
        if was_inserted:
            inserted += 1
        else:
            updated += 1
            if not result[1]:
                revoked += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        # 更新後處於未審核狀態的筆數。包含「本來就沒審過」與「內容變了被退回」
        # 兩種，腳本印出來讓人知道待審清單長了多少。
        "pending_after_update": revoked,
    }


def featured_event(db: Session, *, spirit_id: str, on_date: date) -> LandmarkEvent | None:
    """
    某地標在某一天要推的那一場活動。沒有就回 `None`——**那是常態，不是錯誤**
    （大多數地標大多數日子沒有展覽）。

    回傳的是資料列本身而不是對外形狀，因為它有兩個去處而且兩個要的欄位不同：
    活動卡走 `event_view()`（沒有簡介），B9 的 prompt 走 `prompt_text()`
    （要簡介）。先轉成其中一種的話，另一種就得再查一次資料庫。

    ## 資格：還沒結束，而且在前置期內開演

    用 `rules.is_featurable_on()` 而**不是** `is_running_on()`。抓回來的活動絕大
    多數是單日演出，只推「今天正在進行」的話等於推不出東西（2026-08-18 實測，
    22 筆有 18 筆是單日）。完整推論見 `rules.FEATURE_LEAD_DAYS`。

    ## 排序：最快結束的優先

    「限時」是這個功能的賣點，剩三天的演出比剩三個月的展覽更值得現在推。並列時用
    `event_id` 決勝，讓結果穩定——不穩定的話同一天重新排程會換一場活動，而
    昨天生成的敘事就跟今天的活動卡對不起來了。

    ## 為什麼過濾寫在 Python 而不是 SQL

    資格規則只寫在 `rules.is_featurable_on()` 一個地方。寫成 SQL 的 WHERE 會變成
    第二份實作，而兩份實作遲早會不一致——不一致的那天表現成「測試全綠但線上推了
    一場已經結束的展覽」。
    """
    rows = (
        db.query(LandmarkEvent)
        .filter(
            LandmarkEvent.spirit_id == spirit_id,
            LandmarkEvent.active.is_(True),
        )
        .order_by(LandmarkEvent.end_date.asc(), LandmarkEvent.event_id.asc())
        .all()
    )

    for row in rows:
        if rules.is_featurable_on(row.start_date, row.end_date, on_date):
            return row
    return None


def prompt_text(row: LandmarkEvent) -> str:
    """
    餵給 B9 的那一行「官方活動」。

    帶上簡介而不是只給標題：展覽名稱常常是抽象的（「浮光」「間隙」），只給名稱
    的話模型無從發揮，寫出來的敘事會跟沒有輸入時的保底台詞差不多——那等於白花
    一次模型呼叫。

    長度已經在匯入時封頂（`rules.SUMMARY_MAX_CHARS`），所以這裡不必再截一次。
    每日成本是「地標數 × 一次呼叫」，九個地標的量級可以忽略。
    """
    if row.summary:
        return f"{row.title}——{row.summary}"
    return row.title


def event_view(row: LandmarkEvent) -> dict:
    """
    一列活動轉成對外／存進快取的形狀。

    ⚠️ 這裡**沒有 `summary`**。簡介只餵 B9 的 prompt，不給玩家看——活動卡上是
    標題、檔期、場館、連結（A.L. 2026-08-18）。多帶一個沒有人顯示的欄位，只會
    讓下一個人以為它該顯示。

    `source_label` 由後端決定（政府資料開放授權條款第 1 版要求標示出處）。
    客戶端只負責畫出來，不判斷來源是誰。
    """
    return {
        "title": row.title,
        "venue_name": row.venue_name,
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "source_url": row.source_url,
        "source_label": rules.SOURCE_LABELS.get(row.source, ""),
    }


def pending_events(db: Session, *, spirit_id: str | None = None) -> list[LandmarkEvent]:
    """待審清單。`scripts/review_landmark_events.py` 用。"""
    query = db.query(LandmarkEvent).filter(LandmarkEvent.active.is_(False))
    if spirit_id:
        query = query.filter(LandmarkEvent.spirit_id == spirit_id)
    return query.order_by(LandmarkEvent.spirit_id, LandmarkEvent.end_date, LandmarkEvent.event_id).all()


def approve(db: Session, event_id: str, *, reviewer: str) -> bool:
    """
    人工放行一場活動。回傳有沒有這一筆。

    🔒 **這是 `active` 唯一會變成 true 的地方。** 呼叫它的只有
    `scripts/review_landmark_events.py`——跟 `brain.districts` 的審核閘同一個
    形狀（`import_landmarks.py` 的 docstring 描述過那條規則）。

    `reviewer` 沒有預設值是刻意的：預設成 `"system"` 之類的東西，就等於允許
    一個沒有人負責的簽名。
    """
    if not reviewer or not reviewer.strip():
        raise ValueError("reviewer 不可為空——審核要有人負責")

    row = db.query(LandmarkEvent).filter_by(event_id=event_id).first()
    if row is None:
        return False

    row.active = True
    row.reviewed_by = reviewer.strip()[:64]
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("landmark_events %s 由 %s 放行", event_id, reviewer)
    return True


def revoke(db: Session, event_id: str) -> bool:
    """
    收回放行。審核簽名一併清掉——留著的話下次看會以為它是審過的。
    """
    row = db.query(LandmarkEvent).filter_by(event_id=event_id).first()
    if row is None:
        return False

    row.active = False
    row.reviewed_by = None
    row.reviewed_at = None
    db.commit()
    logger.info("landmark_events %s 已收回放行", event_id)
    return True
