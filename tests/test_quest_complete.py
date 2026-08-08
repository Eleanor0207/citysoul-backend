"""
Ticket #34．`POST /quests/{questId}/complete` 任務完成與共鳴入帳（SDD §7.5 前半）。

驗收標準對照見 GitHub issue #34。對真實 Postgres 跑，不 mock；風格比照
`tests/test_summon.py`（測試自己建 spirit，不依賴也不污染 seed data）。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import complete_quest, quest_id_for_spirit, spirit_id_for_quest
from app.modules.body.resonance import stage_for_value
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token

_LAT = 25.0372
_LON = 121.4998


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.QuestProgress).filter_by(
        quest_id=quest_id_for_spirit(unique_spirit_id)
    ).delete()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client, unique_device_id):
    return client.post("/api/v1/players", json={"device_id": unique_device_id}).json()


def _complete(client, quest_id, *, session_token=None, encounter_token=None, sense_token=None):
    headers = {}
    if session_token is not None:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter_token is not None:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter_token
    if sense_token is not None:
        headers[SENSE_TOKEN_HEADER] = sense_token

    return client.post(
        f"/api/v1/quests/{quest_id}/complete",
        json={"completion_evidence": {}},
        headers=headers,
    )


# ── quest_id ←→ spirit_id 對應 ────────────────────────────────────────────


def test_spirit_id_for_quest_round_trips():
    assert spirit_id_for_quest(quest_id_for_spirit("taipei_longshan")) == "taipei_longshan"


@pytest.mark.parametrize("bad", ["", "no-separator", "trailing:", ":daily", "x:weekly"])
def test_spirit_id_for_quest_rejects_malformed_ids(bad):
    assert spirit_id_for_quest(bad) is None


def test_malformed_quest_id_returns_404(client, player, spirit):
    resp = _complete(
        client,
        "not-a-valid-quest-id",
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )
    assert resp.status_code == 404


# ── 401：缺 token（AC1） ─────────────────────────────────────────────────


def test_missing_both_tokens_returns_401(client, spirit):
    resp = _complete(client, quest_id_for_spirit(spirit.spirit_id))
    assert resp.status_code == 401


def test_session_only_returns_401(client, player, spirit):
    resp = _complete(
        client, quest_id_for_spirit(spirit.spirit_id), session_token=player["session_token"]
    )
    assert resp.status_code == 401


def test_encounter_only_returns_401(client, player, spirit):
    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )
    assert resp.status_code == 401


# ── 403：encounter token 屬於別的靈魂（AC2） ──────────────────────────────


def test_encounter_token_for_a_different_spirit_returns_403(client, db_session, player, spirit):
    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], "some-other-spirit"),
    )

    assert resp.status_code == 403

    # 任務狀態未改變、共鳴值未增加
    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(
            player_id=uuid.UUID(player["player_id"]),
            quest_id=quest_id_for_spirit(spirit.spirit_id),
        )
        .first()
    )
    assert progress is None
    resonance = (
        db_session.query(models.Resonance)
        .filter_by(player_id=uuid.UUID(player["player_id"]), spirit_id=spirit.spirit_id)
        .first()
    )
    assert resonance is None


def test_token_holder_mismatch_returns_403(client, db_session, player, spirit, unique_device_id):
    """A 的 session 配上 B 的相遇憑證不該通過。"""
    other = client.post(
        "/api/v1/players", json={"device_id": f"other-{unique_device_id}"}
    ).json()

    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(other["player_id"], spirit.spirit_id),
    )

    assert resp.status_code == 403


# ── Sense Token 不可呼叫此端點（AC7） ─────────────────────────────────────


def test_sense_token_cannot_complete_a_quest(client, db_session, player, spirit):
    """
    SDD 第6節：持 Sense Token 者不可呼叫 `quests/complete`。感應憑證只證明
    在 150m 範圍內，挑戰任務要求真的到現場（50m）。
    """
    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        sense_token=issue_sense_token(player["player_id"], spirit.spirit_id),
    )

    assert resp.status_code in (401, 403)

    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(
            player_id=uuid.UUID(player["player_id"]),
            quest_id=quest_id_for_spirit(spirit.spirit_id),
        )
        .first()
    )
    assert progress is None


def test_sense_token_sent_in_the_encounter_header_is_also_rejected(client, player, spirit):
    """
    把 sense token 塞進 `X-Encounter-Token` 也不能過——兩種 token 用不同的
    簽章金鑰與 purpose claim，這正是 SDD 第6節「不共用驗證邏輯」的效果。
    """
    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_sense_token(player["player_id"], spirit.spirit_id),
    )

    assert resp.status_code == 401


# ── 完成後狀態與時間戳（AC4） ─────────────────────────────────────────────


def test_completion_sets_status_and_timestamp(client, db_session, player, spirit):
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    resp = _complete(
        client,
        quest_id,
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )
    assert resp.status_code == 200

    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=uuid.UUID(player["player_id"]), quest_id=quest_id)
        .one()
    )
    assert progress.status == "completed"
    assert progress.completed_at is not None


# ── 共鳴值 +20 與門檻（AC5） ──────────────────────────────────────────────


def test_completion_awards_twenty_and_crosses_first_threshold(client, player, spirit):
    """共鳴值 0 → 20，跨過門檻 10（stage 0 → 1）。"""
    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )

    assert resp.status_code == 200
    assert resp.json()["resonance_value"] == 20
    assert stage_for_value(20) == 1


def test_completion_from_thirty_crosses_second_threshold(client, db_session, player, spirit):
    """共鳴值 30 → 50，跨過門檻 40（stage 1 → 2）。"""
    db_session.add(
        models.Resonance(
            player_id=uuid.UUID(player["player_id"]),
            spirit_id=spirit.spirit_id,
            resonance_value=30,
        )
    )
    db_session.commit()

    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )

    assert resp.json()["resonance_value"] == 50
    assert stage_for_value(50) == 2


def test_completion_without_crossing_a_threshold(client, db_session, player, spirit):
    """共鳴值 50 → 70，兩者都在 stage 2，沒有新解鎖。"""
    db_session.add(
        models.Resonance(
            player_id=uuid.UUID(player["player_id"]),
            spirit_id=spirit.spirit_id,
            resonance_value=50,
        )
    )
    db_session.commit()

    resp = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=player["session_token"],
        encounter_token=issue_encounter_token(player["player_id"], spirit.spirit_id),
    )

    assert resp.json()["resonance_value"] == 70
    assert stage_for_value(50) == stage_for_value(70) == 2


# ── 重複提交不重複加值（AC6） ─────────────────────────────────────────────


def test_duplicate_submission_does_not_double_award(client, db_session, player, spirit):
    """
    重複提交是正常使用者行為（網路重試、連點兩下），不是錯誤：第二次仍回
    200、共鳴值維持 20、帳本只有一列。去重靠 S5 的 UNIQUE 約束。
    """
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    token = issue_encounter_token(player["player_id"], spirit.spirit_id)

    first = _complete(
        client, quest_id, session_token=player["session_token"], encounter_token=token
    )
    second = _complete(
        client, quest_id, session_token=player["session_token"], encounter_token=token
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["resonance_value"] == 20
    assert second.json()["resonance_value"] == 20

    ledger_count = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=uuid.UUID(player["player_id"]), source_id=quest_id)
        .count()
    )
    assert ledger_count == 1


# ── 敘事欄位（#34 當時一律 null，#43 之後由 test_quest_narrative.py 接手）
#
# #34 原本有兩條測試釘住「`unlock_story` 與 `quest_wrapper_text` 永遠是
# null」，那在當時是對的——那張票刻意不做敘事。#43 把敘事接上之後，那兩條
# 就變成在釘一個已經被取代的中間狀態，所以移到
# `tests/test_quest_narrative.py` 改成驗真正的行為（跨門檻才有 unlock_story、
# wrapper 永遠非空）。這裡留下說明而不是靜靜刪掉，是為了讓「#34 的 AC8 去
# 哪了」這個問題有答案。


# ── 不呼叫任何 LLM（AC3） ────────────────────────────────────────────────


def test_the_completion_judgement_itself_never_calls_the_brain(
    db_session, player, spirit, monkeypatch
):
    """
    CONTEXT.md：可驗證微任務由後端確定性規則判定，**不由 LLM 判定**。

    ⚠️ 這條原本是打整支端點的（#34 當時端點確實完全不碰腦袋）。#43 之後
    端點會在**判定與寫入都結束之後**呼叫腦袋做敘事包裝，所以打整支端點的
    寫法已經不成立——但 CONTEXT.md 那條規則約束的從來就是「判定」，不是
    「事後的包裝」。因此這裡改成直接打 `complete_quest`：判定與寫入的那一段
    仍然必須一次都不碰腦袋。

    包裝那一段的行為（失敗也不能讓進度消失）由 `test_quest_narrative.py`
    負責，不是這裡。
    """
    import app.modules.brain.gemini as gemini

    def explode(*args, **kwargs):
        raise AssertionError("完成判定不該呼叫腦袋模組")

    monkeypatch.setattr(gemini.VertexAIGeminiClient, "generate", explode)
    monkeypatch.setattr(gemini.FakeGeminiClient, "generate", explode)

    result = complete_quest(
        db_session,
        player_id=uuid.UUID(player["player_id"]),
        spirit_id=spirit.spirit_id,
        quest_id=quest_id_for_spirit(spirit.spirit_id),
    )

    assert result.resonance_value == 20
