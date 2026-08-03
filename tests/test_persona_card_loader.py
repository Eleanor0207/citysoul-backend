"""
Ticket #2．人格卡載入服務可驗證（B3）。

驗收標準對照見 GitHub issue #2。測試資料獨立於 seed script（用 unique_spirit_id
夾具），不依賴 scripts.init_db 是否已經跑過。
"""
from datetime import datetime, timezone

import pytest

from app.modules.brain.loader import load_active_persona_card
from app.modules.brain.models import PersonaCard


@pytest.fixture(autouse=True)
def _cleanup(db_session, unique_spirit_id):
    yield
    db_session.query(PersonaCard).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.commit()


def _make_card(spirit_id: str, version: int, is_active: bool) -> PersonaCard:
    return PersonaCard(
        spirit_id=spirit_id,
        version=version,
        content={"schema_version": 1, "core_personality": f"v{version}"},
        reviewed_by="test-reviewer",
        reviewed_at=datetime.now(timezone.utc),
        is_active=is_active,
    )


def test_returns_the_active_version(db_session, unique_spirit_id):
    db_session.add(_make_card(unique_spirit_id, version=1, is_active=True))
    db_session.commit()

    card = load_active_persona_card(db_session, unique_spirit_id)

    assert card is not None
    assert card.spirit_id == unique_spirit_id
    assert card.version == 1
    assert card.is_active is True


def test_returns_active_version_even_when_not_the_highest_version_number(
    db_session, unique_spirit_id
):
    db_session.add(_make_card(unique_spirit_id, version=1, is_active=False))
    db_session.add(_make_card(unique_spirit_id, version=2, is_active=True))
    db_session.add(_make_card(unique_spirit_id, version=3, is_active=False))
    db_session.commit()

    card = load_active_persona_card(db_session, unique_spirit_id)

    assert card is not None
    assert card.version == 2


def test_returns_none_when_no_active_version_exists(db_session, unique_spirit_id):
    db_session.add(_make_card(unique_spirit_id, version=1, is_active=False))
    db_session.commit()

    card = load_active_persona_card(db_session, unique_spirit_id)

    assert card is None


def test_returns_none_for_nonexistent_spirit_id(db_session, unique_spirit_id):
    card = load_active_persona_card(db_session, unique_spirit_id)

    assert card is None
