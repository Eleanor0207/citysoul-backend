"""
Ticket #43．任務完成串接解鎖敘事（SDD §7.5 後半）。

驗收標準對照見 GitHub issue #43。前半（狀態機寫入、共鳴入帳、去重）已經在
`tests/test_quest_complete.py` 驗過，這裡專注在 #43 新增的部分：B11／B2
接進來之後的行為，包含跨門檻、失敗降級、與呼叫順序。
"""
import uuid

import pytest

from app.core.redis_client import redis_client
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import issue_encounter_token
from app.modules.body.quests import quest_id_for_spirit
from app.modules.body.resonance import apply_resonance
from app.modules.body.router import get_gemini_client
from app.modules.brain.gemini import FakeGeminiClient, GeminiClient

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


def _complete(client, quest_id, session_token, encounter_token):
    return client.post(
        f"/api/v1/quests/{quest_id}/complete", json={"completion_evidence": {}},
        headers={"Authorization": f"Bearer {session_token}", "X-Encounter-Token": encounter_token},
    )


def _teardown_override():
    app.dependency_overrides.pop(get_gemini_client, None)


# ── 跨門檻時回傳 unlock_stories（issue #43 AC1）───────────────────────────

def test_crossing_a_threshold_returns_unlock_story(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake = FakeGeminiClient(response="第一次踏進廟埕，你在門檻前停下腳步。")
    app.dependency_overrides[get_gemini_client] = lambda: fake

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["unlock_stories"] == [
        {"stage": 1, "story_text": "第一次踏進廟埕，你在門檻前停下腳步。"}
    ]
    _teardown_override()


# ── 未跨門檻時 B11 未被呼叫（issue #43 AC2）───────────────────────────────

def test_no_threshold_crossed_means_no_unlock_story_and_no_gemini_call(
    client, db_session, spirit, player
):
    pid, sess = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="preexisting", amount=50,
    )
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["resonance_value"] == 70  # 50 + 20，仍在 stage 2，沒跨門檻
    assert body["unlock_stories"] == []
    # 沒跨門檻不該產生生成成本：只呼叫了 quest_wrapper 那一次，B11 一次都沒被呼叫。
    assert fake.call_count == 1
    _teardown_override()


# ── 一次跨多個門檻，每個 stage 各生成一段（issue #43 AC3）────────────────

def test_crossing_two_thresholds_in_a_single_completion(client, db_session, spirit, player):
    """
    `AMOUNT_QUEST`（20）跨不了兩個門檻——10 與 40 之間差 30，單次任務完成的
    固定加值不可能一次跨兩個。要驗證「`newly_unlocked_stages` 有多個元素時，
    每個都各呼叫一次 B11」這件事，直接對 `router_module.apply_resonance`
    monkeypatch 一個跨兩個門檻的假結果，專注測 `complete_quest` 自己的迴圈
    邏輯，不受「怎麼湊出跨兩門檻的共鳴值」這個無關細節干擾——
    `apply_resonance` 本身回傳跨多門檻結果的能力已經在 `test_resonance.py`
    驗過。
    """
    import app.modules.body.router as router_module
    from app.modules.body.resonance import ResonanceResult

    def _fake_apply_resonance(db, *, player_id, spirit_id, source_type, source_id, amount):
        return ResonanceResult(resonance_value=55, stage=2, newly_unlocked_stages=[1, 2])

    original = router_module.apply_resonance
    router_module.apply_resonance = _fake_apply_resonance

    fake = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake

    pid, sess = player
    try:
        _summon(client, sess, spirit)
        quest_id = quest_id_for_spirit(spirit.spirit_id)
        enc = issue_encounter_token(pid, spirit.spirit_id)

        body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

        assert [s["stage"] for s in body["unlock_stories"]] == [1, 2]
        # B11 兩次（stage 1、2）＋ B2 quest_wrapper 一次 = 3 次。
        assert fake.call_count == 3
    finally:
        router_module.apply_resonance = original
        _teardown_override()


# ── quest_wrapper_text 非空（issue #43 AC4）───────────────────────────────

def test_quest_wrapper_text_is_non_empty_string(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert isinstance(body["quest_wrapper_text"], str)
    assert body["quest_wrapper_text"]


# ── 腦袋生成失敗時回退，任務仍完成（issue #43 AC5）────────────────────────

class _RaisingGeminiClient(GeminiClient):
    """
    刻意違反 `GeminiClient.generate()` 永遠不拋例外的契約——測的是
    `complete_quest` 自己的防線（try/except），不是信任下游遵守契約。
    """

    def generate(self, prompt: str) -> str:
        raise ConnectionError("模擬腦袋呼叫失敗")


def test_brain_failure_does_not_block_quest_completion(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)
    app.dependency_overrides[get_gemini_client] = lambda: _RaisingGeminiClient()

    response = _complete(client, quest_id, session_token=sess, encounter_token=enc)
    body = response.json()

    assert response.status_code == 200
    assert body["resonance_value"] == 20  # 共鳴值已經入帳
    assert body["unlock_stories"][0]["stage"] == 1
    assert body["unlock_stories"][0]["story_text"]  # 回退文字，非空
    assert body["quest_wrapper_text"]  # 同樣回退，非空

    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=pid, quest_id=quest_id)
        .first()
    )
    assert progress.status == "completed"
    _teardown_override()


# ── 資料庫寫入一定發生在腦袋呼叫之前（issue #43 AC6）──────────────────────

class _DBStateSpy(GeminiClient):
    """generate() 被呼叫的當下，回頭查一次 DB，記錄看到的狀態。"""

    def __init__(self, db_session, player_id, quest_id, spirit_id):
        self._db = db_session
        self._player_id = player_id
        self._quest_id = quest_id
        self._spirit_id = spirit_id
        self.observed_status: str | None = None
        self.observed_resonance_value: int | None = None

    def generate(self, prompt: str) -> str:
        from app.modules.body.models import QuestProgress, Resonance

        progress = (
            self._db.query(QuestProgress)
            .filter_by(player_id=self._player_id, quest_id=self._quest_id)
            .first()
        )
        resonance = (
            self._db.query(Resonance)
            .filter_by(player_id=self._player_id, spirit_id=self._spirit_id)
            .first()
        )
        self.observed_status = progress.status if progress else None
        self.observed_resonance_value = resonance.resonance_value if resonance else None
        return "生成的故事文字"


def test_db_writes_happen_before_any_brain_call(client, db_session, spirit, player):
    pid, sess = player
    _summon(client, sess, spirit)
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    spy = _DBStateSpy(db_session, pid, quest_id, spirit.spirit_id)
    app.dependency_overrides[get_gemini_client] = lambda: spy

    _complete(client, quest_id, session_token=sess, encounter_token=enc)

    assert spy.observed_status == "completed"
    assert spy.observed_resonance_value == 20
    _teardown_override()
