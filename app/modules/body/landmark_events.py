"""
地標官方公開活動的**純函式**：正規化、座標品質過濾、比對地標、檔期判斷。

## 為什麼跟 `landmark_events_service.py` 分開

跟 B9／S10 的分法一樣（v2.1 §6.4）：這裡零 DB、零網路，所以「一筆髒座標會不會
被丟掉」「一場已經結束的展覽會不會被推出去」這些規則可以直接餵值驗證，不必先
起一個資料庫、更不必真的去打 iCulture。

抓取（網路 I/O）在 `scripts/fetch_landmark_events.py`，寫入在
`landmark_events_service.py`。這個模組**不 import 任何一邊**。

## ⚠️ 欄位名是照 data.gov.tw 的資料集說明寫的，沒有對過實際回應

`cloud.culture.tw` 擋 robots，開發當下沒辦法先驗一次真實 payload。所以每個欄位
都走 `_first()` 的別名清單，而 `scripts/fetch_landmark_events.py --dry-run` 的
第一件事就是把實際的 key 印出來。第一次跑很可能要照實際欄位補別名——那是預期，
不是失誤。
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, timedelta

from app.modules.body.geo import haversine_distance_m

SOURCE_ICULTURE = "iculture"

# 授權標示。政府資料開放授權條款第 1 版要求標示出處，這**不是可選項**。
#
# 放在後端而不是客戶端：哪天換資料源或授權條款改版，不該要我們發一版 App。
# 對外由 `schemas.OfficialEvent.source_label` 帶出去。
SOURCE_LABELS = {
    SOURCE_ICULTURE: "資料來源：文化部 iCulture",
}

# 台北市的大致外接矩形，比實際市界寬鬆——這裡要擋的是**明顯壞掉**的座標
# （0,0、縣市中心、經緯度寫反），不是精確的行政區判斷。真正的歸屬由
# `nearest_within()` 用距離決定。
#
# 抓寬一點是刻意的：邊界抓太緊會把北投、石碎坑一帶的場館誤殺，而那種誤殺
# 完全沒有痕跡——它只會表現成「那個地標永遠沒有活動」。
TAIPEI_LAT_RANGE = (24.90, 25.35)
TAIPEI_LON_RANGE = (121.40, 121.70)

# 餵進 B9 prompt 的活動簡介上限。
#
# 這**不是**排版考量——簡介不會顯示給玩家，活動卡上只有標題、檔期、場館。
# 它唯一的去處是 Gemini 的 prompt，而開放資料的 `descriptionFilterHtml` 動輒
# 上千字。SDD 把「AI 對話成本與延遲」列為 🔴 高風險，一段沒有上限的外部文字
# 直接進 prompt 正是那條風險的具體形態。
SUMMARY_MAX_CHARS = 200

# 比對半徑的預設值。300m 是「站在這個地標前面，這場活動算不算在這裡」的判斷，
# 比召喚半徑（50m）寬得多——玩家不需要走進場館，這只是內容歸屬。
DEFAULT_MATCH_RADIUS_M = 300.0

# 🔒 會去抓活動的地標白名單（A.L. 2026-08-18 拍板「先上能用的兩個」）。
#
# ## 為什麼是白名單，不是把半徑調小
#
# 2026-08-18 用 `--diagnose` 實測十個地標離最近場館的距離，結論是**距離分不出
# 對錯**：
#
#     西門紅樓     → 西門紅樓二樓劇場      49 m  ✓ 是它自己
#     松山文創     → 松山文創園區 5號倉庫   62 m  ✓ 是它自己
#     霞海城隍廟   → 大稻埕戲苑            42 m  ✗ 是隔壁另一個機構
#     天文館       → 國立臺灣科學教育館    183 m  ✗ 是隔壁另一個機構
#
# 城隍廟到戲苑只有 42 公尺，比紅樓到自己的劇場還近。半徑收到 100m 也擋不掉它，
# 而放進來的後果是城隍廟的靈魂開始講隔壁戲苑的歌仔戲。
#
# ## 為什麼不用名稱比對當第二道
#
# 試過了：`西門町紅樓`（我們的 display_name）不是 `西門紅樓二樓劇場` 的子字串，
# 要做就得退到「共同 2 字」那種模糊比對，然後再補一份「臺北／國立／中心」的
# 停用詞表來壓假陽性。兩道模糊規則疊起來，沒有人能預測它下次會放什麼進來。
#
# 一份看得懂的清單比一條猜不準的規則好。
#
# ## 怎麼加第三個
#
# 跑 `python -m scripts.fetch_landmark_events --dry-run --diagnose --all-spirits`，
# 看那個地標最近的場館**是不是它自己**。是就加進來，不是就別加——距離多近都一樣。
VERIFIED_VENUE_SPIRITS = (
    "ximen_red_house",
    "songshan_cultural_park",
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d")


def _first(raw: dict, *names: str):
    """按別名順序取第一個有值的欄位。見模組 docstring 的欄位名警告。"""
    for name in names:
        value = raw.get(name)
        if value not in (None, "", []):
            return value
    return None


def _clean_text(value) -> str | None:
    """去 HTML 標籤與多餘空白。開放資料的簡介欄位常常夾著 `<p>`／`&nbsp;`。"""
    if value is None:
        return None
    text = _TAG_RE.sub(" ", str(value))
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def parse_date(value) -> date | None:
    """
    解析開放資料的日期。解析不出來回 `None`，**不拋例外**。

    一筆日期壞掉不該讓整批匯入失敗——那會讓幾百筆好資料陪葬。壞掉的那筆會在
    `is_running_on()` 被擋下來（沒有結束日期就不推），而抓取腳本會把數量印出來。
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    text = str(value).strip()
    if not text:
        return None

    # "2026/08/01 00:00:00" → 只取日期那一段。
    text = text.split(" ")[0].split("T")[0]

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _coord(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def is_plausible_taipei_coord(lat, lon) -> bool:
    """
    這組座標像不像台北市內的一個場館。

    擋掉的三種髒資料，都在開放資料裡真實存在過：

    1. `0, 0` 或空白 —— 場館沒填
    2. 落在別的縣市 —— 全國資料集本來就包含全國
    3. 經緯度寫反（`121.5, 25.0`）—— 緯度會直接超出 ±90 或落在荒謬的位置

    第 3 種特別值得擋：寫反之後距離計算不會報錯，只會安靜地永遠對不到任何地標。
    """
    latitude = _coord(lat)
    longitude = _coord(lon)
    if latitude is None or longitude is None:
        return False
    if latitude == 0 and longitude == 0:
        return False

    lat_lo, lat_hi = TAIPEI_LAT_RANGE
    lon_lo, lon_hi = TAIPEI_LON_RANGE
    return lat_lo <= latitude <= lat_hi and lon_lo <= longitude <= lon_hi


def nearest_within(
    lat: float,
    lon: float,
    candidates,
    *,
    radius_m: float = DEFAULT_MATCH_RADIUS_M,
) -> tuple[str, float] | None:
    """
    找出半徑內最近的地標。回 `(spirit_id, 距離公尺)`，沒有就回 `None`。

    `candidates` 是 `(spirit_id, latitude, longitude)` 的序列——刻意收原始值而不是
    ORM 物件，這樣測試不必造 `Spirit`。

    ## 為什麼用 haversine 而不是 PostGIS

    九個地標 × 幾千筆活動，在 Python 裡算完是毫秒等級。走 PostGIS 要多一個
    `geography(Point)` 型別、多一段 SQL，換來的效能在這個規模上量不出來——
    而 `geo.haversine_distance_m` 已經是專案裡「距離」的唯一定義，再開第二套
    才是真正的風險。

    ## 平手時取 spirit_id 較小的

    兩個地標到同一個場館等距離幾乎不可能，但「幾乎不可能」不等於不會發生，而
    不穩定的結果會讓同一批資料重跑兩次得到兩個答案——那種 bug 極難追。
    """
    best: tuple[str, float] | None = None
    for spirit_id, spirit_lat, spirit_lon in candidates:
        distance = haversine_distance_m(
            float(lat), float(lon), float(spirit_lat), float(spirit_lon)
        )
        if distance > radius_m:
            continue
        if best is None or (distance, spirit_id) < (best[1], best[0]):
            best = (spirit_id, distance)
    return best


def is_running_on(start: date | None, end: date | None, on_date: date) -> bool:
    """
    這場活動在 `on_date` 當天算不算「正在進行」。

    ⚠️ **挑選當日活動用的不是這一支，是 `is_featurable_on()`**。這一支只回答
    「現在進行中嗎」，給審核清單標狀態用——單日演出在開演前一天仍然是「未開始」，
    但它早就該被推出去了（見 `FEATURE_LEAD_DAYS`）。

    ⚠️ **沒有結束日期就是不推。** 這條看起來嚴格，但另一邊更糟：一筆日期解析
    失敗的活動如果被當成「永遠有效」，它會從此每次輪到那個地標都被推出來，而且
    沒有任何東西會提醒我們——那是最難發現的一種內容錯誤。

    常設展因此也推不出來。那是已知的代價：真的要推的話，人工在審核時補一個
    結束日期，而那是一個明確的決定。

    沒有開始日期則寬容處理（視為早已開始）——場館常常只填結束日期，而那筆資料
    仍然是可用的。
    """
    if end is None:
        return False
    if on_date > end:
        return False
    if start is not None and on_date < start:
        return False
    return True


# 前置期：活動在幾天內開演就開始推。
#
# ## 為什麼不是「今天正在進行」
#
# 2026-08-18 實測，抓回來的 22 筆有 18 筆是**單日**演出（誠品表演廳的音樂會，
# `2026-09-11 ~ 2026-09-11` 這種）。只推「今天正在進行」的話，一場 9/11 的音樂會
# 只有 9/11 那一天推得出來——而 `DailyFeature` 十天才輪到那個地標一次。兩件事撞在
# 一起的機率趨近於零，功能等於不存在。
#
# 而且**廣告本來就需要前置期**：沒有人在演出當天才打廣告，玩家要買得到票才有意義。
#
# 30 天是「還記得住、又來得及安排」的長度。太短（一週）會漏掉需要提前訂票的節目，
# 太長（三個月）會讓同一場活動反覆出現到玩家開始無視它。
FEATURE_LEAD_DAYS = 30


def is_featurable_on(
    start: date | None,
    end: date | None,
    on_date: date,
    *,
    lead_days: int = FEATURE_LEAD_DAYS,
) -> bool:
    """
    這場活動在 `on_date` 當天值不值得推。**這是挑選當日活動的判斷**。

    跟 `is_running_on()` 的差別是前置期：正在進行的推，即將開演的也推。
    兩支都留著是因為它們回答不同的問題——`is_running_on` 是「現在進行中嗎」
    （審核清單標狀態用），這一支是「今天該不該打這個廣告」。

    ⚠️ **沒有結束日期一律不推**，理由同 `is_running_on()`：日期解析失敗的活動
    如果被當成永遠有效，它會從此每次都被推出來而且沒有東西提醒我們。
    """
    if end is None:
        return False
    if on_date > end:
        return False
    if start is not None and start > on_date + timedelta(days=lead_days):
        return False
    return True


def content_fingerprint(record: dict) -> str:
    """
    內容指紋。**「哪些欄位算內容」只寫在這裡一個地方。**

    用途是「重抓時內容有變就把 `active` 退回 false」（見 0022 的 docstring）。
    在 SQL 裡逐欄比對也做得到，但日後加欄位而忘了加進比對條件，就會出現
    「內容改了但審核沒退回」——那不會有任何錯誤訊息。

    ⚠️ `fetched_at` 與 `active` / `reviewed_*` **不在指紋裡**。前者每次抓都會變，
    納入的話每一次抓取都會把全部活動打回未審核；後者是審核狀態不是內容，納入
    會變成「一放行就立刻自我推翻」。
    """
    parts = [
        record.get("spirit_id") or "",
        record.get("title") or "",
        record.get("summary") or "",
        record.get("venue_name") or "",
        record.get("start_date").isoformat() if record.get("start_date") else "",
        record.get("end_date").isoformat() if record.get("end_date") else "",
        record.get("source_url") or "",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _venues(raw: dict) -> list[tuple[float, float, str | None, date | None, date | None]]:
    """
    一筆活動底下所有**座標可用**的場次，去重後依座標排序。

    一個展覽可能巡迴多個場館，而我們是把「場館」對到地標的。去重的鍵是座標
    （取到小數第 6 位，約 11 公分），因為同一個場館的多個場次會重複出現。

    排序是為了讓 `event_id` 的序號在重跑之間**穩定**——序號會變的話，同一場
    活動每次抓都會被當成新的一筆，而舊的那筆永遠不會被更新也不會消失。
    """
    show_info = raw.get("showInfo") or raw.get("showinfo") or []
    if isinstance(show_info, dict):
        show_info = [show_info]

    seen: dict[tuple[float, float], tuple] = {}
    for show in show_info:
        if not isinstance(show, dict):
            continue
        lat = _coord(_first(show, "latitude", "Latitude", "lat"))
        lon = _coord(_first(show, "longitude", "Longitude", "lng", "lon"))
        if lat is None or lon is None:
            continue
        if not is_plausible_taipei_coord(lat, lon):
            continue

        key = (round(lat, 6), round(lon, 6))
        if key in seen:
            continue

        seen[key] = (
            lat,
            lon,
            _clean_text(_first(show, "locationName", "LocationName", "location")),
            parse_date(_first(show, "startTime", "StartTime", "time")),
            parse_date(_first(show, "endTime", "EndTime")),
        )

    return [seen[key] for key in sorted(seen)]


def normalize_iculture(payload) -> list[dict]:
    """
    把 iCulture 的回應攤平成「一個場館一筆」的中間形狀。

    **這裡還沒有 `spirit_id`** ——比對是下一步（`nearest_within`），需要地標清單。
    回傳的每一筆帶 `latitude` / `longitude` 供比對用，那兩個值**不會進資料庫**。

    🔒 座標只是中間計算，不落地：CONTEXT.md 禁止的是玩家位置，場館座標不在其列，
    但沒有用途的欄位就不該留在表裡——留著的話下一個人會開始拿它做別的事。
    """
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("Data") or []
    if not isinstance(payload, list):
        return []

    records: list[dict] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue

        uid = _first(raw, "UID", "uid", "Uid", "id")
        title = _clean_text(_first(raw, "title", "Title", "activityName"))
        if not uid or not title:
            # 沒有穩定識別碼就沒辦法冪等更新；沒有標題就沒有東西可以顯示。
            continue

        summary = _clean_text(
            _first(raw, "descriptionFilterHtml", "description", "Description", "comment")
        )
        if summary and len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "…"

        source_url = _first(
            raw, "sourceWebPromote", "webSales", "sourceWebName", "url"
        )
        top_start = parse_date(_first(raw, "startDate", "StartDate"))
        top_end = parse_date(_first(raw, "endDate", "EndDate"))

        for index, (lat, lon, venue, start, end) in enumerate(_venues(raw)):
            records.append(
                {
                    "event_id": f"{SOURCE_ICULTURE}:{uid}:{index}",
                    "source": SOURCE_ICULTURE,
                    "title": title,
                    "summary": summary,
                    "venue_name": venue,
                    # 場次自己的日期優先，沒有才用活動整體的。場次日期比較準——
                    # 巡迴展在每個場館的檔期不同。
                    "start_date": start or top_start,
                    "end_date": end or top_end,
                    "source_url": str(source_url) if source_url else None,
                    "latitude": lat,
                    "longitude": lon,
                }
            )

    return records


def match_to_spirits(
    records, candidates, *, radius_m: float = DEFAULT_MATCH_RADIUS_M
) -> tuple[list[dict], list[dict]]:
    """
    把正規化後的活動對到地標。回 `(對上的, 沒對上的)`。

    沒對上的**一起回傳而不是丟掉**：那個數字是判斷半徑設得對不對的唯一依據。
    只回對上的話，「radius 設太小」跟「今天真的沒活動」在輸出上長得一模一樣。
    """
    matched: list[dict] = []
    unmatched: list[dict] = []

    candidates = list(candidates)
    for record in records:
        hit = nearest_within(
            record["latitude"], record["longitude"], candidates, radius_m=radius_m
        )
        if hit is None:
            unmatched.append(record)
            continue

        spirit_id, distance = hit
        row = {k: v for k, v in record.items() if k not in ("latitude", "longitude")}
        row["spirit_id"] = spirit_id
        row["distance_m"] = round(distance, 1)
        row["content_hash"] = content_fingerprint(row)
        matched.append(row)

    return matched, unmatched
