"""
backend#71．地標官方公告的自動抓取（SDD §20.3.3）。

外部網站以 fake 注入——**不打任何真實官網**。理由不只是速度：最需要測的是
「對方回了奇怪的東西時我們怎麼辦」，而你沒辦法要求別人的網站下一次回壞掉的 XML。

⚠️ 這裡最重要的一組是**來源邊界**：非官方網域的項目要被丟掉。那條規則壞掉的
症狀是「遊戲裡出現了沒有人審核過的第三方文字」，而它不會有任何錯誤訊息。
"""
from datetime import date, timedelta

import pytest

from app.modules.body.official_announcements import (
    DEFAULT_WINDOW_DAYS,
    MAX_ITEMS_PER_SOURCE,
    Announcement,
    FakeAnnouncementFetcher,
    OfficialSource,
    parse_feed,
    refresh_official_announcements,
    store,
)
from app.modules.brain.models import DailyEventCalendar

_TODAY = date(2026, 8, 21)
_SOURCE = OfficialSource(
    place_id="longshan_temple", feed_url="https://example-temple.org.tw/news/rss.xml"
)


def _rss(*items: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        + "".join(items)
        + "</channel></rss>"
    )


def _item(title: str, link: str = "https://example-temple.org.tw/news/1") -> str:
    return f"<item><title>{title}</title><link>{link}</link></item>"


# ── 解析 ───────────────────────────────────────────────────────────────

def test_an_official_item_becomes_an_announcement():
    parsed = parse_feed(_SOURCE, _rss(_item("中元普渡法會")), today=_TODAY)

    assert len(parsed) == 1
    assert parsed[0].title == "中元普渡法會"
    assert parsed[0].start_date == _TODAY
    assert parsed[0].end_date == _TODAY + timedelta(days=DEFAULT_WINDOW_DAYS)


def test_an_off_domain_item_is_dropped():
    # 🔒 來源清單保證的是「這個**網域**是官方的」，不是「這個 feed 裡的東西
    # 都是官方的」。官方 feed 裡出現轉貼與媒體報導是常見的事，而那些沒有經過
    # 任何審核——放進去等於把第三方文字直接送進遊戲。
    parsed = parse_feed(
        _SOURCE,
        _rss(
            _item("中元普渡法會"),
            _item("某媒體的報導", link="https://news.example.com/story/123"),
        ),
        today=_TODAY,
    )

    assert [a.title for a in parsed] == ["中元普渡法會"]


def test_a_subdomain_of_the_official_domain_is_accepted():
    parsed = parse_feed(
        _SOURCE,
        _rss(_item("特展", link="https://event.example-temple.org.tw/2026")),
        today=_TODAY,
    )

    assert len(parsed) == 1


def test_broken_xml_is_treated_as_no_announcements():
    # 官網改版把 feed 弄壞時，寧可當作今天沒有公告——當日情境本來就有節慶
    # 日曆與輪播池兩個來源。
    assert parse_feed(_SOURCE, "<rss><channel><item>", today=_TODAY) == []


def test_atom_entries_are_parsed_too():
    atom = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>秋季特展</title>"
        '<link href="https://example-temple.org.tw/exhibit"/></entry>'
        "</feed>"
    )

    parsed = parse_feed(_SOURCE, atom, today=_TODAY)

    assert [a.title for a in parsed] == ["秋季特展"]


def test_only_a_bounded_number_of_items_is_taken():
    # 官網的 feed 可能有上百筆歷史公告，而當日情境一天只用得到幾則。多抓的
    # 部分只會讓日曆表變成公告封存庫。
    many = _rss(*[_item(f"公告 {n}") for n in range(MAX_ITEMS_PER_SOURCE + 5)])

    assert len(parse_feed(_SOURCE, many, today=_TODAY)) == MAX_ITEMS_PER_SOURCE


def test_an_item_without_a_title_is_skipped():
    parsed = parse_feed(_SOURCE, _rss("<item><link>https://example-temple.org.tw/x</link></item>"), today=_TODAY)

    assert parsed == []


# ── 寫入 ───────────────────────────────────────────────────────────────

@pytest.fixture
def _clean(db_session):
    yield
    db_session.query(DailyEventCalendar).filter_by(place_id="longshan_temple").delete()
    db_session.commit()


def test_storing_the_same_announcement_twice_adds_one_row(db_session, _clean):
    # 排程每天跑，而官網的公告會連續掛好幾天。沒有去重的話，一則公告會在
    # 日曆表裡累積成一疊。
    announcement = Announcement(
        place_id="longshan_temple",
        title="中元普渡法會",
        link=None,
        start_date=_TODAY,
        end_date=_TODAY + timedelta(days=7),
    )

    assert store(db_session, [announcement]) == 1
    assert store(db_session, [announcement]) == 0

    rows = (
        db_session.query(DailyEventCalendar)
        .filter_by(place_id="longshan_temple", title="中元普渡法會")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].event_type == "official_event"
    assert rows[0].date_rule == "gregorian_range"


# ── 一輪抓取 ───────────────────────────────────────────────────────────

def test_no_sources_configured_is_not_an_error(db_session):
    # 2026-08-21 起的正常狀態：網址待提供。當日情境照常走節慶日曆與輪播池。
    assert refresh_official_announcements(db_session, [], today=_TODAY) == 0


def test_a_failed_fetch_does_not_stop_the_other_sources(db_session, _clean):
    # 一家官網掛掉不該讓其他地標的公告也抓不到。
    fetcher = FakeAnnouncementFetcher(
        {
            "broken_place": None,
            "longshan_temple": _rss(_item("中元普渡法會")),
        }
    )
    sources = [
        OfficialSource(place_id="broken_place", feed_url="https://down.example.com/rss"),
        _SOURCE,
    ]

    added = refresh_official_announcements(
        db_session, sources, today=_TODAY, fetcher=fetcher
    )

    assert added == 1
    assert fetcher.calls == ["broken_place", "longshan_temple"]
