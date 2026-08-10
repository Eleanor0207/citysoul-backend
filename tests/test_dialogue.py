"""
Ticket #42．POST /spirits/{placeId}/dialogue 可用版整合測試。
"""
import uuid
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token
from app.modules.body.router import get_gemini_client, get_tts_client, FALLBACK_REPLY
from app.modules.brain.gemini import GeminiClient, FakeGeminiClient
from app.modules.brain.tts import FakeTTSClient, TTSResult

_SPIRIT_ID = "test_dialogue_spirit"


class SpyGeminiClient(GeminiClient):
    """計數監控 Fake Gemini Client。"""

    def __init__(self, preset_reply: str = "老樹萌新芽，天地自悠悠。"):
        self.preset_reply = preset_reply
        self.call_count = 0

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        return self.preset_reply


@pytest.fixture
def spy_gemini():
    return SpyGeminiClient()


@pytest.fixture
def fake_tts():
    return FakeTTSClient(preset_url="https://example.test/audio/reply.mp3")


@pytest.fixture
def test_app_client(spy_gemini, fake_tts):
    """以 FastAPI dependency_overrides 注入 Fake/Spy 測試元件。"""
    app.dependency_overrides[get_gemini_client] = lambda: spy_gemini
    app.dependency_overrides[get_tts_client] = lambda: fake_tts
    client = TestClient(app)
    yield client, spy_gemini, fake_tts
    app.dependency_overrides.clear()


@pytest.fixture
def spirit_row(db_session):
    row = models.Spirit(
        spirit_id=_SPIRIT_ID,
        display_name="測試靈魂地標",
        latitude=25.0330,
        longitude=121.5654,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def session_data(client, spirit_row):
    """產生測試用玩家與 Tokens。"""
    resp = client.post("/api/v1/players", json={"device_id": f"dev-{uuid.uuid4()}"})
    player_id = resp.json()["player_id"]
    session_token = resp.json()["session_token"]
    encounter_token = issue_encounter_token(player_id, _SPIRIT_ID)
    sense_token = issue_sense_token(player_id, _SPIRIT_ID)
    return {
        "player_id": player_id,
        "session_token": session_token,
        "encounter_token": encounter_token,
        "sense_token": sense_token,
    }


# ── 1. Token 驗證與 401 / 403 門檻 ──────────────────────────────────────────

def test_missing_both_encounter_and_sense_tokens_returns_401(test_app_client, session_data):
    client, _, _ = test_app_client
    headers = {"Authorization": f"Bearer {session_data['session_token']}"}
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "你好"},
        headers=headers,
    )
    assert resp.status_code == 401


def test_token_spirit_mismatch_returns_403(test_app_client, session_data):
    client, _, _ = test_app_client
    wrong_encounter = issue_encounter_token(session_data["player_id"], "other_spirit_id")
    headers = {
        "Authorization": f"Bearer {session_data['session_token']}",
        ENCOUNTER_TOKEN_HEADER: wrong_encounter,
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "你好"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_token_holder_mismatch_returns_403(test_app_client, session_data):
    client, _, _ = test_app_client
    other_player_encounter = issue_encounter_token(str(uuid.uuid4()), _SPIRIT_ID)
    headers = {
        "Authorization": f"Bearer {session_data['session_token']}",
        ENCOUNTER_TOKEN_HEADER: other_player_encounter,
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "你好"},
        headers=headers,
    )
    assert resp.status_code == 403


# ── 2. B12 固定招呼比對：不呼叫 Gemini ─────────────────────────────────────

def test_canned_greeting_hit_does_not_call_gemini(test_app_client, session_data, db_session):
    client, spy_gemini, _ = test_app_client

    # 塞入測試預寫招呼
    canned_row = models.CannedGreeting(
        spirit_id=_SPIRIT_ID,
        trigger_keyword="平安",
        response_text="平安就是福，願神明保佑你。",
    )
    db_session.add(canned_row)
    db_session.commit()

    headers = {
        "Authorization": f"Bearer {session_data['session_token']}",
        ENCOUNTER_TOKEN_HEADER: session_data["encounter_token"],
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "求平安"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "平安就是福，願神明保佑你。"
    assert data["source"] == "canned"
    assert spy_gemini.call_count == 0, "命中預寫招呼時，Gemini 不得被呼叫"

    db_session.delete(canned_row)
    db_session.commit()


# ── 3. 未命中招呼：走 Gemini 與 TTS ──────────────────────────────────────────

def test_dialogue_miss_calls_gemini_and_tts(test_app_client, session_data):
    client, spy_gemini, _ = test_app_client
    headers = {
        "Authorization": f"Bearer {session_data['session_token']}",
        ENCOUNTER_TOKEN_HEADER: session_data["encounter_token"],
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "這座寺廟建於什麼時候？"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == "老樹萌新芽，天地自悠悠。"
    assert data["source"] == "gemini"
    assert data["tts"] == {"audio_url": "https://example.test/audio/reply.mp3"}
    assert spy_gemini.call_count == 1
    assert "viseme_timeline" not in data["tts"]


# ── 4. 失敗降級：Gemini 或 TTS 失敗保持 HTTP 200 ─────────────────────────────

def test_gemini_or_tts_failure_fallback_returns_200():
    """驗證當 TTS 失敗時仍回傳 200 與文字，tts 降級為 None。"""
    app.dependency_overrides[get_gemini_client] = lambda: FakeGeminiClient(fallback=True)
    app.dependency_overrides[get_tts_client] = lambda: FakeTTSClient(fail=True)

    client = TestClient(app)
    # 產生臨時權限
    p_resp = client.post("/api/v1/players", json={"device_id": f"dev-{uuid.uuid4()}"})
    pid = p_resp.json()["player_id"]
    token = p_resp.json()["session_token"]
    enc_token = issue_encounter_token(pid, _SPIRIT_ID)

    headers = {
        "Authorization": f"Bearer {token}",
        ENCOUNTER_TOKEN_HEADER: enc_token,
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "測試失敗降級"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply_text"] == FALLBACK_REPLY
    assert data["source"] == "fallback"
    assert data["tts"] is None

    app.dependency_overrides.clear()


# ── 5. B4 安全檢查：不安全輸入回傳婉拒且不呼叫 Gemini (BE#45) ─────────────

def test_unsafe_input_triggers_refusal_and_does_not_call_gemini(test_app_client, session_data):
    """驗證當輸入高風險醫療議題時，觸發 B4 安全婉拒且不呼叫 Gemini (call_count == 0)。"""
    client, spy_gemini, _ = test_app_client
    headers = {
        "Authorization": f"Bearer {session_data['session_token']}",
        ENCOUNTER_TOKEN_HEADER: session_data["encounter_token"],
    }
    resp = client.post(
        f"/api/v1/spirits/{_SPIRIT_ID}/dialogue",
        json={"user_input": "我這個症狀是不是癌症？"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "refusal"
    assert "這類專業或個人選擇的問題" in data["reply_text"]
    assert spy_gemini.call_count == 0, "觸發安全婉拒時，Gemini AI 生成一次都不得被呼叫"

