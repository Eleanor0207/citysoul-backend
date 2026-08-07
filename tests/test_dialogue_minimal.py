"""
對話端點：憑證把關 ＋ B12 命中／未命中的基本行為。

issue #42 把 Gemini／TTS／配額接上之後，這支檔案縮小成只驗證「憑證把關」跟
「命中／未命中兩種路徑分別選對 source」——這兩件事在 #42 之後仍然成立，不用
搬到新測試檔重複驗一次。**配額、Sense Token、spy 驗證 Gemini／TTS 呼叫次數、
TTS 失敗降級、`tts.audio_url` 形狀**這些 #42 新增的行為，見 `test_dialogue.py`。

## 這裡改掉的兩件事

- `test_miss_returns_fallback` → 未命中現在會呼叫 Gemini（`source` 從
  `"fallback"` 變成 `"generated"`），不再直接回 `router.FALLBACK_REPLY`——
  那個常數本身已經跟著 #42 一起移除，見 `router.py` 的 diff。
- `test_response_has_no_tts_field` 整條移除：`tts` 欄位現在會出現（`conftest`
  的 autouse fixture 預設用 `FakeTTSClient` 合成成功），這條測試的前提已經
  不成立。`tts` 到底該不該出現、出現時長什麼樣子，屬於 `test_dialogue.py`
  的範圍。
"""
import uuid
from datetime import datetime, timezone

import jwt
import pytest

from app.core.config import settings
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.brain.models import (
    CannedGreeting,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)

_LAT, _LON = 25.0955, 121.5186
_GREETING = "你來了。今晚雲不多。"


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    """
    一條完整的 city → landmark → character → spirit 鏈。

    0005 之後靈魂要經由 `character_id` 才找得到人格，所以夾具比以前多三層。
    """
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    character_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(
            landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[]
        )
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    row = models.Spirit(
        spirit_id=unique_spirit_id, display_name="測試地標", latitude=_LAT, longitude=_LON,
        character_id=character_id, landmark_id=landmark_id,
        summon_radius_meters=50, is_active=True,
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


def _say(client, spirit, session_token, encounter_token, text):
    headers = {}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter_token:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter_token
    return client.post(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue",
        json={"user_input": text}, headers=headers,
    )


# ── 憑證把關 ───────────────────────────────────────────────────────────

def test_missing_session_token_returns_401(client, spirit, player):
    pid, _ = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    assert _say(client, spirit, None, enc, "你好").status_code == 401


def test_missing_encounter_token_returns_401(client, spirit, player):
    _, sess = player
    assert _say(client, spirit, sess, None, "你好").status_code == 401


def test_encounter_token_for_another_spirit_returns_403(client, spirit, player):
    """相遇憑證屬於別的地標時回 403——憑證有效，只是不適用於此處。"""
    pid, sess = player
    other = issue_encounter_token(pid, "some-other-spirit")
    assert _say(client, spirit, sess, other, "你好").status_code == 403


def test_session_token_cannot_be_used_as_encounter_token(client, spirit, player):
    """
    拿 session token 塞進 X-Encounter-Token 不能過。

    兩者金鑰不同本來就會擋下，這條是把「不可互相冒充」變成看得見的事實。
    """
    _, sess = player
    assert _say(client, spirit, sess, sess, "你好").status_code == 401


def test_tokens_from_different_players_are_rejected(client, spirit, player):
    """
    A 的 session 配 B 的相遇憑證要被擋。

    少了這道檢查，沒到現場的人就能借用別人的在場證明——在場驗證會整個失效。
    """
    _, sess_a = player
    stranger = uuid.uuid4()
    enc_b = issue_encounter_token(stranger, spirit.spirit_id)
    assert _say(client, spirit, sess_a, enc_b, "你好").status_code == 403


def test_forged_encounter_token_with_session_secret_is_rejected(client, spirit, player):
    pid, sess = player
    forged = jwt.encode(
        {"sub": str(pid), "spirit_id": spirit.spirit_id, "purpose": "encounter"},
        settings.session_token_secret, algorithm="HS256",
    )
    assert _say(client, spirit, sess, forged, "你好").status_code == 401


# ── 回應內容 ───────────────────────────────────────────────────────────

def test_canned_greeting_hit(client, spirit, player, active_card):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()
    assert body["reply_text"] == _GREETING
    assert body["source"] == "canned"


def test_miss_goes_through_generation(client, spirit, player, active_card):
    """
    未命中固定招呼時交給 Gemini（這裡是 conftest 預設注入的 `FakeGeminiClient`）
    生成，`source` 是 `"generated"`——是否命中內部真的呼叫了模型、還是模型
    失敗後自己回退，這一層看不出也不需要看出（見 `gemini.py` 模組說明）。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "圓頂是什麼時候蓋的？").json()
    assert body["source"] == "generated"
    assert body["reply_text"]  # 非空字串即可，內容由 B1 決定，不是這層的責任


def test_no_active_persona_falls_through_to_generation(client, spirit, player):
    """人格是 active=False 的草稿（或根本還沒建）時，一律當作未命中，不拋例外。"""
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()
    assert body["source"] == "generated"


@pytest.mark.parametrize("bad_input", ["", "   "])
def test_blank_input_returns_422(client, spirit, player, bad_input):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    assert _say(client, spirit, sess, enc, bad_input).status_code == 422


def test_inactive_spirit_returns_404(client, spirit, player, db_session):
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    spirit.is_active = False
    db_session.commit()
    assert _say(client, spirit, sess, enc, "你好").status_code == 404
