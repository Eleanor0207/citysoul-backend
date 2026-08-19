"""
`POST /spirits/{placeId}/dialogue` —— Phase 2 可用版的完整鏈路（issue #42）。

    Session ＋（Encounter 或 Sense）→ 配額 → B12 招呼 → B1 生成 → B10 語音
    → B7 短期記憶

憑證把關與 B12 命中／未命中的基本情境在 `test_dialogue.py`；這裡驗的是
**接上 B1／B10／配額之後才存在的行為**。分成兩個檔案是因為前者在 Phase 1
就有了，把它整個重寫會讓「哪些是既有保證、哪些是這次新增的」在 diff 裡看不出來。

模型與語音由 `conftest.py` 的 autouse fixture 預設注入 fake，所以整條路徑
**不需要 GCP 憑證**。要驗證失敗降級的測試自己覆寫成會失敗的 fake。

B4 安全邊界與 B2 完整組裝屬 Phase 3（#45），不在本檔範圍。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.redis_client import get_session, redis_client
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quota import RESOURCE_DIALOGUE, current_usage
from app.modules.body.router import FALLBACK_REPLY, get_gemini_client, get_tts_client
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.models import (
    CannedGreeting,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)
from app.modules.brain.safety import SafetyCategory, refusal_for
from app.modules.brain.tts import FakeTTSClient, TTSResult

_LAT, _LON = 25.0955, 121.5186
_GREETING = "你來了。今晚雲不多。"
_GENERATED = "那是在清乾隆三年，先民從泉州渡海而來的時候。"
_AUDIO_URL = "https://example.test/audio/abc.mp3"


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    """一條完整的 city → landmark → character → spirit 鏈，附生效人格與招呼語。"""
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    character_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[])
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    db_session.add(
        CharacterPersona(
            character_id=character_id,
            version=1,
            archetype="守望者",
            speech_style="溫和",
            reviewed_by="test",
            reviewed_at=datetime.now(timezone.utc),
            active=True,
        )
    )
    db_session.flush()
    db_session.add(
        CannedGreeting(
            character_id=character_id,
            version=1,
            trigger_phrases=["你好"],
            response_text=_GREETING,
        )
    )
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        character_id=character_id,
        landmark_id=landmark_id,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    yield row

    db_session.query(CannedGreeting).filter_by(character_id=character_id).delete()
    db_session.query(models.GuidedQuestionCache).filter_by(place_id=unique_spirit_id).delete()
    # backend#70：每日對話會入帳共鳴值。不先清掉 resonance/resonance_events
    # 就砍 spirit，會撞上 FK 約束——這兩張表沒有 ON DELETE CASCADE。
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.delete(row)
    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture(autouse=True)
def _clear_quota_and_sessions():
    yield
    for pattern in ("quota:*", "session:*"):
        for key in redis_client.scan_iter(pattern):
            redis_client.delete(key)


@pytest.fixture
def gemini():
    """記錄呼叫次數的 spy，覆寫掉 conftest 的預設 fake。"""
    spy = FakeGeminiClient(response=_GENERATED)
    app.dependency_overrides[get_gemini_client] = lambda: spy
    yield spy
    app.dependency_overrides.pop(get_gemini_client, None)


@pytest.fixture
def tts():
    fake = FakeTTSClient(TTSResult(audio_url=_AUDIO_URL))
    app.dependency_overrides[get_tts_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_tts_client, None)


def _say(client, spirit, session_token, text, *, encounter=None, sense=None):
    headers = {"Authorization": f"Bearer {session_token}"}
    if encounter:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter
    if sense:
        headers[SENSE_TOKEN_HEADER] = sense
    return client.post(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue",
        json={"user_input": text},
        headers=headers,
    )


def _set_dialogue_limit(db_session, player_id, value: int) -> int:
    """把該玩家分級的對話上限改成 value，回傳原值。"""
    player = db_session.query(models.Player).filter_by(player_id=player_id).one()
    row = (
        db_session.query(models.UsageTierLimit)
        .filter_by(tier_id=player.usage_tier_id, resource_type=RESOURCE_DIALOGUE)
        .one()
    )
    original = row.limit_value
    row.limit_value = value
    db_session.commit()
    return original


# ── Sense token 也能對話 ──────────────────────────────────────────────

def test_sense_token_is_accepted(client, spirit, player):
    """
    感應憑證（150m）換得到對話，跟相遇憑證（50m）一樣。

    兩者的差別在**別的端點**——感應憑證換不到召喚與任務挑戰。
    """
    pid, sess = player

    response = _say(client, spirit, sess, "你好", sense=issue_sense_token(pid, spirit.spirit_id))

    assert response.status_code == 200


def test_sense_token_for_another_spirit_returns_403(client, spirit, player):
    pid, sess = player

    response = _say(client, spirit, sess, "你好", sense=issue_sense_token(pid, "some-other-spirit"))

    assert response.status_code == 403


def test_neither_token_returns_401(client, spirit, player):
    _, sess = player

    assert _say(client, spirit, sess, "你好").status_code == 401


# ── B12：命中招呼不呼叫 Gemini ────────────────────────────────────────

def test_canned_greeting_does_not_call_gemini(client, spirit, player, gemini):
    """
    🔒 這是 B12 的全部價值（零成本、零延遲）。有呼叫就等於白做。

    AC 指定要做 mutation 驗證的那一條。
    """
    pid, sess = player

    body = _say(
        client, spirit, sess, "你好", encounter=issue_encounter_token(pid, spirit.spirit_id)
    ).json()

    assert body["reply_text"] == _GREETING
    assert body["source"] == "canned"
    assert gemini.call_count == 0


def test_miss_calls_gemini_and_returns_generated_text(client, spirit, player, gemini, tts):
    pid, sess = player

    body = _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert gemini.call_count == 1
    assert body["reply_text"] == _GENERATED
    assert body["source"] == "generated"
    assert body["tts"] == {"audio_url": _AUDIO_URL}


def test_prompt_sent_to_gemini_contains_persona_and_rules(client, spirit, player, gemini, tts):
    """B2 的組裝結果真的被送出去了，不是組好就丟掉。"""
    pid, sess = player

    _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    prompt = gemini.prompts[0]
    assert "守望者" in prompt  # 人格（B3）
    assert "定論" in prompt  # 史實規則（B5）
    assert "這座廟最早是什麼時候蓋的？" in prompt  # 玩家輸入


def test_safety_refusals_are_not_in_the_prompt(client, spirit, player, gemini, tts):
    """B4 不重複塞進 prompt（#12 的決策，在端到端層級再確認一次）。"""
    pid, sess = player

    _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert refusal_for(SafetyCategory.MEDICAL) not in gemini.prompts[0]


# ── tts 形狀 ──────────────────────────────────────────────────────────

def test_tts_object_has_only_audio_url(client, spirit, player, gemini, tts):
    """
    AC：`tts` 的鍵名集合恰為 `{"audio_url"}`。

    v2.1 §10.1 已移除 viseme 時間軸，對嘴由客戶端 uLipSync 負責。
    """
    pid, sess = player

    body = _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert set(body["tts"]) == {"audio_url"}


# ── 失敗降級：200 而不是 503 ──────────────────────────────────────────

def test_gemini_failure_returns_200_with_fallback(client, spirit, player, tts):
    """
    AC (a)：Gemini 失敗回 200 ＋ fallback，不是 503。

    SDD §8.5：模型失敗**不視為錯誤**，召喚流程不得中斷——玩家已經走到廟埕了，
    他不該因為我們的雲端服務打嗝而看到錯誤畫面。
    """
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient(response=FALLBACK_REPLY)
    pid, sess = player

    response = _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 200
    assert response.json()["reply_text"] == FALLBACK_REPLY
    assert response.json()["source"] == "fallback"


def test_tts_failure_returns_200_with_text_only(client, spirit, player, gemini):
    """
    AC (b)：TTS 失敗回 200 ＋ 有 reply_text 但無音訊。

    對照客戶端 F5：音檔載入失敗時角色維持靜止口型、對話文字照常顯示。
    兩端的降級行為刻意銜接。
    """
    app.dependency_overrides[get_tts_client] = lambda: FakeTTSClient(None)
    pid, sess = player

    response = _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 200
    assert response.json()["reply_text"] == _GENERATED
    assert response.json()["tts"] is None


# ── 配額 ──────────────────────────────────────────────────────────────

def test_quota_exhausted_returns_429_without_calling_gemini(client, spirit, player, db_session, gemini, tts):
    """
    AC：配額超過回 429，且 Gemini **未被呼叫**。

    配額是第一道關卡，擋下就不該產生成本。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    original = _set_dialogue_limit(db_session, pid, 1)

    try:
        assert _say(client, spirit, sess, "第一句話", encounter=enc).status_code == 200
        before = gemini.call_count

        response = _say(client, spirit, sess, "第二句話", encounter=enc)

        assert response.status_code == 429
        assert gemini.call_count == before, "配額擋下之後仍然呼叫了模型"
    finally:
        _set_dialogue_limit(db_session, pid, original)


def test_429_body_does_not_leak_the_limit(client, spirit, player, db_session, gemini, tts):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    original = _set_dialogue_limit(db_session, pid, 1)

    try:
        _say(client, spirit, sess, "第一句話", encounter=enc)
        body = _say(client, spirit, sess, "第二句話", encounter=enc).json()

        assert set(body) == {"detail", "resource", "reset_at"}
    finally:
        _set_dialogue_limit(db_session, pid, original)


@pytest.mark.parametrize("bad_input", ["", "   ", "字" * 501])
def test_invalid_input_returns_422_without_consuming_quota(client, spirit, player, bad_input):
    """
    AC：`user_input` 為空或超長回 422，且**配額用量完全沒變**。

    格式錯誤不該扣玩家額度。這由順序保證：FastAPI 的請求驗證在 handler 進入
    之前就完成了，所以 `consume()` 根本沒被執行到——不是靠我們記得先檢查。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    before = current_usage(pid, RESOURCE_DIALOGUE)
    response = _say(client, spirit, sess, bad_input, encounter=enc)

    assert response.status_code == 422
    assert current_usage(pid, RESOURCE_DIALOGUE) == before


# ── B7 短期記憶 ───────────────────────────────────────────────────────

def test_dialogue_is_written_to_short_term_memory(client, spirit, player, gemini, tts):
    pid, sess = player

    _say(
        client,
        spirit,
        sess,
        "這座廟最早是什麼時候蓋的？",
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    turns = get_session(str(pid), spirit.spirit_id)

    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "這座廟最早是什麼時候蓋的？"
    assert turns[1]["text"] == _GENERATED


def test_sense_and_encounter_share_one_session_key(client, spirit, player, gemini, tts):
    """
    🔒 AC：先用 sense token 講一句，再用 encounter token 講一句，
    **同一個 key 底下有兩輪對話**。

    玩家從 150m 隔空聊天走進 50m 召喚時，對話要自然延續（SDD §7.3）。
    session key 不含 token 種類，正是這件事成立的原因。
    """
    pid, sess = player

    _say(client, spirit, sess, "遠遠地問一句", sense=issue_sense_token(pid, spirit.spirit_id))
    _say(client, spirit, sess, "走近再問一句", encounter=issue_encounter_token(pid, spirit.spirit_id))

    turns = get_session(str(pid), spirit.spirit_id)

    assert len(turns) == 4  # 兩輪 × (user + assistant)
    assert turns[0]["text"] == "遠遠地問一句"
    assert turns[2]["text"] == "走近再問一句"


def test_short_term_memory_feeds_the_next_prompt(client, spirit, player, gemini, tts):
    """
    寫進短期記憶的內容，下一輪要出現在 prompt 裡。

    少了這條，B7 寫入與 B2 讀取可能各自都「正常」，但接不起來——玩家會覺得
    角色每一句都在重新認識他。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    _say(client, spirit, sess, "第一個問題", encounter=enc)
    _say(client, spirit, sess, "第二個問題", encounter=enc)

    assert "第一個問題" in gemini.prompts[-1]
