"""
Ticket #43．任務完成串接解鎖敘事（SDD §7.5 後半）。

驗收標準對照見 GitHub issue #43。腦袋以 fake 注入（`get_gemini_client`
dependency override），完全不需要 GCP 憑證。

跟 `tests/test_quest_complete.py` 的分工：那邊驗**身體**（判定、寫表、
入帳、去重、token），這邊驗**腦袋接上去之後**的行為（跨門檻才有故事、
包裝永遠有、生成失敗不吃掉進度、呼叫順序）。
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import quest_id_for_spirit
from app.modules.brain.gemini import FakeGeminiClient, GeminiClient, get_gemini_client

_LAT = 25.0372
_LON = 121.4998


class _ExplodingGeminiClient(GeminiClient):
    """一叫就爆。用來確認包裝失敗不會連帶讓任務進度消失。"""

    def generate(self, prompt: str) -> str:
        raise ConnectionError("brain is down")


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


@pytest.fixture
def use_fake_brain():
    """
    把端點用的 GeminiClient 換成 fake，測試結束後還原。

    回傳一個 setter，讓每個測試自己決定要注入哪一種 fake（正常回應／
    一叫就爆／記錄 DB 狀態的 spy）。
    """
    injected: list[GeminiClient] = []

    def _use(fake: GeminiClient) -> GeminiClient:
        app.dependency_overrides[get_gemini_client] = lambda: fake
        injected.append(fake)
        return fake

    yield _use
    app.dependency_overrides.pop(get_gemini_client, None)


def _complete(client, player, spirit):
    return client.post(
        f"/api/v1/quests/{quest_id_for_spirit(spirit.spirit_id)}/complete",
        json={"completion_evidence": {}},
        headers={
            "Authorization": f"Bearer {player['session_token']}",
            ENCOUNTER_TOKEN_HEADER: issue_encounter_token(
                player["player_id"], spirit.spirit_id
            ),
        },
    )


def _seed_resonance(db_session, player, spirit, value):
    db_session.add(
        models.Resonance(
            player_id=uuid.UUID(player["player_id"]),
            spirit_id=spirit.spirit_id,
            resonance_value=value,
        )
    )
    db_session.commit()


# ── 跨門檻時回傳 unlock_story（AC1） ──────────────────────────────────────


def test_crossing_a_threshold_returns_an_unlock_story(
    client, player, spirit, use_fake_brain
):
    """共鳴值 0 → 20，跨過門檻 10。"""
    use_fake_brain(FakeGeminiClient(response="你又來了，這次我想多說一點。"))

    body = _complete(client, player, spirit).json()

    assert body["unlock_story"] is not None
    assert body["unlock_story"]["stage"] == 1
    assert body["unlock_story"]["story_text"].strip() != ""


# ── 未跨門檻時為 null，且不呼叫 B11（AC2） ────────────────────────────────


def test_no_threshold_crossed_returns_null_and_generates_no_story(
    client, db_session, player, spirit, use_fake_brain
):
    """
    共鳴值 50 → 70，兩者都在 stage 2。除了包裝那一次之外，不該有任何
    解鎖敘事的生成呼叫——沒跨門檻就不該產生生成成本。
    """
    _seed_resonance(db_session, player, spirit, 50)
    fake = use_fake_brain(FakeGeminiClient(response="一段文字"))

    body = _complete(client, player, spirit).json()

    assert body["resonance_value"] == 70
    assert body["unlock_story"] is None
    # 只有 quest wrapper 那一次呼叫，沒有解鎖敘事的呼叫。
    assert fake.call_count == 1


# ── 一次跨多個門檻，每個 stage 各生成一段（AC3） ──────────────────────────


def test_crossing_two_thresholds_generates_one_story_per_stage(
    client, db_session, player, spirit, use_fake_brain, monkeypatch
):
    """
    共鳴值 5，一次入帳 +50（5 → 55，跨過 10 與 40）。

    ⚠️ #16 讓 `newly_unlocked_stages` 回傳 list 就是為了這件事：端點只取
    最後一個 stage 的話，stage 1 那段故事會**靜默消失**，沒有任何錯誤訊息
    會提醒。

    這條**刻意走完整條端點**而不是直接呼叫 `generate_unlock_stories`：
    要守的是「端點有沒有把整個 list 交出去」，直接呼叫服務層的話，端點改成
    `stages[-1:]` 這個測試照樣會過（實際試過，確實會過），等於沒有守到。

    MVP 的 +20 跨不過兩個門檻（10 與 40 之間差 30），所以把入帳金額暫時
    調成 50 來構造這個情境——驗的是機制，不是 MVP 的數字。
    """
    import app.modules.body.quests as quests_module

    monkeypatch.setattr(quests_module, "AMOUNT_QUEST", 50)
    _seed_resonance(db_session, player, spirit, 5)
    fake = use_fake_brain(FakeGeminiClient(response="一段故事"))

    body = _complete(client, player, spirit).json()

    assert body["resonance_value"] == 55

    # prompt 裡帶著階段編號（見 unlock_story._PROMPT_TEMPLATE），用它數出
    # 每個 stage 各被生成過幾次。
    stage_1_calls = [p for p in fake.prompts if "第 1 階段" in p]
    stage_2_calls = [p for p in fake.prompts if "第 2 階段" in p]

    assert len(stage_1_calls) == 1, "stage 1 的故事沒有被生成——中間那段靜默消失了"
    assert len(stage_2_calls) == 1

    # 回應只裝得下一個，帶回最高的那階（見 schemas.QuestCompleteResponse）。
    assert body["unlock_story"]["stage"] == 2


# ── quest_wrapper_text 永遠非空（AC4） ────────────────────────────────────


def test_quest_wrapper_text_is_always_present(client, player, spirit, use_fake_brain):
    use_fake_brain(FakeGeminiClient(response="你做到了。"))

    body = _complete(client, player, spirit).json()

    assert body["quest_wrapper_text"] == "你做到了。"


def test_quest_wrapper_text_is_non_empty_even_when_generation_fails(
    client, player, spirit, use_fake_brain
):
    use_fake_brain(_ExplodingGeminiClient())

    body = _complete(client, player, spirit).json()

    assert body["quest_wrapper_text"]
    assert body["quest_wrapper_text"].strip() != ""


# ── 腦袋失敗不吃掉進度（AC5） ─────────────────────────────────────────────


def test_brain_failure_still_completes_the_quest_and_awards_resonance(
    client, db_session, player, spirit, use_fake_brain
):
    """
    任務已經完成了、共鳴值也入帳了，敘事只是包裝——包裝失敗不能讓玩家的
    進度消失。
    """
    use_fake_brain(_ExplodingGeminiClient())

    resp = _complete(client, player, spirit)

    assert resp.status_code == 200
    body = resp.json()
    assert body["resonance_value"] == 20

    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(
            player_id=uuid.UUID(player["player_id"]),
            quest_id=quest_id_for_spirit(spirit.spirit_id),
        )
        .one()
    )
    assert progress.status == "completed"

    # 跨過門檻但生成失敗：回退台詞，不是 null、也不是 500。
    assert body["unlock_story"] is not None
    assert body["unlock_story"]["story_text"].strip() != ""


# ── 資料庫寫入一定發生在腦袋呼叫之前（AC6，v2.1 §6.4） ────────────────────


class _DbSnoopingGeminiClient(GeminiClient):
    """
    被呼叫的當下，用**另一個 session** 去查資料庫目前 committed 的狀態。

    用新 session 而不是端點那個，是因為端點的 session 看得到自己還沒
    commit 的東西——那樣就算順序錯了也照樣「看得到」，測試會假過。
    """

    def __init__(self, player_id: str, quest_id: str, spirit_id: str):
        self._player_id = player_id
        self._quest_id = quest_id
        self._spirit_id = spirit_id
        self.observations: list[dict] = []

    def generate(self, prompt: str) -> str:
        session = SessionLocal()
        try:
            progress = (
                session.query(models.QuestProgress)
                .filter_by(player_id=uuid.UUID(self._player_id), quest_id=self._quest_id)
                .first()
            )
            resonance = (
                session.query(models.Resonance)
                .filter_by(
                    player_id=uuid.UUID(self._player_id), spirit_id=self._spirit_id
                )
                .first()
            )
            self.observations.append(
                {
                    "status": progress.status if progress else None,
                    "resonance_value": resonance.resonance_value if resonance else None,
                }
            )
        finally:
            session.close()
        return "一段文字"


def test_database_is_written_before_the_brain_is_called(
    client, player, spirit, use_fake_brain
):
    """
    v2.1 §6.4 硬規則：身體先寫完自己的表、再呼叫腦袋。顛倒的話腦袋拿到的
    stage 會跟資料庫不一致——玩家會拿到一段跟他實際進度對不上的故事。
    """
    spy = _DbSnoopingGeminiClient(
        player["player_id"], quest_id_for_spirit(spirit.spirit_id), spirit.spirit_id
    )
    use_fake_brain(spy)

    assert _complete(client, player, spirit).status_code == 200

    assert spy.observations, "腦袋一次都沒被呼叫，這條測試沒有驗到東西"
    for observed in spy.observations:
        assert observed["status"] == "completed"
        assert observed["resonance_value"] == 20


# ── 去重不因為加了敘事而破功（AC7 的延伸） ────────────────────────────────


def test_duplicate_submission_still_does_not_double_award_with_narrative(
    client, db_session, player, spirit, use_fake_brain
):
    """
    #34 的去重保證不得因為接上敘事而破功。第二次提交沒有跨門檻（共鳴值
    沒變），所以也不該再有解鎖故事。
    """
    use_fake_brain(FakeGeminiClient(response="一段文字"))

    first = _complete(client, player, spirit).json()
    second = _complete(client, player, spirit).json()

    assert first["resonance_value"] == 20
    assert second["resonance_value"] == 20
    assert first["unlock_story"] is not None  # 第一次跨過門檻 10
    assert second["unlock_story"] is None  # 第二次沒有加值，沒有新解鎖

    ledger_count = (
        db_session.query(models.ResonanceEvent)
        .filter_by(
            player_id=uuid.UUID(player["player_id"]),
            source_id=quest_id_for_spirit(spirit.spirit_id),
        )
        .count()
    )
    assert ledger_count == 1
