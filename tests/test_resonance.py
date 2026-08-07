"""
Ticket #16．共鳴值資料表與判定邏輯（S5）。

驗收標準對照見 GitHub issue #16。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.resonance import (
    AMOUNT_ENCOUNTER_COLLECTION,
    AMOUNT_QUEST,
    RESONANCE_THRESHOLDS,
    SOURCE_ENCOUNTER_COLLECTION,
    SOURCE_QUEST,
    apply_resonance,
    next_threshold,
    stage_for_value,
)


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="測試地標",
        latitude=25.0955,
        longitude=121.5186,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=row.spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=row.spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"])


def _award(db_session, player_id, spirit_id, *, source_type=SOURCE_QUEST, source_id=None, amount=AMOUNT_QUEST):
    return apply_resonance(
        db_session,
        player_id=player_id,
        spirit_id=spirit_id,
        source_type=source_type,
        source_id=source_id or f"src-{uuid.uuid4()}",
        amount=amount,
    )


# ── 純函式：門檻換算 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected_stage",
    [(0, 0), (9, 0), (10, 1), (39, 1), (40, 2), (99, 2), (100, 3), (250, 3)],
)
def test_stage_for_value(value, expected_stage):
    """門檻 10/40/100 含邊界（剛好等於門檻就算達標）。"""
    assert stage_for_value(value) == expected_stage


@pytest.mark.parametrize(
    "value,expected", [(0, 10), (9, 10), (10, 40), (39, 40), (40, 100), (99, 100), (100, None)]
)
def test_next_threshold(value, expected):
    assert next_threshold(value) == expected


def test_thresholds_match_context_definition():
    """守門測試：門檻是 CONTEXT.md 明訂的共用固定值，不該被隨手改掉。"""
    assert RESONANCE_THRESHOLDS == (10, 40, 100)


# ── 基本入帳 ───────────────────────────────────────────────────────────

def test_first_award_creates_row(db_session, player, spirit):
    result = _award(db_session, player, spirit.spirit_id, amount=AMOUNT_QUEST)

    assert result.awarded is True
    assert result.resonance_value == 20
    assert result.stage == 1

    row = db_session.query(models.Resonance).filter_by(
        player_id=player, spirit_id=spirit.spirit_id
    ).one()
    assert row.resonance_value == 20
    assert row.stage == 1


def test_awards_accumulate(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, amount=AMOUNT_QUEST)
    result = _award(db_session, player, spirit.spirit_id, amount=AMOUNT_QUEST)

    assert result.resonance_value == 40
    assert result.awarded is True


def test_event_ledger_records_each_award(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, source_id="quest-a")
    _award(db_session, player, spirit.spirit_id, source_id="quest-b")

    count = db_session.query(models.ResonanceEvent).filter_by(
        player_id=player, spirit_id=spirit.spirit_id
    ).count()
    assert count == 2


# ── 重複入帳 ───────────────────────────────────────────────────────────

def test_same_source_is_not_credited_twice(db_session, player, spirit):
    """AC：同一 source_type + source_id 重複呼叫不重複加值。"""
    first = _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    second = _award(db_session, player, spirit.spirit_id, source_id="quest-001")

    assert first.awarded is True
    assert second.awarded is False
    assert second.resonance_value == first.resonance_value  # 沒有再加上去


def test_duplicate_does_not_raise(db_session, player, spirit):
    """
    AC 明講「不是讓例外往外拋炸掉呼叫端」。重複提交是正常的使用者行為
    （網路重試、連點兩下），不是錯誤。
    """
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")  # 不該拋


def test_session_still_usable_after_duplicate(db_session, player, spirit):
    """
    撞到 UNIQUE 之後 session 要還能用——這是用 savepoint 而不是讓整個
    transaction 髒掉的理由。後面接著入帳另一筆來源必須成功。
    """
    _award(db_session, player, spirit.spirit_id, source_id="quest-001", amount=AMOUNT_QUEST)
    _award(db_session, player, spirit.spirit_id, source_id="quest-001", amount=AMOUNT_QUEST)

    result = _award(db_session, player, spirit.spirit_id, source_id="quest-002", amount=AMOUNT_QUEST)

    assert result.awarded is True
    assert result.resonance_value == 40


def test_duplicate_writes_no_extra_ledger_row(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")

    count = db_session.query(models.ResonanceEvent).filter_by(
        player_id=player, source_id="quest-001"
    ).count()
    assert count == 1


def test_same_source_id_different_source_type_is_a_separate_award(db_session, player, spirit):
    """UNIQUE 是 (player_id, source_type, source_id)，type 不同就是不同來源。"""
    first = _award(
        db_session, player, spirit.spirit_id, source_type=SOURCE_QUEST, source_id="X", amount=20
    )
    second = _award(
        db_session,
        player,
        spirit.spirit_id,
        source_type=SOURCE_ENCOUNTER_COLLECTION,
        source_id="X",
        amount=10,
    )

    assert first.awarded is True
    assert second.awarded is True
    assert second.resonance_value == 30


def test_same_source_is_blocked_across_spirits(db_session, player, spirit, client):
    """
    SDD 的 UNIQUE **不含 spirit_id**，所以同一個 player 的同一個 source_id
    跨靈魂也只能入帳一次。這條容易被誤以為是 bug，用測試把規格釘住。
    """
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        first = _award(db_session, player, spirit.spirit_id, source_id="shared-id")
        second = _award(db_session, player, other.spirit_id, source_id="shared-id")

        assert first.awarded is True
        assert second.awarded is False
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()


# ── 門檻解鎖 ───────────────────────────────────────────────────────────

def test_crossing_first_threshold_reports_unlock(db_session, player, spirit):
    """0 → 10：跨過第一個門檻。"""
    result = _award(
        db_session, player, spirit.spirit_id, amount=AMOUNT_ENCOUNTER_COLLECTION
    )

    assert result.newly_unlocked_stages == [1]
    assert result.has_new_unlock is True


def test_not_crossing_a_threshold_reports_no_unlock(db_session, player, spirit):
    """
    10 → 30：已經在 stage 1，還沒到 40，不該回報新解鎖。
    """
    _award(db_session, player, spirit.spirit_id, amount=10, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=20, source_id="b")

    assert result.resonance_value == 30
    assert result.stage == 1
    assert result.newly_unlocked_stages == []
    assert result.has_new_unlock is False


def test_below_first_threshold_reports_no_unlock(db_session, player, spirit):
    result = _award(db_session, player, spirit.spirit_id, amount=5)

    assert result.stage == 0
    assert result.newly_unlocked_stages == []


def test_crossing_second_threshold(db_session, player, spirit):
    """30 → 50：跨過 40。"""
    _award(db_session, player, spirit.spirit_id, amount=30, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=20, source_id="b")

    assert result.stage == 2
    assert result.newly_unlocked_stages == [2]


def test_crossing_multiple_thresholds_reports_each(db_session, player, spirit):
    """
    5 → 55 一次跨過 10 與 40，兩個 stage 都要回報。

    MVP 的 10／20 點跨不到兩個門檻，但每個新 stage 都該有自己的一段敘事，
    只回報最後一個會讓中間那段靜默消失——不會有任何錯誤訊息提醒。
    """
    _award(db_session, player, spirit.spirit_id, amount=5, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=50, source_id="b")

    assert result.resonance_value == 55
    assert result.stage == 2
    assert result.newly_unlocked_stages == [1, 2]


def test_duplicate_award_reports_no_unlock(db_session, player, spirit):
    """重複入帳沒有加值，自然也不該回報解鎖。"""
    first = _award(db_session, player, spirit.spirit_id, amount=10, source_id="dup")
    assert first.newly_unlocked_stages == [1]

    second = _award(db_session, player, spirit.spirit_id, amount=10, source_id="dup")
    assert second.newly_unlocked_stages == []
    assert second.stage == 1


# ── 兩種來源（AC 指名的情境）──────────────────────────────────────────

def test_encounter_collection_awards_ten_once(db_session, player, spirit):
    """相遇收藏：10 點，同一玩家對同一靈魂只加一次。"""
    first = _award(
        db_session,
        player,
        spirit.spirit_id,
        source_type=SOURCE_ENCOUNTER_COLLECTION,
        source_id=spirit.spirit_id,
        amount=AMOUNT_ENCOUNTER_COLLECTION,
    )
    second = _award(
        db_session,
        player,
        spirit.spirit_id,
        source_type=SOURCE_ENCOUNTER_COLLECTION,
        source_id=spirit.spirit_id,
        amount=AMOUNT_ENCOUNTER_COLLECTION,
    )

    assert first.awarded is True
    assert first.resonance_value == 10
    assert second.awarded is False
    assert second.resonance_value == 10


def test_quest_awards_twenty_per_distinct_quest(db_session, player, spirit):
    """可驗證微任務：每個不同任務各 20 點，可以疊加。"""
    _award(db_session, player, spirit.spirit_id, source_type=SOURCE_QUEST, source_id="q1", amount=AMOUNT_QUEST)
    result = _award(
        db_session, player, spirit.spirit_id, source_type=SOURCE_QUEST, source_id="q2", amount=AMOUNT_QUEST
    )

    assert result.resonance_value == 40
    assert result.stage == 2


def test_mvp_amounts_match_spec():
    """守門測試：MVP 固定值（CONTEXT.md／SDD 第7.5節）。"""
    assert AMOUNT_ENCOUNTER_COLLECTION == 10
    assert AMOUNT_QUEST == 20


# ── 隔離 ───────────────────────────────────────────────────────────────

def test_resonance_is_per_player(db_session, player, spirit, client):
    other_player = uuid.UUID(
        client.post(
            "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
        ).json()["player_id"]
    )

    _award(db_session, player, spirit.spirit_id, amount=20)
    result = _award(db_session, other_player, spirit.spirit_id, amount=20)

    assert result.resonance_value == 20  # 沒有把別人的分數算進來


def test_resonance_is_per_spirit(db_session, player, spirit):
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        _award(db_session, player, spirit.spirit_id, amount=20, source_id="a")
        result = _award(db_session, player, other.spirit_id, amount=20, source_id="b")

        assert result.resonance_value == 20
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()
