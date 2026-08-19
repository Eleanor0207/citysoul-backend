"""
backend#72．劇情主線 beat 推進判定與寫入（`app/modules/body/story_progress.py`）。

測試用的 arc 是一條菱形因果鏈，刻意不是萬華主線本身的五個 beat：

    beat_a（序章，無角色）
      └── beat_b（角色 A → 靈魂 A）
      └── beat_c（角色 B → 靈魂 B）
            └── beat_finale（終點，無角色，前置 = [beat_b, beat_c]）

菱形結構同時測得到「兩條分支都要完成才能推進終點」與「終點共鳴值要發給
兩個靈魂」，比純線性鏈更貼近會犯錯的地方。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.resonance import SOURCE_STORY_COMPLETION
from app.modules.body.story_progress import (
    ArcNotFoundError,
    BeatNotFoundError,
    BeatNotUnlockedError,
    advance_beat,
    arc_state,
    is_terminal_beat,
    unlockable,
)
from app.modules.brain.models import Character, CitySoul, LandmarkSoul
from app.modules.brain.models import StoryArc, StoryBeat


@pytest.fixture
def arc(db_session, unique_spirit_id):
    arc_id = f"arc-{unique_spirit_id}"
    char_a = f"char-a-{unique_spirit_id}"
    char_b = f"char-b-{unique_spirit_id}"
    spirit_a_id = f"spirit-a-{unique_spirit_id}"
    spirit_b_id = f"spirit-b-{unique_spirit_id}"

    city_id = f"city-{unique_spirit_id}"
    landmark_a = f"lm-a-{unique_spirit_id}"
    landmark_b = f"lm-b-{unique_spirit_id}"

    db_session.add(StoryArc(arc_id=arc_id, title="測試主線", active=True))
    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(LandmarkSoul(landmark_id=landmark_a, city_id=city_id, name="地標A", founding_facts=[]))
    db_session.add(LandmarkSoul(landmark_id=landmark_b, city_id=city_id, name="地標B", founding_facts=[]))
    db_session.flush()
    db_session.add(Character(character_id=char_a, landmark_id=landmark_a))
    db_session.add(Character(character_id=char_b, landmark_id=landmark_b))
    db_session.add(models.Spirit(
        spirit_id=spirit_a_id, display_name="靈魂A", latitude=25.0, longitude=121.5,
        character_id=char_a, landmark_id=landmark_a, summon_radius_meters=50, is_active=True,
    ))
    db_session.add(models.Spirit(
        spirit_id=spirit_b_id, display_name="靈魂B", latitude=25.0, longitude=121.5,
        character_id=char_b, landmark_id=landmark_b, summon_radius_meters=50, is_active=True,
    ))
    db_session.flush()

    beat_a = f"beat-a-{unique_spirit_id}"
    beat_b = f"beat-b-{unique_spirit_id}"
    beat_c = f"beat-c-{unique_spirit_id}"
    beat_finale = f"beat-finale-{unique_spirit_id}"

    db_session.add_all([
        StoryBeat(
            beat_id=beat_a, arc_id=arc_id, character_id=None, sequence_order=1,
            trigger_condition="x", narrative_directive="x", prerequisite_beat_ids=None,
            active=True,
        ),
        StoryBeat(
            beat_id=beat_b, arc_id=arc_id, character_id=char_a, sequence_order=1,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=[beat_a], active=True,
        ),
        StoryBeat(
            beat_id=beat_c, arc_id=arc_id, character_id=char_b, sequence_order=1,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=[beat_a], active=True,
        ),
        StoryBeat(
            beat_id=beat_finale, arc_id=arc_id, character_id=None, sequence_order=2,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=[beat_b, beat_c], active=True,
        ),
    ])
    db_session.commit()

    class Fixture:
        pass

    f = Fixture()
    f.arc_id = arc_id
    f.beat_a, f.beat_b, f.beat_c, f.beat_finale = beat_a, beat_b, beat_c, beat_finale
    f.spirit_a_id, f.spirit_b_id = spirit_a_id, spirit_b_id

    yield f

    db_session.query(models.ResonanceEvent).filter(
        models.ResonanceEvent.spirit_id.in_([spirit_a_id, spirit_b_id])
    ).delete(synchronize_session=False)
    db_session.query(models.Resonance).filter(
        models.Resonance.spirit_id.in_([spirit_a_id, spirit_b_id])
    ).delete(synchronize_session=False)
    db_session.query(models.PlayersStoryProgress).filter(
        models.PlayersStoryProgress.beat_id.in_([beat_a, beat_b, beat_c, beat_finale])
    ).delete(synchronize_session=False)
    db_session.query(StoryBeat).filter_by(arc_id=arc_id).delete()
    db_session.query(models.Spirit).filter(
        models.Spirit.spirit_id.in_([spirit_a_id, spirit_b_id])
    ).delete(synchronize_session=False)
    db_session.query(Character).filter(Character.character_id.in_([char_a, char_b])).delete(
        synchronize_session=False
    )
    db_session.query(LandmarkSoul).filter(
        LandmarkSoul.landmark_id.in_([landmark_a, landmark_b])
    ).delete(synchronize_session=False)
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.query(StoryArc).filter_by(arc_id=arc_id).delete()
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _resonance_value(db_session, player_id, spirit_id):
    row = db_session.query(models.Resonance).filter_by(
        player_id=player_id, spirit_id=spirit_id
    ).first()
    return row.resonance_value if row else 0


# ── 純函式 ─────────────────────────────────────────────────────────────

def test_beat_with_no_prerequisites_is_always_unlockable(db_session, arc):
    beat = db_session.query(StoryBeat).filter_by(beat_id=arc.beat_a).one()
    assert unlockable(beat, completed_beat_ids=set())


def test_beat_needs_all_prerequisites_not_just_one(db_session, arc):
    beat = db_session.query(StoryBeat).filter_by(beat_id=arc.beat_finale).one()
    assert not unlockable(beat, completed_beat_ids={arc.beat_b})
    assert unlockable(beat, completed_beat_ids={arc.beat_b, arc.beat_c})


def test_only_the_beat_nothing_depends_on_is_terminal(db_session, arc):
    beats = db_session.query(StoryBeat).filter_by(arc_id=arc.arc_id).all()

    assert not is_terminal_beat(arc.beat_a, beats)
    assert not is_terminal_beat(arc.beat_b, beats)
    assert not is_terminal_beat(arc.beat_c, beats)
    assert is_terminal_beat(arc.beat_finale, beats)


# ── advance_beat：判定 ───────────────────────────────────────────────

def test_advancing_a_beat_without_prerequisites_succeeds(db_session, arc, player):
    pid, _ = player
    result = advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)

    assert result.already_completed is False
    assert result.story_completed is False
    assert result.completed_beat_ids == [arc.beat_a]


def test_advancing_without_prerequisite_met_raises(db_session, arc, player):
    pid, _ = player
    with pytest.raises(BeatNotUnlockedError):
        advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)


def test_advancing_after_prerequisite_met_succeeds(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    result = advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)

    assert result.already_completed is False
    assert set(result.completed_beat_ids) == {arc.beat_a, arc.beat_b}


def test_re_advancing_a_completed_beat_is_not_an_error(db_session, arc, player):
    """重複提交是正常的使用者行為，不是錯誤——跟 quests.complete_quest() 同一個道理。"""
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    result = advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)

    assert result.already_completed is True


def test_unknown_beat_id_raises_not_found(db_session, arc, player):
    pid, _ = player
    with pytest.raises(BeatNotFoundError):
        advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id="no-such-beat")


def test_beat_id_from_a_different_arc_raises_not_found(db_session, arc, player):
    """arc_id 跟 beat 對不起來，當成查無此節點——不是另一種錯誤形狀。"""
    pid, _ = player
    with pytest.raises(BeatNotFoundError):
        advance_beat(db_session, player_id=pid, arc_id="wrong-arc", beat_id=arc.beat_a)


# ── advance_beat：結局共鳴值 ─────────────────────────────────────────

def test_finishing_the_arc_awards_all_landmark_spirits(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_c)
    result = advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_finale)

    assert result.story_completed is True
    assert _resonance_value(db_session, pid, arc.spirit_a_id) == 30
    assert _resonance_value(db_session, pid, arc.spirit_b_id) == 30


def test_non_terminal_beats_do_not_award_story_completion(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    result = advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)

    assert result.story_completed is False
    assert _resonance_value(db_session, pid, arc.spirit_a_id) == 0


def test_re_finishing_an_already_completed_arc_does_not_double_award(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_c)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_finale)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_finale)

    assert _resonance_value(db_session, pid, arc.spirit_a_id) == 30


def test_story_completion_ledger_uses_arc_and_spirit_scoped_source_id(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_c)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_finale)

    events = db_session.query(models.ResonanceEvent).filter_by(
        player_id=pid, source_type=SOURCE_STORY_COMPLETION
    ).all()
    source_ids = {e.source_id for e in events}

    assert source_ids == {f"{arc.arc_id}:{arc.spirit_a_id}", f"{arc.arc_id}:{arc.spirit_b_id}"}


# ── arc_state ────────────────────────────────────────────────────────

def test_arc_state_starts_with_only_the_prerequisite_free_beat_eligible(db_session, arc, player):
    pid, _ = player
    state = arc_state(db_session, player_id=pid, arc_id=arc.arc_id)

    assert state["completed_beat_ids"] == []
    assert state["eligible_beat_ids"] == [arc.beat_a]
    assert state["story_completed"] is False


def test_arc_state_reflects_progress_and_completion(db_session, arc, player):
    pid, _ = player
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_a)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_b)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_c)
    advance_beat(db_session, player_id=pid, arc_id=arc.arc_id, beat_id=arc.beat_finale)

    state = arc_state(db_session, player_id=pid, arc_id=arc.arc_id)

    assert set(state["completed_beat_ids"]) == {arc.beat_a, arc.beat_b, arc.beat_c, arc.beat_finale}
    assert state["eligible_beat_ids"] == []
    assert state["story_completed"] is True


def test_arc_state_does_not_mutate_anything(db_session, arc, player):
    """唯讀查詢——呼叫兩次結果要一樣，不該把玩家的進度往前推。"""
    pid, _ = player
    first = arc_state(db_session, player_id=pid, arc_id=arc.arc_id)
    second = arc_state(db_session, player_id=pid, arc_id=arc.arc_id)

    assert first == second
    assert first["completed_beat_ids"] == []


def test_unknown_arc_id_raises_not_found(db_session, arc, player):
    pid, _ = player
    with pytest.raises(ArcNotFoundError):
        arc_state(db_session, player_id=pid, arc_id="no-such-arc")


# ── API 端點 ─────────────────────────────────────────────────────────

def test_get_state_requires_session_token(client, arc):
    assert client.get(f"/api/v1/story-arcs/{arc.arc_id}/state").status_code == 401


def test_get_state_returns_404_for_unknown_arc(client, player):
    _, sess = player
    resp = client.get(
        "/api/v1/story-arcs/no-such-arc/state",
        headers={"Authorization": f"Bearer {sess}"},
    )
    assert resp.status_code == 404


def test_get_state_reflects_progress(client, arc, player):
    pid, sess = player
    headers = {"Authorization": f"Bearer {sess}"}
    client.post(f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_a}/advance", headers=headers)

    body = client.get(f"/api/v1/story-arcs/{arc.arc_id}/state", headers=headers).json()

    assert body["completed_beat_ids"] == [arc.beat_a]
    assert set(body["eligible_beat_ids"]) == {arc.beat_b, arc.beat_c}


def test_advance_endpoint_requires_session_token(client, arc):
    resp = client.post(f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_a}/advance")
    assert resp.status_code == 401


def test_advance_endpoint_returns_403_when_prerequisites_not_met(client, arc, player):
    _, sess = player
    resp = client.post(
        f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_b}/advance",
        headers={"Authorization": f"Bearer {sess}"},
    )
    assert resp.status_code == 403


def test_advance_endpoint_returns_404_for_unknown_beat(client, arc, player):
    _, sess = player
    resp = client.post(
        f"/api/v1/story-arcs/{arc.arc_id}/beats/no-such-beat/advance",
        headers={"Authorization": f"Bearer {sess}"},
    )
    assert resp.status_code == 404


def test_advance_endpoint_full_arc_reports_story_completed(client, db_session, arc, player):
    pid, sess = player
    headers = {"Authorization": f"Bearer {sess}"}

    client.post(f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_a}/advance", headers=headers)
    client.post(f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_b}/advance", headers=headers)
    client.post(f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_c}/advance", headers=headers)
    body = client.post(
        f"/api/v1/story-arcs/{arc.arc_id}/beats/{arc.beat_finale}/advance", headers=headers
    ).json()

    assert body["story_completed"] is True
    assert _resonance_value(db_session, pid, arc.spirit_a_id) == 30
    assert _resonance_value(db_session, pid, arc.spirit_b_id) == 30
