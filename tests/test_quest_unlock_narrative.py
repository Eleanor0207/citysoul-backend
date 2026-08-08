"""
#43．任務完成串接解鎖敘事（SDD §7.5 後半）。

#34 的前半（判定 → 寫表 → 入帳 → 門檻）在 `test_quest_complete.py`。
這裡驗的是**接上腦袋之後才存在的行為**：跨門檻生成、多門檻各一段、
生成失敗的降級，以及 v2.1 §6.4 的呼叫順序硬規則。
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import STATUS_COMPLETED, quest_id_for_spirit
from app.modules.body.resonance import apply_resonance
from app.modules.body.router import get_gemini_client
from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient

_LAT, _LON = 25.0955, 121.5186
_GENERATED = "（城市靈魂偏過頭）我記得你了。"


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
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
def summoned(client, spirit):
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    pid, sess = uuid.UUID(body["player_id"]), body["session_token"]
    client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit.spirit_id, "latitude": _LAT, "longitude": _LON},
        headers={"Authorization": f"Bearer {sess}"},
    )
    return pid, sess


class _RecordingClient(FakeGeminiClient):
    """記下每次收到的 prompt，讓測試看得出 B11 被呼叫了幾次、帶了哪些 stage。"""

    @property
    def unlock_prompts(self) -> list[str]:
        return [p for p in self.prompts if "共鳴值剛跨過" in p]


@pytest.fixture
def gemini():
    spy = _RecordingClient(response=_GENERATED)
    app.dependency_overrides[get_gemini_client] = lambda: spy
    yield spy
    app.dependency_overrides.pop(get_gemini_client, None)


def _complete(client, quest_id, session_token, encounter_token):
    return client.post(
        f"/api/v1/quests/{quest_id}/complete",
        json={"completion_evidence": {}},
        headers={
            "Authorization": f"Bearer {session_token}",
            ENCOUNTER_TOKEN_HEADER: encounter_token,
        },
    )


# ── 跨門檻 ─────────────────────────────────────────────────────────────

def test_crossing_a_threshold_returns_an_unlock_story(client, spirit, summoned, gemini):
    """AC：共鳴值 0 → 20（跨過 10），`unlock_story` 為 `{stage, story_text}`。"""
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        sess,
        issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["unlock_story"] == {"stage": 1, "story_text": _GENERATED}


def test_not_crossing_a_threshold_returns_null_and_never_calls_b11(
    client, spirit, summoned, gemini, db_session
):
    """
    🔒 AC：未跨門檻時 `unlock_story` 為 null，且 **B11 未被呼叫**。

    沒跨門檻就不該產生生成成本——而這是最常見的情況，每次白呼叫一次很可觀。
    """
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-50",
        amount=50,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        sess,
        issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 70
    assert body["unlock_story"] is None
    assert body["unlock_stories"] == []
    assert gemini.unlock_prompts == [], "沒跨門檻卻呼叫了 B11"


def test_quest_wrapper_text_is_generated(client, spirit, summoned, gemini):
    """AC：`quest_wrapper_text` 為非空字串（不再是 #34 的 null）。"""
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        sess,
        issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["quest_wrapper_text"] == _GENERATED


def test_wrapper_is_generated_even_without_a_new_unlock(client, spirit, summoned, gemini, db_session):
    """包裝台詞跟門檻無關——每次完成都該有一句回應。"""
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-50",
        amount=50,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        sess,
        issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["quest_wrapper_text"]
    assert body["unlock_story"] is None


# ── 一次跨多個門檻 ─────────────────────────────────────────────────────

def test_endpoint_forwards_every_newly_unlocked_stage(client, spirit, summoned, gemini, db_session):
    """
    端點把 `newly_unlocked_stages` 原封不動轉交給 B11，一個 stage 一段故事。

    ⚠️ AC 的情境（5 → 55，一次跨兩個門檻）**在端點層製造不出來**：MVP 的任務
    固定 +20，而 10 與 40 相差 30。硬要湊出來就得讓任務給 +50，而任務不會給
    +50——那會變成測一個產品上不存在的路徑。

    所以這裡驗轉交（單門檻），多段生成本身由下一條在服務層驗。兩條合起來涵蓋
    AC 要的保證。
    """
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-5",
        amount=5,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        sess,
        issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 25  # 5 + 20，只跨過 10
    assert body["newly_unlocked_stages"] == [1]
    assert [s["stage"] for s in body["unlock_stories"]] == [1]
    assert len(gemini.unlock_prompts) == 1


def test_b11_generates_one_story_per_newly_unlocked_stage(gemini):
    """
    AC 的多門檻情境在**服務層**驗證。

    ⚠️ 端點層驗不到：MVP 的任務固定 +20，從任何起點都跨不過兩個門檻（10→40
    相差 30）。硬要在端點層製造那個情境，就得先用非任務來源把共鳴值墊到剛好
    5，再讓任務給 +50——而任務不會給 +50。那會變成測一個產品上不存在的路徑。

    所以這裡直接對 B11 驗證：給它 `[1, 2]`，它要生成兩段。端點只是把
    `newly_unlocked_stages` 原封不動轉交，那條轉交路徑由上面的單門檻測試涵蓋。
    """
    from app.modules.brain.unlock_story import generate_unlock_stories

    client = _RecordingClient(response=_GENERATED)

    stories = generate_unlock_stories(client, spirit_id="taipei_longshan", stages=[1, 2])

    assert [s.stage for s in stories] == [1, 2]
    assert len(client.unlock_prompts) == 2


# ── 生成失敗不影響進度 ─────────────────────────────────────────────────

def test_brain_failure_still_completes_the_quest(client, spirit, summoned, db_session):
    """
    AC：B11 失敗時 `200`、狀態為 completed、共鳴值已加。

    任務已經完成了，敘事只是包裝——包裝失敗不能讓玩家的進度消失。
    """
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient(response=FALLBACK_REPLY)
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    try:
        response = _complete(client, quest_id, sess, issue_encounter_token(pid, spirit.spirit_id))

        assert response.status_code == 200
        body = response.json()
        assert body["resonance_value"] == 20
        # 回退台詞，不是 null——玩家仍看到一段話。
        assert body["unlock_story"]["story_text"]
        assert body["quest_wrapper_text"]

        db_session.expire_all()
        progress = (
            db_session.query(models.QuestProgress)
            .filter_by(player_id=pid, quest_id=quest_id)
            .one()
        )
        assert progress.status == STATUS_COMPLETED
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)


def test_brain_raising_does_not_lose_progress(client, spirit, summoned, db_session, monkeypatch):
    """
    🔒 腦袋**拋例外**（違反 B1 的契約）時，玩家的進度仍然保住。

    ⚠️ 這條防的是一個很具體的傷害：上面的寫入已經 commit 了，一個逸出的例外
    會讓玩家收到 500——而他的任務其實已經完成、共鳴值也已入帳。他會重試，
    然後看到「重複提交」的結果，以為進度沒存到。

    契約被違反時，付出代價的是玩家的信任，不是我們的 log。
    """
    from app.modules.brain import gemini as gemini_module

    def _explode(*args, **kwargs):
        raise RuntimeError("模型整個掛了")

    monkeypatch.setattr(gemini_module.FakeGeminiClient, "generate", _explode)

    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(client, quest_id, sess, issue_encounter_token(pid, spirit.spirit_id))

    assert response.status_code == 200
    assert response.json()["resonance_value"] == 20

    db_session.expire_all()
    progress = (
        db_session.query(models.QuestProgress).filter_by(player_id=pid, quest_id=quest_id).one()
    )
    assert progress.status == STATUS_COMPLETED


# ── v2.1 §6.4：先寫表、再呼叫腦袋 ─────────────────────────────────────

def test_database_is_written_before_the_brain_is_called(client, spirit, summoned, db_session):
    """
    🔒 AC：B11 被呼叫時，`quest_progress` 已是 completed、共鳴值已是入帳後的值。

    v2.1 §6.4 的硬規則。顛倒的話腦袋拿到的 stage 會跟資料庫不一致——玩家會看到
    一段講述他還沒達到的關係階段的故事。

    做法是讓 fake 在被呼叫的當下**開一個獨立 session 去查資料庫**。獨立 session
    很重要：共用的話看到的可能是同一個交易裡尚未 commit 的狀態，那證明不了
    「已經寫進去了」。

    AC 指定要做 mutation 驗證的那一條。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    observed = {}

    class _ObservingClient(FakeGeminiClient):
        def generate(self, prompt: str) -> str:
            if "observed_once" not in observed:
                probe = SessionLocal()
                try:
                    progress = (
                        probe.query(models.QuestProgress)
                        .filter_by(player_id=pid, quest_id=quest_id)
                        .first()
                    )
                    resonance = (
                        probe.query(models.Resonance)
                        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
                        .first()
                    )
                    observed["status"] = progress.status if progress else None
                    observed["resonance"] = resonance.resonance_value if resonance else 0
                    observed["observed_once"] = True
                finally:
                    probe.close()
            return super().generate(prompt)

    app.dependency_overrides[get_gemini_client] = lambda: _ObservingClient(response=_GENERATED)
    try:
        _complete(client, quest_id, sess, issue_encounter_token(pid, spirit.spirit_id))
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)

    assert observed.get("status") == STATUS_COMPLETED, (
        "腦袋被呼叫時任務還不是 completed——寫入與生成的順序顛倒了（v2.1 §6.4）"
    )
    assert observed.get("resonance") == 20, (
        "腦袋被呼叫時共鳴值還沒入帳——它拿到的 stage 會跟資料庫不一致"
    )
