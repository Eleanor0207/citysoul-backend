"""
Ticket #2．人格載入服務可驗證（B3）。

驗收標準對照見 GitHub issue #2。0005 之後讀的是 `brain.character_personas`
三層結構，經由 `spirits.character_id` 找到角色；測試資料獨立於 seed script。
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.body.models import Spirit
from app.modules.brain.loader import load_active_persona
from app.modules.brain.models import Character, CharacterPersona, CitySoul, LandmarkSoul


@pytest.fixture
def character_id(db_session, unique_spirit_id):
    """
    一整條 city → landmark → character → spirit 的鏈，用完清乾淨。

    夾具做這麼多層是三層設計的真實成本：以前建一張人格卡只要一列。
    換來的是 city 基調可以共用、史實與人格分離。
    """
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    char_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(
            landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[]
        )
    )
    db_session.flush()
    db_session.add(Character(character_id=char_id, landmark_id=landmark_id))
    db_session.add(
        Spirit(
            spirit_id=unique_spirit_id,
            display_name="測試地標",
            character_id=char_id,
            landmark_id=landmark_id,
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            is_active=True,
        )
    )
    db_session.commit()

    yield char_id

    db_session.query(CharacterPersona).filter_by(character_id=char_id).delete()
    db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(Character).filter_by(character_id=char_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


def _make_persona(character_id: str, version: int, active: bool) -> CharacterPersona:
    return CharacterPersona(
        character_id=character_id,
        version=version,
        archetype=f"v{version}",
        speech_style="溫和",
        reviewed_by="test-reviewer",
        reviewed_at=datetime.now(timezone.utc),
        active=active,
    )


def test_returns_the_active_version(db_session, unique_spirit_id, character_id):
    db_session.add(_make_persona(character_id, version=1, active=True))
    db_session.commit()

    persona = load_active_persona(db_session, unique_spirit_id)

    assert persona is not None
    assert persona.character_id == character_id
    assert persona.version == 1
    assert persona.active is True


def test_returns_active_version_even_when_not_the_highest_version_number(
    db_session, unique_spirit_id, character_id
):
    """
    生效的不一定是版號最大的：version 3 可能是還在審核的草稿，而目前上線的
    仍然是 version 2。挑「最新」而不是挑「生效」會讓未審核內容直接見客。
    """
    db_session.add(_make_persona(character_id, version=1, active=False))
    db_session.add(_make_persona(character_id, version=2, active=True))
    db_session.add(_make_persona(character_id, version=3, active=False))
    db_session.commit()

    persona = load_active_persona(db_session, unique_spirit_id)

    assert persona is not None
    assert persona.version == 2


def test_returns_none_when_no_active_version_exists(db_session, unique_spirit_id, character_id):
    """草稿寫好但還沒審核通過，是封閉測試期的常態而不是例外。"""
    db_session.add(_make_persona(character_id, version=1, active=False))
    db_session.commit()

    assert load_active_persona(db_session, unique_spirit_id) is None


def test_returns_none_for_spirit_without_a_character(db_session, unique_spirit_id):
    """`spirits.character_id` 是 NULL 的靈魂（還沒接上人格）不該爆炸。"""
    db_session.add(
        Spirit(
            spirit_id=unique_spirit_id,
            display_name="沒有角色的地標",
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            is_active=True,
        )
    )
    db_session.commit()
    try:
        assert load_active_persona(db_session, unique_spirit_id) is None
    finally:
        db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
        db_session.commit()


def test_returns_none_for_nonexistent_spirit_id(db_session, unique_spirit_id):
    assert load_active_persona(db_session, unique_spirit_id) is None


def test_two_versions_cannot_be_active_at_once(db_session, character_id):
    """
    「一個角色同時只能有一個生效版本」由 partial unique index 保證，不是靠
    寫入時記得先把舊版關掉。少了它，`load_active_persona` 會任意挑一個，
    而且兩個版本都是「合法」的——沒有任何地方會報錯。
    """
    db_session.add(_make_persona(character_id, version=1, active=True))
    db_session.add(_make_persona(character_id, version=2, active=True))

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_many_inactive_versions_are_fine(db_session, character_id):
    """partial index 只約束生效的那一列。歷史版本要能無限累積。"""
    for version in range(1, 5):
        db_session.add(_make_persona(character_id, version=version, active=False))
    db_session.commit()

    assert (
        db_session.query(CharacterPersona).filter_by(character_id=character_id).count() == 4
    )
