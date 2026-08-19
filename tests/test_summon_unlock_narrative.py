"""
`/summon` 串接解鎖敘事（SDD §7.5 後半，原 #43）。

🔴 **2026-08-19：這個檔案是 `test_quest_unlock_narrative.py` 搬過來的。**
共鳴入帳從 `/quests/{id}/complete` 移到了 `/summon`（來源改為每日召喚 +10 與
劇本節點 +30），跨門檻與解鎖敘事跟著搬。舊檔已移除——它整檔建立在「任務完成
會跨門檻」這個前提上，而那條路徑不再入帳。

入帳邏輯本身在 `test_resonance.py`，召喚端點的基本行為在 `test_summon.py`。
這裡驗的是**接上腦袋之後才存在的行為**：跨門檻生成、生成失敗的降級，
以及 v2.1 §6.4 的呼叫順序硬規則。

⚠️ 這裡的 `summoned` 夾具**刻意只建玩家、不召喚**——召喚就是待測行為本身，
先召喚一次的話測試裡那次會變成「同一天第二次」，不會入帳。
"""
import uuid

import pytest

from app.core.database import SessionLocal
from app.main import app
from app.modules.body import models
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
        quest_id=f"{unique_spirit_id}:daily"
    ).delete()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    """
    ⚠️ **只建玩家，不召喚。** 召喚是待測行為，先跑一次會讓測試裡那次變成
    「同一天第二次」而不入帳——那會讓每一條斷言都以很難看懂的方式失敗。
    """
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


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


def _summon(client, spirit_id, session_token):
    return client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit_id, "latitude": _LAT, "longitude": _LON},
        headers={"Authorization": f"Bearer {session_token}"},
    )


def _seed_resonance(db_session, player_id, spirit_id, amount):
    """用非召喚來源把共鳴值墊到指定值，好製造「這次召喚不跨門檻」的情境。"""
    apply_resonance(
        db_session,
        player_id=player_id,
        spirit_id=spirit_id,
        source_type="test_seed",
        source_id=f"seed-{amount}",
        amount=amount,
    )


# ── 跨門檻 ─────────────────────────────────────────────────────────────

def test_first_summon_crosses_the_first_threshold(client, spirit, player, gemini):
    """
    第一次召喚：0 → 10，跨過第一道門檻，回一段解鎖敘事。

    🔑 這是新規則下最常見的跨門檻情境，也是玩家跟每個地標的第一次相遇。
    """
    pid, sess = player

    body = _summon(client, spirit.spirit_id, sess).json()

    assert body["newly_unlocked_stages"] == [1]
    assert [s["stage"] for s in body["unlock_stories"]] == [1]
    assert body["unlock_stories"][0]["story_text"] == _GENERATED
    assert len(gemini.unlock_prompts) == 1


def test_not_crossing_a_threshold_never_calls_b11(client, spirit, player, gemini, db_session):
    """
    🔒 未跨門檻時不生成，且 **B11 一次都沒被呼叫**。

    沒跨門檻就不該產生生成成本——而在新規則下這是**絕大多數**的情況：
    每天 +10，十天裡只有三天會跨門檻。舊規則下任務 +20 反而常跨，
    所以這條測試的份量比以前重。
    """
    pid, sess = player
    _seed_resonance(db_session, pid, spirit.spirit_id, 50)

    body = _summon(client, spirit.spirit_id, sess).json()

    assert body["resonance_value"] == 60
    assert body["newly_unlocked_stages"] == []
    assert body["unlock_stories"] == []
    assert gemini.unlock_prompts == [], "沒跨門檻卻呼叫了 B11"


def test_repeat_summon_same_day_never_calls_b11(client, spirit, player, gemini):
    """
    同一天第二次召喚沒有入帳，自然也不該再生成一次解鎖敘事。

    ⚠️ 這條防的是「重播解鎖動畫」：玩家在現場逛一圈又點一次召喚，不該再看到
    一次「你們的關係更近了」。
    """
    pid, sess = player

    _summon(client, spirit.spirit_id, sess)
    calls_after_first = len(gemini.unlock_prompts)
    body = _summon(client, spirit.spirit_id, sess).json()

    assert body["resonance_awarded"] is False
    assert body["newly_unlocked_stages"] == []
    assert body["unlock_stories"] == []
    assert len(gemini.unlock_prompts) == calls_after_first, "第二次召喚不該再呼叫 B11"


# ── 一次跨多個門檻 ─────────────────────────────────────────────────────

def test_b11_generates_one_story_per_newly_unlocked_stage(gemini):
    """
    多門檻情境在**服務層**驗證。

    ⚠️ 端點層造不出來，而且新規則下比舊規則更造不出來：每日召喚固定 +10，
    劇本節點固定 +30，而 10 與 40 相差 30——從任何起點都跨不過兩個門檻
    （要跨過 10 與 40 需要一次超過 30 點）。硬要在端點層製造那個情境，
    就得讓某個來源給 +50，而沒有來源會給 +50。那會變成測一個產品上不存在的路徑。

    所以這裡直接對 B11 驗證：給它 `[1, 2]`，它要生成兩段。端點只是把
    `newly_unlocked_stages` 原封不動轉交，那條轉交路徑由上面的單門檻測試涵蓋。

    🔑 `ResonanceResult.newly_unlocked_stages` 做成 list 而不是單一 stage，
    就是為了讓「未來出現大額來源」時不需要改這條路徑。
    """
    from app.modules.brain.unlock_story import generate_unlock_stories

    recording = _RecordingClient(response=_GENERATED)

    stories = generate_unlock_stories(recording, spirit_id="taipei_longshan", stages=[1, 2])

    assert [s.stage for s in stories] == [1, 2]
    assert len(recording.unlock_prompts) == 2


# ── 生成失敗不影響入帳 ─────────────────────────────────────────────────

def test_brain_fallback_still_awards_resonance(client, spirit, player, db_session):
    """B11 回退時仍然 200，共鳴值照樣入帳——敘事只是包裝。"""
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient(response=FALLBACK_REPLY)
    pid, sess = player

    try:
        response = _summon(client, spirit.spirit_id, sess)

        assert response.status_code == 200
        body = response.json()
        assert body["resonance_value"] == 10
        assert body["resonance_awarded"] is True
        # 回退台詞，不是空的——玩家仍看到一段話。
        assert body["unlock_stories"][0]["story_text"]
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)


def test_brain_raising_does_not_lose_the_award(client, spirit, player, db_session, monkeypatch):
    """
    🔒 腦袋**拋例外**（違反 B1 的契約）時，玩家的共鳴值仍然保住。

    ⚠️ 這條防的是一個很具體的傷害：入帳已經 commit 了，一個逸出的例外會讓
    玩家收到 500——而他的 +10 其實已經加了。他重試，然後看到
    `resonance_awarded: false`，會以為分數消失了。

    ⚠️ **`newly_unlocked_stages` 仍然是 `[1]`，但 `unlock_stories` 是空的。**
    那不是矛盾：階段確實解鎖了，只是沒有故事可以配。客戶端判斷「有沒有解鎖」
    要看前者。
    """
    from app.modules.brain import gemini as gemini_module

    def _explode(*args, **kwargs):
        raise RuntimeError("模型整個掛了")

    monkeypatch.setattr(gemini_module.FakeGeminiClient, "generate", _explode)

    pid, sess = player

    response = _summon(client, spirit.spirit_id, sess)

    assert response.status_code == 200
    body = response.json()
    assert body["resonance_value"] == 10
    assert body["newly_unlocked_stages"] == [1]
    assert body["unlock_stories"] == []

    db_session.expire_all()
    row = (
        db_session.query(models.Resonance)
        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
        .one()
    )
    assert row.resonance_value == 10


# ── v2.1 §6.4：先寫表、再呼叫腦袋 ─────────────────────────────────────

def test_database_is_written_before_the_brain_is_called(client, spirit, player, db_session):
    """
    🔒 B11 被呼叫時，共鳴值已經是入帳**之後**的值。

    v2.1 §6.4 的硬規則。顛倒的話腦袋拿到的 stage 會跟資料庫不一致——玩家會看到
    一段講述他還沒達到的關係階段的故事。

    做法是讓 fake 在被呼叫的當下**開一個獨立 session 去查資料庫**。獨立 session
    很重要：共用的話看到的可能是同一個交易裡尚未 commit 的狀態，那證明不了
    「已經寫進去了」。
    """
    pid, sess = player
    observed = {}

    class _ObservingClient(FakeGeminiClient):
        def generate(self, prompt: str) -> str:
            if "observed_once" not in observed:
                probe = SessionLocal()
                try:
                    resonance = (
                        probe.query(models.Resonance)
                        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
                        .first()
                    )
                    observed["resonance"] = resonance.resonance_value if resonance else 0
                    observed["observed_once"] = True
                finally:
                    probe.close()
            return super().generate(prompt)

    app.dependency_overrides[get_gemini_client] = lambda: _ObservingClient(response=_GENERATED)
    try:
        _summon(client, spirit.spirit_id, sess)
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)

    assert observed.get("resonance") == 10, (
        "腦袋被呼叫時共鳴值還沒入帳——它拿到的 stage 會跟資料庫不一致"
    )
