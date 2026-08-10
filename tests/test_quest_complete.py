"""
Ticket #34．POST /quests/{questId}/complete 任務完成與共鳴入帳。

驗收標準對照見 GitHub issue #34。透過 `/summon` 建立 `quest_progress` 列，
再用 `/quests/{questId}/complete` 完成它——跟其他任務相關測試同一套模式。
"""
import uuid

import pytest

from app.core.config import settings
from app.modules.body import models
from app.modules.body.encounter_tokens import issue_encounter_token
from app.modules.body.quests import quest_id_for_spirit
from app.modules.body.sense_tokens import issue_sense_token

_LAT, _LON = 25.0955, 121.5186


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}", display_name="測試地標",
        latitude=_LAT, longitude=_LON, summon_radius_meters=50, is_active=True,
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
def player(client, db_session):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    player_id = uuid.UUID(body["player_id"])
    yield player_id, body["session_token"]
    db_session.query(models.QuestProgress).filter_by(player_id=player_id).delete()
    db_session.commit()


def _summon(client, token, spirit):
    return client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit.spirit_id, "latitude": spirit.latitude, "longitude": spirit.longitude},
        headers={"Authorization": f"Bearer {token}"},
    )


def _complete(client, quest_id, session_token=None, encounter_token=None):
    headers = {}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter_token:
        headers["X-Encounter-Token"] = encounter_token
    return client.post(
        f"/api/v1/quests/{quest_id}/complete", json={"completion_evidence": {}}, headers=headers
    )


# ── 憑證（issue #34 AC1／AC2）────────────────────────────────────────────

def test_missing_both_tokens_returns_401(client, spirit, player):
    _, sess = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    assert _complete(client, quest_id).status_code == 401


def test_session_only_returns_401(client, spirit, player):
    _, sess = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    assert _complete(client, quest_id, session_token=sess).status_code == 401


def test_encounter_only_returns_401(client, spirit, player):
    pid, _ = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)
    assert _complete(client, quest_id, encounter_token=enc).status_code == 401


def test_encounter_for_wrong_spirit_returns_403_without_side_effects(
    client, db_session, spirit, player
):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    wrong_enc = issue_encounter_token(pid, "some-other-spirit")

    response = _complete(client, quest_id, session_token=sess, encounter_token=wrong_enc)

    assert response.status_code == 403
    progress = db_session.query(models.QuestProgress).filter_by(player_id=pid, quest_id=quest_id).first()
    assert progress.status == "in_progress"
    assert db_session.query(models.Resonance).filter_by(player_id=pid, spirit_id=spirit.spirit_id).first() is None


def test_sense_token_cannot_complete_quest(client, db_session, spirit, player):
    """SDD 第6節：持 Sense Token 者不可呼叫 quests/complete。"""
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    sense = issue_sense_token(pid, spirit.spirit_id)

    # Sense token 是不同金鑰簽的，塞進 X-Encounter-Token 解碼就會失敗。
    response = _complete(client, quest_id, session_token=sess, encounter_token=sense)

    assert response.status_code in (401, 403)
    progress = db_session.query(models.QuestProgress).filter_by(player_id=pid, quest_id=quest_id).first()
    assert progress.status == "in_progress"
    assert db_session.query(models.Resonance).filter_by(player_id=pid, spirit_id=spirit.spirit_id).first() is None


# ── 不呼叫任何 LLM（issue #34 AC3）───────────────────────────────────────

def test_completion_never_calls_a_brain_module(client, db_session, spirit, player, monkeypatch):
    from app.modules.brain.gemini import VertexAIGeminiClient

    def _boom(self, prompt):
        raise AssertionError("quests/complete 不該呼叫任何腦袋模組")

    monkeypatch.setattr(VertexAIGeminiClient, "generate", _boom)

    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    assert _complete(client, quest_id, session_token=sess, encounter_token=enc).status_code == 200


# ── 完成後狀態與時間戳（issue #34 AC4）───────────────────────────────────

def test_completion_sets_status_and_completed_at(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    _complete(client, quest_id, session_token=sess, encounter_token=enc)

    progress = db_session.query(models.QuestProgress).filter_by(player_id=pid, quest_id=quest_id).first()
    assert progress.status == "completed"
    assert progress.completed_at is not None


# ── 共鳴值 +20，跨門檻回報 stage（issue #34 AC5）─────────────────────────

def test_completion_awards_twenty_resonance_from_zero_crosses_stage_one(
    client, db_session, spirit, player
):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["resonance_value"] == 20
    assert [s["stage"] for s in body["unlock_stories"]] == [1]


def test_completion_from_thirty_crosses_stage_two(client, db_session, spirit, player):
    from app.modules.body.resonance import apply_resonance

    pid, sess = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="preexisting", amount=30,
    )
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["resonance_value"] == 50
    assert [s["stage"] for s in body["unlock_stories"]] == [2]


# ── 重複提交不重複加值（issue #34 AC6）───────────────────────────────────

def test_duplicate_submission_does_not_double_award(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    first = _complete(client, quest_id, session_token=sess, encounter_token=enc)
    second = _complete(client, quest_id, session_token=sess, encounter_token=enc)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["resonance_value"] == 20

    count = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=pid, source_type="quest", source_id=quest_id)
        .count()
    )
    assert count == 1


# ── 回應形狀（issue #34 AC7，issue #43 接上敘事後更新）───────────────────
#
# #34 原本的 AC7 是「quest_wrapper_text／unlock_story 回 null，敘事生成屬
# #43」。#43 已經接上真正的生成（conftest 的 autouse fixture 預設用
# `FakeGeminiClient` 頂著），這條測試的前提因此不再成立，改成驗證新的
# 回應形狀——`quest_wrapper_text` 非空、`unlock_stories` 是 list。
# `unlock_story`（單數、字串）這個舊欄位已經不存在，見
# `schemas.QuestCompleteResponse` 的說明。

def test_quest_wrapper_text_is_present(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["quest_wrapper_text"]
    assert "unlock_story" not in body
