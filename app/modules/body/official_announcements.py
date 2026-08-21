"""
地標官方公告的自動抓取（backend#71／SDD §20.3.3）。

## 這是生產者，不是新的輸入類別

§20.1.2 的三類白名單**維持不變**。「地標官方公開活動」本來就是第二類——先前
受限的是它的生產者只能是人工錄入。這支模組是那一類的第二個生產者。

抓到的公告寫進 `brain.daily_event_calendars` 的 `official_event` 列，跟人工錄入
的資料**長得一模一樣**。B9 那邊完全不用改：對 `build_daily_event_inputs()` 而言，
一列是人打的還是排程抓的沒有區別。

⚠️ **仍然禁止**：即時天氣 API 作為 B9 輸入、社群內容（Threads／FB／IG 等 UGC）、
非官方的媒體報導。`daily_event.py` 那條掃描原始碼的測試繼續有效——**這支模組
刻意放在 body 而不是 brain**，因為它會對外抓資料，而 brain 不准。

## 三條硬規則

1. **來源是明列的 URL，不是搜尋**。每個地標一份，指向該廟方／館方自己的 RSS
   或公告頁。不搜尋、不聚合、不跟著連結跳轉。
2. **網域必須相符**。RSS 裡的項目連結若指向別的網域（轉貼、媒體二手報導），
   整條丟掉——來源清單保證的是「這個網域是官方的」，不是「這個 feed 裡的東西
   都是官方的」。
3. **原文不直接給玩家看**。它只是 B9 的輸入，由人格卡的口吻重寫。官方公告是
   公關文，直接顯示會讓那天的靈魂突然變成佈告欄（§20.3.3）。

## 抓不到就是今天沒有公告

不重試、不告警。官網暫時掛掉、改版、憑證過期——那些都不該變成我們的事故，
而當日情境本來就有 fallback（§20.1.3）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app.modules.brain.models import DailyEventCalendar

logger = logging.getLogger(__name__)

# 公告的預設展示期間。RSS 很少帶結束日期，而「這則公告從哪天到哪天算數」
# 是 B9 需要的東西——沒有的話只能猜，這裡把猜法寫明。
DEFAULT_WINDOW_DAYS = 7

FETCH_TIMEOUT_SECONDS = 10.0

# 一次最多收幾則。官網的 feed 可能有上百筆歷史公告，而當日情境一天只用得到
# 幾則——多抓的部分只會讓日曆表變成公告封存庫。
MAX_ITEMS_PER_SOURCE = 10

REVIEWED_BY = "official_feed"


@dataclass(frozen=True)
class OfficialSource:
    """一個地標的官方公告來源。"""

    place_id: str
    feed_url: str

    @property
    def domain(self) -> str:
        return urlparse(self.feed_url).netloc.lower()


@dataclass(frozen=True)
class Announcement:
    """抓到的一則公告。"""

    place_id: str
    title: str
    link: str | None
    start_date: date
    end_date: date


class AnnouncementFetcher:
    """
    抽象出來是為了測試——真的去打官網的話，測試會依賴別人的網站活著，
    而最需要測的正是「對方回了奇怪的東西時我們怎麼辦」。
    """

    def fetch(self, source: OfficialSource) -> str | None:
        """回傳 feed 的原始內容；失敗回 None（不拋例外）。"""
        try:
            response = httpx.get(source.feed_url, timeout=FETCH_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except Exception:  # noqa: BLE001
            # 官網掛掉不是我們的事故，見模組說明。
            logger.warning("official feed fetch failed: %s", source.place_id, exc_info=True)
            return None


class FakeAnnouncementFetcher(AnnouncementFetcher):
    """測試用。放在正式程式碼而不是 tests/ 底下，理由同其他 fake。"""

    def __init__(self, payload_by_place: dict[str, str | None] | None = None):
        self.payload_by_place = payload_by_place or {}
        self.calls: list[str] = []

    def fetch(self, source: OfficialSource) -> str | None:
        self.calls.append(source.place_id)
        return self.payload_by_place.get(source.place_id)


def parse_feed(source: OfficialSource, raw: str, *, today: date) -> list[Announcement]:
    """
    把 RSS／Atom 拆成公告清單。

    ## 為什麼自己拆而不是用 feedparser

    我們只需要標題與連結兩個欄位，而多一個第三方套件就多一份要跟著更新的相依。
    `xml.etree` 在標準庫裡，而且**壞掉的 XML 會丟例外而不是猜**——那正是我們
    要的行為：官網改版把 feed 弄壞時，寧可當作今天沒有公告。

    ## 網域不符的項目整條丟掉

    來源清單保證的是「這個**網域**是官方的」，不是「這個 feed 裡的東西都是
    官方的」。轉貼與媒體二手報導會帶著別人的連結出現在官方 feed 裡（§20.3.3）。
    """
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        logger.warning("official feed is not valid XML: %s", source.place_id)
        return []

    announcements: list[Announcement] = []

    # RSS 的 <item> 與 Atom 的 <entry> 只差標籤名，欄位語意一樣。
    items = root.iter("item")
    entries = [element for element in root.iter() if element.tag.endswith("}entry")]

    for element in list(items) + entries:
        if len(announcements) >= MAX_ITEMS_PER_SOURCE:
            break

        title = _text_of(element, "title")
        if not title:
            continue

        link = _link_of(element)
        if link and not _same_domain(link, source.domain):
            logger.info(
                "dropping off-domain item from %s feed: %s", source.place_id, link
            )
            continue

        announcements.append(
            Announcement(
                place_id=source.place_id,
                title=title.strip(),
                link=link,
                start_date=today,
                end_date=today + timedelta(days=DEFAULT_WINDOW_DAYS),
            )
        )

    return announcements


def _text_of(element, tag: str) -> str | None:
    for child in element:
        if child.tag == tag or child.tag.endswith("}" + tag):
            return child.text
    return None


def _link_of(element) -> str | None:
    for child in element:
        if child.tag == "link":
            return (child.text or "").strip() or None
        if child.tag.endswith("}link"):
            # Atom 把連結放在屬性裡。
            return child.attrib.get("href")
    return None


def _same_domain(link: str, domain: str) -> bool:
    host = urlparse(link).netloc.lower()
    return host == domain or host.endswith("." + domain)


def store(db: Session, announcements: list[Announcement]) -> int:
    """
    把公告寫進日曆表，回傳新增的筆數。

    ## 去重靠「同一個地標 ＋ 同一個標題 ＋ 同一個起日」

    排程每天跑，而官網的公告會連續掛好幾天——沒有去重的話，一則公告會在日曆
    表裡累積成一疊。這裡用查詢去重而不是 UNIQUE 約束，因為人工錄入的列本來
    就可能有相同標題（例如每年的「安太歲法會」），不該被資料庫擋下來。

    ## `active=True` 是刻意的

    §20.3.3 已經把「官方網域」定義成可信來源，所以抓進來就生效——**它不會直接
    給玩家看**，只是 B9 的輸入，由人格卡口吻重寫。人工錄入的列仍然照原本的
    審核流程走。
    """
    added = 0

    for announcement in announcements:
        exists = (
            db.query(DailyEventCalendar)
            .filter_by(
                place_id=announcement.place_id,
                title=announcement.title,
                start_date=announcement.start_date,
            )
            .first()
        )
        if exists is not None:
            continue

        db.add(
            DailyEventCalendar(
                place_id=announcement.place_id,
                event_type="official_event",
                title=announcement.title,
                date_rule="gregorian_range",
                start_date=announcement.start_date,
                end_date=announcement.end_date,
                active=True,
                reviewed_by=REVIEWED_BY,
            )
        )
        added += 1

    if added:
        db.commit()

    return added


def refresh_official_announcements(
    db: Session,
    sources: list[OfficialSource],
    *,
    today: date,
    fetcher: AnnouncementFetcher | None = None,
) -> int:
    """
    跑一輪抓取，回傳新增的公告筆數。

    來源清單為空時直接回 0——那是**目前的正常狀態**（2026-08-21：網址待提供），
    不是設定錯誤。
    """
    if not sources:
        logger.info("no official announcement sources configured")
        return 0

    fetcher = fetcher or AnnouncementFetcher()
    added = 0

    for source in sources:
        raw = fetcher.fetch(source)
        if not raw:
            continue

        added += store(db, parse_feed(source, raw, today=today))

    return added
