"""
Ticket #45．POST /spirits/{placeId}/dialogue 完整版（接上 B4 安全邊界與 B2 組裝）。

驗收標準對照見 GitHub issue #45。憑證把關、配額、B12 命中、TTS 降級、共用
session key 都在 #42 的測試檔案裡驗過，這裡專注在 #45 新增的東西：B4 安全
分類決定要不要往下走生成、婉拒文案的形狀、以及 B2 組裝結果真的被拿去用。

`spirit`／`active_card`／`player` 沿用 `test_dialogue.py` 同一套 fixture
寫法（各測試檔案各自持有，不跨檔案 import，同本 repo 一貫做法）。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.redis_client import _session_key, redis_client
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.router import get_gemini_client, get_safety_classifier_client, get_tts_client
from app.modules.brain import safety as safety_module
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.models import CharacterPersona, Character, CitySoul, LandmarkSoul
from app.modules.brain.tts import FakeTTSClient

_LAT, _LON = 25.0955, 121.5186


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
    db_session.add(
        CharacterPersona(
            character_id=character_id, version=1,
            archetype="守望者", speech_style="溫和、話不多",
            imagination_license="不宣稱代替神明發言。",
            reviewed_by="test", reviewed_at=datetime.now(timezone.utc), active=True,
        )
    )
    db_session.commit()

    yield row

    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.delete(row)
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
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


def _say(client, spirit, session_token, encounter_token, text):
    headers = {
        "Authorization": f"Bearer {session_token}",
        ENCOUNTER_TOKEN_HEADER: encounter_token,
    }
    return client.post(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue", json={"user_input": text}, headers=headers
    )


def _mark_unsafe(category: str):
    """把安全分類換成永遠判定為指定類別的 fake。"""
    app.dependency_overrides[get_safety_classifier_client] = lambda: FakeGeminiClient(
        response=f"UNSAFE:{category}"
    )


def _teardown_overrides():
    app.dependency_overrides.pop(get_safety_classifier_client, None)
    app.dependency_overrides.pop(get_gemini_client, None)


# ── 不安全時婉拒，B2／B1 不被呼叫（issue #45 AC1）────────────────────────

def test_unsafe_input_is_refused_and_does_not_call_gemini_generation(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _mark_unsafe("high_risk_advice")
    fake_generation = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake_generation

    body = _say(client, spirit, sess, enc, "我這個症狀是不是癌症").json()

    assert body["source"] == "refused"
    assert fake_generation.call_count == 0  # 生成用的 B1 一次都沒被呼叫
    _teardown_overrides()


def test_unsafe_response_is_still_200(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _mark_unsafe("high_risk_advice")

    response = _say(client, spirit, sess, enc, "我這個症狀是不是癌症")

    assert response.status_code == 200
    _teardown_overrides()


# ── 婉拒台詞符合人格、帶回地標／任務／城市故事（issue #45 AC2）──────────

def test_refusal_text_matches_the_persona_templates(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _mark_unsafe("high_risk_advice")

    body = _say(client, spirit, sess, enc, "我這個症狀是不是癌症").json()

    assert body["reply_text"] == safety_module._REFUSAL_TEMPLATES[
        safety_module._SafetyCategory.HIGH_RISK_ADVICE
    ]
    assert "違反使用規範" not in body["reply_text"]
    _teardown_overrides()


# ── 高風險專業建議三類皆觸發婉拒（issue #45 AC3）─────────────────────────

@pytest.mark.parametrize("text", ["我這個症狀是不是癌症", "我可以告他嗎", "我該買哪支股票"])
def test_high_risk_professional_advice_is_refused(client, spirit, player, text):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _mark_unsafe("high_risk_advice")

    body = _say(client, spirit, sess, enc, text).json()

    assert body["source"] == "refused"
    assert text not in body["reply_text"]  # 沒有把輸入原樣複誦或延伸成建議
    _teardown_overrides()


# ── 宗教教義性提問依規範婉拒（issue #45 AC4）────────────────────────────

@pytest.mark.parametrize(
    "text", ["我求的這支籤是吉是凶？", "這尊神明真的有顯靈過嗎？", "佛教跟道教比起來哪個比較靈？"]
)
def test_religious_teaching_questions_are_refused(client, spirit, player, text):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _mark_unsafe("religious_boundary")

    body = _say(client, spirit, sess, enc, text).json()

    assert body["source"] == "refused"
    for doctrinal_word in ("很靈驗", "不靈驗", "會實現", "不會實現", "比較正統"):
        assert doctrinal_word not in body["reply_text"]
    _teardown_overrides()


# ── 安全的輸入正常進入完整流程（issue #45 AC5）───────────────────────────

def test_safe_input_flows_through_generation_and_tts(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake_generation = FakeGeminiClient(response="這座廟最早建於1738年。")
    app.dependency_overrides[get_gemini_client] = lambda: fake_generation
    app.dependency_overrides[get_tts_client] = lambda: FakeTTSClient()

    response = _say(client, spirit, sess, enc, "這座廟最早是什麼時候蓋的？")
    body = response.json()

    assert response.status_code == 200
    assert body["source"] == "generated"
    assert body["reply_text"] == "這座廟最早建於1738年。"
    assert "tts" in body and body["tts"]["audio_url"]
    assert fake_generation.call_count == 1
    _teardown_overrides()
    app.dependency_overrides.pop(get_tts_client, None)


def test_safe_input_prompt_includes_persona_content(client, spirit, player):
    """
    B2 組裝結果真的被拿去用，不是繞過去直接餵 user_input——檢查送進 Gemini
    的 prompt 含人格卡欄位內容。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake_generation = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake_generation

    _say(client, spirit, sess, enc, "這座廟最早是什麼時候蓋的？")

    assert len(fake_generation.prompts) == 1
    assert "守望者" in fake_generation.prompts[0]
    assert "這座廟最早是什麼時候蓋的？" in fake_generation.prompts[0]
    _teardown_overrides()


# ── 安全規則不重複塞進 System Instruction（issue #45 AC6）───────────────

def test_prompt_sent_to_gemini_does_not_repeat_safety_refusal_copy(client, spirit, player):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    fake_generation = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake_generation

    _say(client, spirit, sess, enc, "這座廟最早是什麼時候蓋的？")

    sent_prompt = fake_generation.prompts[0]
    for refusal_text in safety_module._REFUSAL_TEMPLATES.values():
        assert refusal_text not in sent_prompt
    _teardown_overrides()
