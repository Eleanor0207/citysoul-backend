"""`scripts/import_daily_event_calendars.py`（backend#71）。

這支腳本補的是一個**沒有症狀的缺口**：`brain.daily_event_calendars` 在 2026-08-22
之前只有讀取端，沒有任何寫入管道。所以這裡釘住的都是「壞掉的時候不會報錯」
那一類的行為：

1. 錄進去的日期，`daily_event_sources` 那天真的讀得到（不然只是寫進一張沒人看的表）
2. 重跑同一份檔案不會長出重複列（排程與手動都可能重跑）
3. `--prune` 不會誤刪抓取器寫的列（兩個生產者共用同一張表）
4. 日期規則填錯要整批擋下來，而不是安靜地寫進一列永遠不匹配的規則

⚠️ 這些測試跑真的資料庫。日期規則的四種形狀都由資料庫的 CHECK 約束把關，
用假連線只驗得到「有沒有送出 SQL」，驗不到形狀對不對——而形狀正是這支腳本
唯一需要小心的地方。
"""
import datetime
import pathlib
import sys
import uuid

import pytest
import yaml
from sqlalchemy import text

from app.modules.body import models
from app.modules.brain.daily_event_sources import build_daily_event_inputs
from app.modules.brain.models import DailyEventCalendar
from scripts import import_daily_event_calendars as importer


@pytest.fixture
def place(db_session):
    """一個乾淨的地標，測試結束後連同它的日曆列一起清掉。"""
    spirit_id = f"test-spirit-{uuid.uuid4()}"
    db_session.add(
        models.Spirit(
            spirit_id=spirit_id,
            display_name="測試地標",
            latitude=25.037398,
            longitude=121.499732,
        )
    )
    db_session.commit()

    yield spirit_id

    db_session.query(DailyEventCalendar).filter_by(place_id=spirit_id).delete()
    db_session.query(models.Spirit).filter_by(spirit_id=spirit_id).delete()
    db_session.commit()


def _write(directory: pathlib.Path, place_id: str, events: list[dict]) -> None:
    path = directory / f"{place_id}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"place_id": place_id, "reviewed_by": "Jessie_Lee", "events": events},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _import(monkeypatch, directory: pathlib.Path, *extra: str) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        ["import_daily_event_calendars", "--calendars-dir", str(directory), *extra],
    )
    return importer.main()


def _rows(db_session, place_id) -> list[DailyEventCalendar]:
    db_session.expire_all()
    return (
        db_session.query(DailyEventCalendar)
        .filter_by(place_id=place_id)
        .order_by(DailyEventCalendar.title)
        .all()
    )


# ── 錄進去的東西那天讀得到 ──────────────────────────────────────────


def test_a_one_off_event_is_visible_to_b9_on_its_days(
    monkeypatch, tmp_path, db_session, place
):
    """這是整支腳本存在的理由：錄一個 Demo 日期，那天 B9 拿得到它。"""
    _write(tmp_path, place, [
        {"title": "成果發表 Demo",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
    ])

    assert _import(monkeypatch, tmp_path) == 0

    on_the_day = build_daily_event_inputs(
        db_session, place_id=place, event_date=datetime.date(2026, 8, 31)
    )
    assert on_the_day.official_events == ["成果發表 Demo"]

    day_after = build_daily_event_inputs(
        db_session, place_id=place, event_date=datetime.date(2026, 9, 1)
    )
    assert day_after.official_events == []


def test_a_multi_day_event_covers_the_whole_range(
    monkeypatch, tmp_path, db_session, place
):
    _write(tmp_path, place, [
        {"title": "封測體驗場",
         "start_date": datetime.date(2026, 8, 29),
         "end_date": datetime.date(2026, 8, 31)},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    for day in (29, 30, 31):
        inputs = build_daily_event_inputs(
            db_session, place_id=place, event_date=datetime.date(2026, 8, day)
        )
        assert inputs.official_events == ["封測體驗場"], f"8/{day} 應該看得到"

    assert build_daily_event_inputs(
        db_session, place_id=place, event_date=datetime.date(2026, 8, 28)
    ).official_events == []


def test_a_lunar_festival_lands_on_the_right_solar_day(
    monkeypatch, tmp_path, db_session, place
):
    """農曆節日錄一次，每年的國曆日期由 lunardate 換算——這是不寫死年份的理由。"""
    _write(tmp_path, place, [
        {"title": "中秋節", "event_type": "festival",
         "lunar": {"start": "08-15", "end": "08-15"}},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    # 2026 年的農曆八月十五是國曆 9 月 25 日。
    assert build_daily_event_inputs(
        db_session, place_id=place, event_date=datetime.date(2026, 9, 25)
    ).festival == "中秋節"
    assert build_daily_event_inputs(
        db_session, place_id=place, event_date=datetime.date(2026, 9, 24)
    ).festival is None


def test_event_type_defaults_to_official_event(monkeypatch, tmp_path, db_session, place):
    """錄入者辦活動時不必知道 event_type 這個欄位存在。"""
    _write(tmp_path, place, [
        {"title": "特展開幕",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    assert _rows(db_session, place)[0].event_type == "official_event"


# ── 重跑安全 ────────────────────────────────────────────────────────


def test_rerunning_the_same_file_does_not_duplicate(
    monkeypatch, tmp_path, db_session, place
):
    """這張表刻意沒有 UNIQUE 約束，所以重複只能靠腳本自己擋。"""
    events = [{"title": "封測體驗場",
               "start_date": datetime.date(2026, 8, 29),
               "end_date": datetime.date(2026, 8, 31)}]
    _write(tmp_path, place, events)

    assert _import(monkeypatch, tmp_path) == 0
    assert _import(monkeypatch, tmp_path) == 0

    assert len(_rows(db_session, place)) == 1


def test_changing_the_end_date_updates_in_place(
    monkeypatch, tmp_path, db_session, place
):
    """延長活動是常見的事，不該變成第二列。"""
    _write(tmp_path, place, [
        {"title": "封測體驗場",
         "start_date": datetime.date(2026, 8, 29),
         "end_date": datetime.date(2026, 8, 30)},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    _write(tmp_path, place, [
        {"title": "封測體驗場",
         "start_date": datetime.date(2026, 8, 29),
         "end_date": datetime.date(2026, 9, 2)},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    rows = _rows(db_session, place)
    assert len(rows) == 1
    assert rows[0].end_date == datetime.date(2026, 9, 2)


def test_removing_an_event_needs_prune(monkeypatch, tmp_path, db_session, place):
    """從檔案裡刪掉不會自動刪資料庫——同輪播池的理由，刪除要明確。"""
    _write(tmp_path, place, [
        {"title": "封測體驗場",
         "start_date": datetime.date(2026, 8, 29),
         "end_date": datetime.date(2026, 8, 31)},
    ])
    assert _import(monkeypatch, tmp_path) == 0

    _write(tmp_path, place, [])
    assert _import(monkeypatch, tmp_path) == 0
    assert len(_rows(db_session, place)) == 1

    assert _import(monkeypatch, tmp_path, "--prune") == 0
    assert _rows(db_session, place) == []


def test_prune_leaves_the_fetchers_rows_alone(
    monkeypatch, tmp_path, db_session, place
):
    """
    人與排程共用同一張表。`--prune` 的語意是「這份檔案擁有人工錄入的列」，
    不是「這份檔案擁有這個地標的所有列」——搞混的話，一次 --prune 會把排程
    抓來的公告全部清掉，而且沒有任何錯誤訊息。
    """
    db_session.add(
        DailyEventCalendar(
            place_id=place,
            event_type="official_event",
            title="官網抓來的公告",
            date_rule="gregorian_range",
            start_date=datetime.date(2026, 8, 20),
            end_date=datetime.date(2026, 8, 27),
            active=True,
            reviewed_by=importer.FEED_REVIEWED_BY,
        )
    )
    db_session.commit()

    _write(tmp_path, place, [])
    assert _import(monkeypatch, tmp_path, "--prune") == 0

    titles = [row.title for row in _rows(db_session, place)]
    assert titles == ["官網抓來的公告"]


# ── 填錯要擋下來，而且一個字都不寫 ──────────────────────────────


def test_two_date_rules_in_one_event_is_rejected(
    monkeypatch, tmp_path, db_session, place
):
    _write(tmp_path, place, [
        {"title": "填錯的活動",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31),
         "lunar": {"start": "08-15", "end": "08-15"}},
    ])

    assert _import(monkeypatch, tmp_path) == 1
    assert _rows(db_session, place) == []


def test_no_date_rule_at_all_is_rejected(monkeypatch, tmp_path, db_session, place):
    _write(tmp_path, place, [{"title": "沒有日期的活動"}])

    assert _import(monkeypatch, tmp_path) == 1
    assert _rows(db_session, place) == []


def test_reversed_dates_are_rejected(monkeypatch, tmp_path, db_session, place):
    _write(tmp_path, place, [
        {"title": "顛倒的活動",
         "start_date": datetime.date(2026, 9, 2),
         "end_date": datetime.date(2026, 8, 29)},
    ])

    assert _import(monkeypatch, tmp_path) == 1
    assert _rows(db_session, place) == []


def test_a_bad_event_rejects_the_whole_batch(monkeypatch, tmp_path, db_session, place):
    """整批拒收，不是「好的先寫進去」——半套寫入比整批失敗難查。"""
    _write(tmp_path, place, [
        {"title": "好的活動",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
        {"title": "壞的活動", "event_type": "不存在的類型",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
    ])

    assert _import(monkeypatch, tmp_path) == 1
    assert _rows(db_session, place) == []


def test_unknown_place_id_is_rejected(monkeypatch, tmp_path, db_session):
    """
    這張表的 place_id 刻意不是外鍵，所以打錯字沒有任何症狀——那則活動只是
    永遠不會被讀到。腳本自己擋。
    """
    missing = f"test-spirit-{uuid.uuid4()}"
    _write(tmp_path, missing, [
        {"title": "掛在不存在的地標上",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
    ])

    assert _import(monkeypatch, tmp_path) == 1

    db_session.expire_all()
    assert db_session.query(DailyEventCalendar).filter_by(place_id=missing).all() == []


def test_dry_run_writes_nothing(monkeypatch, tmp_path, db_session, place):
    _write(tmp_path, place, [
        {"title": "只是看看",
         "start_date": datetime.date(2026, 8, 31),
         "end_date": datetime.date(2026, 8, 31)},
    ])

    assert _import(monkeypatch, tmp_path, "--dry-run") == 0
    assert _rows(db_session, place) == []


# ── 隨附的內容檔本身要能過 ──────────────────────────────────────


def test_the_shipped_content_files_validate():
    """
    `content/daily_event_calendars/*.yaml` 目前九份都是空的 events，那是正常
    狀態。這裡確保它們的骨架有效——不然第一次錄入時才會發現檔案壞掉。
    """
    loaded = importer.load_files(importer.CALENDARS_DIR)
    assert loaded, "content/daily_event_calendars 裡應該有九份地標檔"

    _, errors = importer.validate(loaded)
    assert errors == []


def test_the_template_is_not_picked_up_by_the_glob():
    """範本用 .example 副檔名，免得它的示範資料被當成真的活動匯進去。"""
    names = [path.name for path, _ in importer.load_files(importer.CALENDARS_DIR)]
    assert not any(name.startswith("_template") for name in names)


def test_the_template_itself_is_a_valid_example(tmp_path):
    """
    範本是錄入者唯一會照抄的東西。它自己過不了驗證的話，第一個使用者會以為
    是自己填錯。
    """
    template = importer.CALENDARS_DIR / "_template.yaml.example"
    copied = tmp_path / "longshan_temple.yaml"
    copied.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    by_place, errors = importer.validate(importer.load_files(tmp_path))
    assert errors == []
    # 五種寫法各一則：一次性單日、一次性多日、國曆節日、農曆節日、農曆月末。
    rules = sorted(row["date_rule"] for row in by_place["longshan_temple"])
    assert rules == [
        "gregorian_fixed",
        "gregorian_range",
        "gregorian_range",
        "lunar_fixed",
        "lunar_fixed",
        "lunar_month_end",
    ]
