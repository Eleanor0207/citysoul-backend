"""把 `content/daily_event_calendars/*.yaml` 匯入 `brain.daily_event_calendars`。

用法：
    python -m scripts.import_daily_event_calendars --dry-run
    python -m scripts.import_daily_event_calendars
    python -m scripts.import_daily_event_calendars --prune

## 為什麼有這支腳本（backend#71）

B9 當日情境的三類白名單輸入（SDD §20.1）裡，節慶日曆與官方活動都放在
`brain.daily_event_calendars`。輪播池早就有 `import_daily_event_notes.py`，
但這張表**在 2026-08-22 之前沒有任何寫入管道**——只有 `daily_event_sources.py`
在讀。文件裡講的「人工錄入」實際上等於直接連上資料庫打 INSERT。

那不是流程，是缺工具。這支腳本補上它，讓錄入跟人格卡、輪播池走同一套模式：
內容放在版控的 YAML，審核痕跡留在檔案裡，匯入是可重跑的。

## 抓取器寫的列不歸這支腳本管

`app/modules/body/official_announcements.py` 的排程也會寫 `official_event` 列，
它寫的列 `reviewed_by` 是 `official_feed`。這支腳本**只碰人工錄入的列**
（`reviewed_by <> 'official_feed'`），`--prune` 也一樣。

兩邊共存是設計：同一類輸入可以有兩個生產者，一個是人、一個是排程。
（2026-08-22 盤查結果：九個地標的官網都沒有可用的 feed，所以目前實際上
只有人這一個生產者。見 `content/official_sources.yaml`。）

## 去重靠自然鍵，不靠 UNIQUE 約束

這張表刻意沒有 UNIQUE 約束——人工錄入的列本來就可能有相同標題（例如每年的
「安太歲法會」），不該被資料庫擋下來。所以這裡跟 `official_announcements.store()`
一樣用查詢去重，鍵是「同一個地標 ＋ 同一種事件 ＋ 同一個標題 ＋ 同一個日期規則
的起點」。重跑同一份檔案不會長出第二列，改了結束日期會就地更新。

## date_rule 由填的欄位推導，不用自己填

`date_rule` 那四個列舉值是資料庫的詞彙，不是錄入者的詞彙。要求手填只會讓人
填錯，而填錯的症狀是「那天就是沒有東西」——不會報錯。所以這裡從欄位形狀推導：

    一次性活動（Demo、測試、特展）：
      start_date / end_date        -> gregorian_range

    每年重複的國曆節日：
      gregorian: {start: "09-15", end: "09-17"}   -> gregorian_fixed

    每年重複的農曆節日：
      lunar: {start: "01-01", end: "01-05"}       -> lunar_fixed

    農曆某月的最後一天（除夕那種）：
      lunar_month_end: 12                         -> lunar_month_end

## active 一律寫 true

比照 2026-08-18 取消人工審核閘門的決定（見 `scripts/import_personas.py`）。
`reviewed_by` 照實寫進資料庫：`MVP_NO_REVIEW` 代表沒有人讀過，那是稽核資訊，
不是擋下匯入的理由。
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

CALENDARS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "content" / "daily_event_calendars"
)
PLACEHOLDERS = ("PENDING_NARRATIVE_REVIEW", "PENDING_HUMAN_REVIEW", "待定", "TODO")

# 抓取器寫的列用這個值。這支腳本不碰它們。
FEED_REVIEWED_BY = "official_feed"

EVENT_TYPES = ("festival", "official_event")

KNOWN_SPIRITS = text("SELECT spirit_id FROM spirits")

FIND_ROW = text(
    """
    SELECT calendar_id FROM brain.daily_event_calendars
    WHERE place_id = :place_id
      AND event_type = :event_type
      AND title = :title
      AND date_rule = :date_rule
      AND start_date IS NOT DISTINCT FROM :start_date
      AND start_month IS NOT DISTINCT FROM :start_month
      AND start_day IS NOT DISTINCT FROM :start_day
      AND reviewed_by IS DISTINCT FROM :feed
    LIMIT 1
    """
)

UPDATE_ROW = text(
    """
    UPDATE brain.daily_event_calendars
    SET end_date = :end_date,
        end_month = :end_month,
        end_day = :end_day,
        active = true,
        reviewed_by = :reviewed_by,
        reviewed_at = now()
    WHERE calendar_id = :calendar_id
    """
)

INSERT_ROW = text(
    """
    INSERT INTO brain.daily_event_calendars
        (place_id, event_type, title, date_rule,
         start_date, end_date, start_month, start_day, end_month, end_day,
         active, reviewed_by, reviewed_at)
    VALUES
        (:place_id, :event_type, :title, :date_rule,
         :start_date, :end_date, :start_month, :start_day, :end_month, :end_day,
         true, :reviewed_by, now())
    """
)

# --prune 只刪人工錄入的列。抓取器寫的列不歸這支腳本管。
DELETE_EXTRA = text(
    """
    DELETE FROM brain.daily_event_calendars
    WHERE place_id = :place_id
      AND reviewed_by IS DISTINCT FROM :feed
      AND calendar_id <> ALL(:keep)
    """
)


def load_files(directory: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]


def _parse_month_day(value: object) -> tuple[int, int] | None:
    """`"09-15"` -> `(9, 15)`。看不懂就回 None，由呼叫端報錯。"""
    if not isinstance(value, str):
        return None
    parts = value.split("-")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def resolve_shape(event: dict) -> tuple[dict | None, str | None]:
    """把一則事件的日期欄位化成資料庫那五個欄位。回傳 (參數, 錯誤訊息)。"""
    has_range = "start_date" in event or "end_date" in event
    has_gregorian = "gregorian" in event
    has_lunar = "lunar" in event
    has_month_end = "lunar_month_end" in event

    chosen = [has_range, has_gregorian, has_lunar, has_month_end]
    if sum(chosen) != 1:
        return None, (
            "日期規則要恰好填一種："
            "start_date/end_date、gregorian、lunar 或 lunar_month_end"
        )

    empty = {
        "start_date": None, "end_date": None,
        "start_month": None, "start_day": None,
        "end_month": None, "end_day": None,
    }

    if has_range:
        start, end = event.get("start_date"), event.get("end_date")
        if not isinstance(start, datetime.date) or not isinstance(end, datetime.date):
            return None, "start_date 與 end_date 都要填，格式是 YYYY-MM-DD"
        if start > end:
            return None, f"start_date {start} 晚於 end_date {end}"
        return {**empty, "date_rule": "gregorian_range",
                "start_date": start, "end_date": end}, None

    if has_month_end:
        month = event.get("lunar_month_end")
        if not isinstance(month, int) or not (1 <= month <= 12):
            return None, "lunar_month_end 要是 1 到 12 的農曆月份"
        # 資料庫的 shape 約束要求 end_month = start_month、兩個 day 都是 NULL。
        return {**empty, "date_rule": "lunar_month_end",
                "start_month": month, "end_month": month}, None

    key = "gregorian" if has_gregorian else "lunar"
    block = event.get(key) or {}
    if not isinstance(block, dict):
        return None, f"{key} 要是含 start 與 end 的對應表，例如 {{start: '09-15', end: '09-17'}}"
    start = _parse_month_day(block.get("start"))
    end = _parse_month_day(block.get("end", block.get("start")))
    if start is None or end is None:
        return None, f"{key} 的 start/end 要是 \"MM-DD\" 格式的字串"
    return {**empty,
            "date_rule": "gregorian_fixed" if has_gregorian else "lunar_fixed",
            "start_month": start[0], "start_day": start[1],
            "end_month": end[0], "end_day": end[1]}, None


def build_rows(data: dict) -> tuple[list[dict], list[str]]:
    """把一份 YAML 化成待寫入的列。回傳 (列, 錯誤訊息)。"""
    rows: list[dict] = []
    errors: list[str] = []
    place_id = data.get("place_id")
    reviewed_by = data.get("reviewed_by")

    for index, event in enumerate(data.get("events") or []):
        if not isinstance(event, dict):
            errors.append(f"第 {index + 1} 則不是對應表")
            continue

        title = (event.get("title") or "").strip()
        if not title:
            errors.append(f"第 {index + 1} 則缺 title")
            continue
        for placeholder in PLACEHOLDERS:
            if placeholder in title:
                errors.append(f"「{title}」還帶著佔位字串 {placeholder}")

        event_type = event.get("event_type", "official_event")
        if event_type not in EVENT_TYPES:
            errors.append(
                f"「{title}」的 event_type 是 {event_type!r}，只能是 {EVENT_TYPES}"
            )
            continue

        shape, error = resolve_shape(event)
        if error:
            errors.append(f"「{title}」：{error}")
            continue

        rows.append({
            "place_id": place_id,
            "event_type": event_type,
            "title": title,
            "reviewed_by": reviewed_by,
            **shape,
        })

    return rows, errors


def validate(loaded: list[tuple[pathlib.Path, dict]]) -> tuple[dict[str, list[dict]], list[str]]:
    """驗證整批，回傳 (place_id -> 列, 錯誤訊息)。有錯就整批不匯入。"""
    by_place: dict[str, list[dict]] = {}
    errors: list[str] = []

    for path, data in loaded:
        name = path.name
        data = data or {}
        place_id = data.get("place_id")
        if not place_id:
            errors.append(f"{name}：缺 place_id")
            continue
        if place_id in by_place:
            errors.append(f"{name}：place_id {place_id} 重複，兩個檔案會互相覆蓋")
        if not data.get("reviewed_by"):
            errors.append(f"{name}：缺 reviewed_by")

        rows, row_errors = build_rows(data)
        errors.extend(f"{name}：{error}" for error in row_errors)

        # 同一份檔案裡的自然鍵撞在一起，第二則會被當成第一則的更新而消失。
        seen: set[tuple] = set()
        for row in rows:
            key = (row["event_type"], row["title"], row["date_rule"],
                   row["start_date"], row["start_month"], row["start_day"])
            if key in seen:
                errors.append(f"{name}：「{row['title']}」有兩則日期規則完全相同的重複")
            seen.add(key)

        by_place[place_id] = rows

    return by_place, errors


def describe(row: dict) -> str:
    if row["date_rule"] == "gregorian_range":
        return f"{row['start_date']} ~ {row['end_date']}"
    if row["date_rule"] == "lunar_month_end":
        return f"農曆 {row['start_month']} 月最後一天"
    calendar = "國曆" if row["date_rule"] == "gregorian_fixed" else "農曆"
    return (f"每年{calendar} {row['start_month']:02d}-{row['start_day']:02d}"
            f" ~ {row['end_month']:02d}-{row['end_day']:02d}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--calendars-dir", default=str(CALENDARS_DIR))
    parser.add_argument(
        "--prune",
        action="store_true",
        help="刪掉檔案裡不再有的人工錄入列。抓取器寫的列不受影響。預設不做。",
    )
    args = parser.parse_args()

    directory = pathlib.Path(args.calendars_dir)
    if not directory.is_dir():
        print(f"✗ 找不到目錄：{directory}")
        return 1

    loaded = load_files(directory)
    if not loaded:
        print(f"✗ {directory} 裡沒有 YAML")
        return 1

    by_place, errors = validate(loaded)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        print(f"\n{len(errors)} 個問題，整批不匯入。")
        return 1

    total = sum(len(rows) for rows in by_place.values())
    print(f"{len(loaded)} 份 YAML 全部通過驗證，共 {total} 則")
    for place_id, rows in by_place.items():
        # 空清單是正常狀態：多數地標多數時候沒有活動。
        print(f"  {place_id:38s} {len(rows)} 則")
        for row in rows:
            print(f"      [{row['event_type']}] {row['title']}  {describe(row)}")

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        known = {row[0] for row in conn.execute(KNOWN_SPIRITS)}
        unknown = [place_id for place_id in by_place if place_id not in known]
        if unknown:
            # 沒有外鍵擋著（這張表的 place_id 刻意不是外鍵），所以自己擋：打錯字的
            # place_id 會安靜地永遠不被讀到，那種錯誤沒有任何症狀。
            print(f"✗ 這些 place_id 不在 spirits 裡：{unknown}")
            return 1

        inserted = updated = 0
        for place_id, rows in by_place.items():
            keep: list = []
            for row in rows:
                existing = conn.execute(
                    FIND_ROW, {**row, "feed": FEED_REVIEWED_BY}
                ).first()
                if existing is None:
                    conn.execute(INSERT_ROW, row)
                    inserted += 1
                    keep.append(
                        conn.execute(FIND_ROW, {**row, "feed": FEED_REVIEWED_BY}).first()[0]
                    )
                else:
                    conn.execute(UPDATE_ROW, {**row, "calendar_id": existing[0]})
                    updated += 1
                    keep.append(existing[0])

            if args.prune:
                removed = conn.execute(
                    DELETE_EXTRA,
                    {"place_id": place_id, "feed": FEED_REVIEWED_BY, "keep": keep},
                ).rowcount
                if removed:
                    print(f"  --prune：{place_id} 刪掉 {removed} 則檔案裡沒有的")

    print(f"\n匯入完成：新增 {inserted} 則，更新 {updated} 則。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
