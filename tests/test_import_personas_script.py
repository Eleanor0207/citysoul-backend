"""`scripts/import_personas.py` 在 MVP 取消人工審核之後的行為。

2026-08-18 的決定：匯入即上線。這裡釘住那個決定的三個後果，因為它們都是
「壞掉的時候不會報錯」的那一類：

1. 新版本進來，舊版本要被關掉——沒關掉的話偏索引會擋，但**擋在哪一列不確定**
2. 少一條 taboo 要整批拒收，而且**一個字都不能寫進去**
3. 沒有人看過的內容，`reviewed_by` 要留下 `MVP_NO_REVIEW` 這個查得到的值

⚠️ 這些測試跑真的資料庫，不是假的連線物件。匯入器自己建 engine、自己開交易，
用假連線只能驗到「有沒有送出那幾句 SQL」，驗不到偏索引會不會擋、整批拒收是不是
真的沒寫進去——而那兩件事正是這支腳本存在的理由。
"""
import pathlib
import sys
import uuid

import pytest
import yaml
from sqlalchemy import text

from app.modules.brain.models import Character, CharacterPersona, CitySoul, LandmarkSoul
from scripts import import_personas

FLOOR = "代替神明給予指示或應許"


@pytest.fixture
def character(db_session):
    """一個乾淨的角色，測試結束後連同它的人格版本一起清掉。"""
    suffix = uuid.uuid4()
    city_id = f"city-{suffix}"
    landmark_id = f"lm-{suffix}"
    character_id = f"ch-{suffix}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(
            landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[]
        )
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    db_session.commit()

    yield character_id

    db_session.execute(
        text("DELETE FROM brain.canned_greetings WHERE character_id = :c"),
        {"c": character_id},
    )
    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


def _write(directory: pathlib.Path, character_id: str, version: int, **overrides) -> None:
    card = {
        "character_id": character_id,
        "version": version,
        "archetype": "看過這條街很久的守望者。",
        "speech_style": "溫和、不疾不徐。",
        "taboos": [FLOOR],
        "reviewed_by": "PENDING_HUMAN_REVIEW",
    }
    card.update(overrides)
    path = directory / f"{character_id}_v{version}.yaml"
    path.write_text(yaml.safe_dump(card, allow_unicode=True), encoding="utf-8")


def _import(monkeypatch, directory: pathlib.Path) -> int:
    monkeypatch.setattr(
        sys, "argv", ["import_personas", "--persona-dir", str(directory)]
    )
    return import_personas.main()


def _versions(db_session, character_id):
    db_session.expire_all()
    rows = (
        db_session.query(CharacterPersona)
        .filter_by(character_id=character_id)
        .order_by(CharacterPersona.version)
        .all()
    )
    return {r.version: r for r in rows}


def test_import_activates_the_new_version(monkeypatch, tmp_path, db_session, character):
    _write(tmp_path, character, 1)

    assert _import(monkeypatch, tmp_path) == 0

    rows = _versions(db_session, character)
    assert rows[1].active is True


def test_a_second_version_takes_over_and_the_first_steps_down(
    monkeypatch, tmp_path, db_session, character
):
    """
    偏索引 `uq_character_personas_active` 只允許一列 active。如果匯入器沒有先關掉
    舊版本，這裡會是資料庫層的違反約束而不是斷言失敗——兩種都算紅燈，但差別在於
    後者代表「順序寫反了」，而順序正是這支腳本唯一需要小心的地方。
    """
    _write(tmp_path, character, 1)
    assert _import(monkeypatch, tmp_path) == 0

    _write(tmp_path, character, 2)
    assert _import(monkeypatch, tmp_path) == 0

    rows = _versions(db_session, character)
    assert rows[1].active is False
    assert rows[2].active is True
    assert sum(1 for r in rows.values() if r.active) == 1


def test_dropping_a_taboo_rejects_the_batch_without_writing(
    monkeypatch, tmp_path, db_session, character
):
    """安全下限只能往上加。這道檢查刻意沒有隨人工審核一起被拿掉。"""
    _write(tmp_path, character, 1)
    assert _import(monkeypatch, tmp_path) == 0

    _write(tmp_path, character, 2, taboos=["改成一條無關的禁忌"])
    assert _import(monkeypatch, tmp_path) == 1

    rows = _versions(db_session, character)
    assert 2 not in rows, "被拒收的版本不該留下任何一列"
    assert rows[1].active is True, "上一版仍然是生效的那一版"


def test_adding_a_taboo_is_allowed(monkeypatch, tmp_path, db_session, character):
    _write(tmp_path, character, 1)
    assert _import(monkeypatch, tmp_path) == 0

    _write(tmp_path, character, 2, taboos=[FLOOR, "另外加一條"])
    assert _import(monkeypatch, tmp_path) == 0

    rows = _versions(db_session, character)
    assert set(rows[2].taboos) == {FLOOR, "另外加一條"}
    assert rows[2].active is True


def test_unreviewed_content_is_recorded_as_such(
    monkeypatch, tmp_path, db_session, character
):
    """
    `reviewed_by` 不改成可空，是為了讓「這份內容沒有人看過」是一個查得到的值。
    日後恢復審核時，要重看哪些，查這個字串就知道。
    """
    _write(tmp_path, character, 1)
    assert _import(monkeypatch, tmp_path) == 0

    assert _versions(db_session, character)[1].reviewed_by == "MVP_NO_REVIEW"


def test_reimporting_the_same_version_changes_nothing(
    monkeypatch, tmp_path, db_session, character
):
    """同一個 (character_id, version) 已存在就跳過——不覆寫，也不報錯。"""
    _write(tmp_path, character, 1)
    assert _import(monkeypatch, tmp_path) == 0
    before = _versions(db_session, character)[1].archetype

    _write(tmp_path, character, 1, archetype="改過的內容，不該生效。")
    assert _import(monkeypatch, tmp_path) == 0

    assert _versions(db_session, character)[1].archetype == before
