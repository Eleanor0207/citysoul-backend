"""
Ticket #42．POST /spirits/{placeId}/dialogue 可用版（Phase 2 端到端打通）。

驗收標準對照見 GitHub issue #42。憑證把關與 B12 命中／未命中的基本行為留在
`test_dialogue_minimal.py`，不重複驗一次；這裡專注在 #42 新增的部分：
Sense Token、配額、Gemini／TTS 的 spy 驗證與失敗降級、短期記憶。

`conftest.py` 的 autouse fixture 已經把 `get_gemini_client`／`get_tts_client`
換成預設成功的 fake，這裡的測試視情況再另外覆寫。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.redis_client import _session_key, get_session, redis_client
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.models import Player, UsageTier, UsageTierLimit
from app.modules.body.quota import _quota_key
from app.modules.body.router import get_gemini_client, get_tts_client
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.models import (
    CannedGreeting,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)
from app.modules.brain.tts import FakeTTSClient, TTSResult

_LAT, _LON = 25.0955, 121.5186
_GREETING = "你來了。今晚雲不多。"


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    character_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[])
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    row = models.Spirit(
        spirit_id=unique_spirit_id, display_name="測試地標", latitude=_LAT, longitude=_LON,
        character_id=character_id, landmark_id=landmark_id,
        summon_radius_meters=50, sense_radius_meters=150, is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    yield row

    db_session.query(CannedGreeting).filter_by(character_id=character_id).delete()
    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.delete(row)
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def active_card(db_session, spirit):
    db_session.add(
        CharacterPersona(
            character_id=spirit.character_id, version=1,
            archetype="守望者", speech_style="溫和",
            reviewed_by="test", reviewed_at=datetime.now(timezone.utc), active=True,
        )
    )
    db_session.flush()
    db_session.add(
        CannedGreeting(
            character_id=spirit.character_id, version=1,
            trigger_phrases=["你好", "hello"], response_text=_GREETING,
        )
    )
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture(autouse=True)
def _redis_cleanup(spirit, player):
    yield
    pid, _ = player
    redis_client.delete(_session_key(str(pid), spirit.spirit_id))
    for key in redis_client.scan_iter(match=f"quota:{pid}:*"):
        redis_client.delete(key)


def _say(client, spirit, session_token, *, encounter_token=None, sense_token=None, text="你好"):
    headers = {}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter_token:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter_token
    if sense_token:
        headers[SENSE_TOKEN_HEADER] = sense_token
    return client.post(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue", json={"user_input": text}, headers=headers
    )


# ── Sense Token 也能通過（issue #42 AC1，補 test_dialogue_minimal 沒測的一半）──

def test_sense_token_alone_is_sufficient(client, spirit, player):
    pid, sess = player
    sense = issue_sense_token(pid, spirit.spirit_id)
    assert _say(client, spirit, sess, sense_token=sense).status_code == 200


def test_sense_token_for_another_spirit_returns_403(client, spirit, player):
    pid, sess = player
    other_sense = issue_sense_token(pid, "some-other-spirit")
    assert _say(client, spirit, sess, sense_token=other_sense).status_code == 403


def test_missing_both_encounter_and_sense_returns_401(client, spirit, player):
    _, sess = player
    assert _say(client, spirit, sess).status_code == 401


# ── 配額（issue #32／#42 AC）─────────────────────────────────────────────

@pytest.fixture
def small_quota_player(db_session, player):
    """把玩家配到一個 dialogue_calls_daily 上限只有 2 的分級，測完還原。"""
    pid, sess = player
    tier_id = f"tt-{uuid.uuid4().hex[:8]}"
    db_session.add(UsageTier(tier_id=tier_id, display_name="測試分級", is_default=False))
    db_session.flush()
    db_session.add(UsageTierLimit(tier_id=tier_id, resource_type="dialogue_calls_daily", limit_value=2))
    db_session.commit()

    row = db_session.query(Player).filter_by(player_id=pid).first()
    original_tier_id = row.usage_tier_id
    row.usage_tier_id = tier_id
    db_session.commit()

    yield pid, sess

    row.usage_tier_id = original_tier_id
    db_session.commit()
    db_session.query(UsageTierLimit).filter_by(tier_id=tier_id).delete()
    db_session.query(UsageTier).filter_by(tier_id=tier_id).delete()
    db_session.commit()


def test_quota_exceeded_returns_429(client, spirit, small_quota_player):
    pid, sess = small_quota_player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    assert _say(client, spirit, sess, encounter_token=enc, text="第一句").status_code == 200
    assert _say(client, spirit, sess, encounter_token=enc, text="第二句").status_code == 200
    assert _say(client, spirit, sess, encounter_token=enc, text="第三句").status_code == 429


def test_quota_exceeded_body_does_not_leak_internal_details(client, spirit, small_quota_player):
    pid, sess = small_quota_player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _say(client, spirit, sess, encounter_token=enc, text="第一句")
    _say(client, spirit, sess, encounter_token=enc, text="第二句")

    body = _say(client, spirit, sess, encounter_token=enc, text="第三句").json()
    assert str(pid) not in str(body)  # 不外洩任何可辨識的內部識別碼


def test_quota_exceeded_does_not_call_gemini(client, spirit, small_quota_player):
    pid, sess = small_quota_player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake

    _say(client, spirit, sess, encounter_token=enc, text="第一句")
    _say(client, spirit, sess, encounter_token=enc, text="第二句")
    fake.prompts.clear()  # 只看第三次呼叫有沒有打到 Gemini

    _say(client, spirit, sess, encounter_token=enc, text="第三句")

    assert fake.call_count == 0
    app.dependency_overrides.pop(get_gemini_client, None)


def test_blank_input_does_not_consume_quota(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    response = _say(client, spirit, sess, encounter_token=enc, text="   ")

    assert response.status_code == 422
    assert redis_client.get(_quota_key(pid, "dialogue_calls_daily", datetime.now(timezone.utc).date())) is None


# ── B12 命中不呼叫 Gemini（spy，issue #42 AC）────────────────────────────

def test_canned_hit_never_calls_gemini(client, spirit, player, active_card):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake

    body = _say(client, spirit, sess, encounter_token=enc, text="你好").json()

    assert body["reply_text"] == _GREETING
    assert body["source"] == "canned"
    assert fake.call_count == 0
    app.dependency_overrides.pop(get_gemini_client, None)


# ── 未命中：Gemini + TTS，回應含 reply_text 與 tts.audio_url ─────────────

def test_miss_calls_gemini_once_and_returns_audio_url(client, spirit, player, active_card):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake_gemini = FakeGeminiClient(response="這座廟最早建於1738年。")
    fake_tts = FakeTTSClient(TTSResult(audio_url="https://example.test/audio/xyz.mp3"))
    app.dependency_overrides[get_gemini_client] = lambda: fake_gemini
    app.dependency_overrides[get_tts_client] = lambda: fake_tts

    response = _say(client, spirit, sess, encounter_token=enc, text="這座廟幾年蓋的？")
    body = response.json()

    assert response.status_code == 200
    assert body["reply_text"] == "這座廟最早建於1738年。"
    assert body["source"] == "generated"
    assert body["tts"] == {"audio_url": "https://example.test/audio/xyz.mp3"}
    assert fake_gemini.call_count == 1
    app.dependency_overrides.pop(get_gemini_client, None)
    app.dependency_overrides.pop(get_tts_client, None)


def test_response_never_contains_viseme_timeline(client, spirit, player, active_card):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    body = _say(client, spirit, sess, encounter_token=enc, text="這座廟幾年蓋的？").json()

    assert "viseme" not in str(body).lower()
    if "tts" in body:
        assert set(body["tts"]) == {"audio_url"}


# ── TTS 合成失敗時降級為純文字，不是 503（issue #42 AC）──────────────────

def test_tts_failure_degrades_to_text_only_200(client, spirit, player, active_card):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    app.dependency_overrides[get_tts_client] = lambda: FakeTTSClient(fail=True)

    response = _say(client, spirit, sess, encounter_token=enc, text="這座廟幾年蓋的？")
    body = response.json()

    assert response.status_code == 200
    assert body["reply_text"]
    assert "tts" not in body
    app.dependency_overrides.pop(get_tts_client, None)


# ── 對話寫入短期記憶，感應／召喚共用同一 session key（issue #42 AC）───────

def test_sense_and_encounter_turns_share_the_same_session_key(client, spirit, player, active_card):
    pid, sess = player
    sense = issue_sense_token(pid, spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    _say(client, spirit, sess, sense_token=sense, text="你好")
    _say(client, spirit, sess, encounter_token=enc, text="這座廟幾年蓋的？")

    turns = get_session(str(pid), spirit.spirit_id)
    user_texts = [t["text"] for t in turns if t.get("role") == "user"]
    assert "你好" in user_texts
    assert "這座廟幾年蓋的？" in user_texts
