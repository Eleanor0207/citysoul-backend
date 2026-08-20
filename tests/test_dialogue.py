"""
`POST /spirits/{placeId}/dialogue` —— 對話完整鏈路（issue #45）。

    Session ＋（Encounter 或 Sense）→ 配額 → B12 招呼 → B4 安全閘
    → B2 組裝 → B1 生成 → B10 語音 → B7 短期記憶

模型與語音由 `conftest.py` 的 autouse fixture 預設注入 fake，所以整條路徑
**不需要 GCP 憑證**。要驗證失敗降級的測試自己覆寫成會失敗的 fake。

B4 安全邊界與 B2 完整組裝已接在端點中；本檔補上逐地標安全閘的端到端驗收。
"""
import uuid
from datetime import datetime, timezone

import jwt
import pytest

from app.core.config import settings
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.router import FALLBACK_REPLY, get_gemini_client
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.models import (
    CannedGreeting,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)
from app.modules.brain.safety import (
    FakeSafetyChecker,
    SafetyCategory,
    SafetyGate,
    SafetyResult,
    refusal_for,
)

_LAT, _LON = 25.0955, 121.5186
_GREETING = "你來了。今晚雲不多。"


@pytest.fixture
def gemini_factory():
    """讓每個情境都能注入固定回應並記錄本次請求的模型呼叫。"""
    clients = []

    def make(response: str):
        client = FakeGeminiClient(response=response)
        clients.append(client)
        app.dependency_overrides[get_gemini_client] = lambda: client
        return client

    yield make
    app.dependency_overrides.pop(get_gemini_client, None)


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
    db_session.query(models.GuidedQuestionCache).filter_by(place_id=unique_spirit_id).delete()
    # backend#70：每日對話會入帳共鳴值。不先清掉 resonance/resonance_events
    # 就砍 spirit，會撞上 FK 約束——這兩張表沒有 ON DELETE CASCADE。
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=unique_spirit_id).delete()
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


def test_no_active_persona_falls_back(client, spirit, player):
    """人格是 active=False 的草稿（或根本還沒建）時，一律 fallback，不拋例外。"""
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()
    assert body["source"] == "fallback"


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


def test_response_shape(client, spirit, player, active_card):
    """
    SDD v2.1 §10.1：`{ reply_text, tts: { audio_url } }`（＋除錯用的 source）。

    `segments` 是附加欄位：把 `reply_text` 依空行切好，讓客戶端能逐段推播。
    **它不取代 `reply_text`**——舊版客戶端忽略它仍然正確。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()

    assert set(body) == {"reply_text", "source", "segments", "tts", "suggested_questions"}
    # 分段是 reply_text 的切片，不是另一份內容
    assert body["segments"]
    for seg in body["segments"]:
        assert seg in body["reply_text"]


# ── 共鳴值：每日對話（backend#70／#52 拍板）────────────────────────────

def _resonance_value(db_session, player_id, spirit_id):
    row = (
        db_session.query(models.Resonance)
        .filter_by(player_id=player_id, spirit_id=spirit_id)
        .first()
    )
    return row.resonance_value if row else 0


def test_generated_reply_awards_daily_dialogue_resonance(
    client, db_session, spirit, player, active_card
):
    """source=="generated" 是真的跟靈魂對上話，該入帳。"""
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？").json()

    assert body["source"] == "generated"
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


def test_canned_greeting_also_awards_resonance(client, db_session, spirit, player, active_card):
    """source=="canned" 一樣算對上話——只是不需要跑模型，不代表沒聊天。"""
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()

    assert body["source"] == "canned"
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


def test_fallback_reply_does_not_award_resonance(client, db_session, spirit, player):
    """
    source=="fallback"（沒有生效人格卡，或生成失敗）不算：玩家拿到的是
    一句人工預寫保底句，不是這個靈魂真的回應了什麼。
    """
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()

    assert body["source"] == "fallback"
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 0


def test_refused_reply_does_not_award_resonance(client, db_session, spirit, player, active_card):
    """source=="refused"（B4 擋下）不算：玩家沒有真的跟靈魂對上話。"""
    spirit.safety_gate_enabled = True
    db_session.commit()

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    # 刻意不用「你好」：2026-08-20 起完全相等命中預寫招呼會在 B4 之前短路，
    # 那條路徑回的是 canned 不是 refused。要測 B4 擋下就得用非招呼的輸入。
    body = _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？").json()

    assert body["source"] == "refused"
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 0


def test_second_dialogue_same_day_does_not_award_twice(
    client, db_session, spirit, player, active_card
):
    """一天只入帳一次，同一天第二輪對話不重複加值。"""
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)

    _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？")
    body = _say(client, spirit, sess, enc, "還有其他故事嗎？").json()

    assert body["source"] == "generated"
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


def test_dialogue_resonance_is_per_spirit_not_global(
    client, db_session, spirit, player, active_card, unique_spirit_id
):
    """
    resonance_events 的 UNIQUE 不含 spirit_id（見 ResonanceEvent 的
    docstring）——source_id 沒帶好靈魂 id 的話，跟一個靈魂聊過會讓所有
    靈魂當天都算入帳。這裡用第二個靈魂驗證沒有這件事。
    """
    other_id = f"{unique_spirit_id}-other"
    other = models.Spirit(
        spirit_id=other_id, display_name="另一個測試地標",
        latitude=_LAT, longitude=_LON, summon_radius_meters=50, is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        pid, sess = player
        enc = issue_encounter_token(pid, spirit.spirit_id)
        _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？")

        assert _resonance_value(db_session, pid, spirit.spirit_id) == 10
        assert _resonance_value(db_session, pid, other_id) == 0
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()


# ── B4 安全閘（逐地標開關，0019）──────────────────────────────────────

def test_safety_gate_is_off_by_default(client, spirit, player, active_card):
    """
    預設不跑 B4。開了就是每輪多一次 Gemini 呼叫，延遲與成本加倍——那個代價
    只值得付在風險高的地標上（見 migration 0019）。
    """
    assert spirit.safety_gate_enabled is False

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？").json()

    assert body["source"] != "refused"


def test_gated_spirit_refuses_without_calling_the_model(
    client, db_session, spirit, player, active_card
):
    """
    開了閘的靈魂，分類判定不出來時 **fail-closed**，而且下游一次都不會被呼叫。

    測試用的 `FakeGeminiClient` 回的是固定字串，解析不出任何標籤——那正是
    「分類失敗」的情境。B4 的設計是往嚴格的方向倒（`safety.py` 模組註解），
    所以這裡預期婉拒。

    `source == "refused"` 是這條保證的證據而不是另一個推斷：`source` 的初始值
    就是 refused，只有下游真的被呼叫到才會被改寫。
    """
    # commit 而不是 flush：路由跑在自己的 session 裡（`get_db` 沒有被覆寫成
    # 共用），沒 commit 的話它看不到這個改動。
    spirit.safety_gate_enabled = True
    db_session.commit()

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    # 非招呼的輸入：完全相等命中預寫招呼會在 B4 之前短路（見下一條測試），
    # 用「你好」測不到 fail-closed。
    body = _say(client, spirit, sess, enc, "這座廟什麼時候蓋的？").json()

    assert body["source"] == "refused"
    assert body["reply_text"] != _GREETING
    assert body["reply_text"]


def test_exact_canned_greeting_short_circuits_the_safety_gate(
    client, db_session, spirit, player, active_card
):
    """
    **B12 短路的不變式測試（2026-08-20）。**

    完全相等命中人工審核過的觸發語時，直接回預寫台詞，不跑 B4、不呼叫模型。

    這條測試同時釘住兩件事：

    1. **行為**：開了閘的靈魂對「你好」回 `canned`，不是 `refused`。改壞了
       會退回舊行為——每一句招呼都燒一次分類呼叫，而且「拜拜」這種同時是
       再見又是祭拜的詞會被誤攔（實測龍山寺／故宮／新文化運動三隻皆然）。

    2. **前提**：短路的安全性完全建立在「完全相等」上。下一條測試守著這一半
       ——包含觸發語但不相等的輸入**不能**短路，否則夾帶就成立了。
    """
    spirit.safety_gate_enabled = True
    db_session.commit()

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好").json()

    assert body["source"] == "canned"
    assert body["reply_text"] == _GREETING


def test_smuggled_input_does_not_short_circuit_the_safety_gate(
    client, db_session, spirit, player, active_card
):
    """
    短路的安全性前提：**包含**觸發語不等於命中。

    「你好，我想自殺」含有「你好」，但不等於它，所以不短路、照樣進 B4。
    哪天有人把 B12 改成模糊比對或前綴比對，這條會紅——那時候 router 的短路
    必須跟著搬回 B4 後面。
    """
    spirit.safety_gate_enabled = True
    db_session.commit()

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, "你好，我想自殺").json()

    assert body["source"] == "refused"
    assert body["reply_text"] != _GREETING


# ── B4 完整分類與 B1 阻斷 ─────────────────────────────────────────────

_DANGEROUS_DIALOGUES = [
    (SafetyCategory.MEDICAL, "我這個症狀是不是癌症？"),
    (SafetyCategory.LEGAL, "我可以告他嗎？"),
    (SafetyCategory.FINANCIAL, "我該買哪一支股票？"),
    (SafetyCategory.RELIGIOUS_DOCTRINE, "這個籤詩代表我一定會發財嗎？"),
]


@pytest.mark.parametrize("category,user_input", _DANGEROUS_DIALOGUES)
def test_安全閘攔下各類高風險提問並阻止生成(
    client, db_session, spirit, player, active_card, gemini_factory, category, user_input
):
    """
    AC：醫療、法律、財務與宗教教義提問都在生成前婉拒。

    fake Gemini 回固定分類標籤；分類本身會用掉一次呼叫，但 B1 生成不應再被呼叫。
    `source` 維持 refused，證明 router 的下游 closure 沒有被安全閘放行。
    """
    spirit.safety_gate_enabled = True
    db_session.commit()
    gemini = gemini_factory(category)

    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    body = _say(client, spirit, sess, enc, user_input).json()

    assert body["source"] == "refused"
    assert body["reply_text"] == refusal_for(category)
    assert gemini.call_count == 1, "分類後不應再呼叫 Gemini 生成回應"


def test_安全輸入的系統指令不含B4文案或分類規則(
    client, spirit, player, active_card, gemini_factory
):
    """
    AC：B4 在輸入端攔截，不把婉拒文案或分類規則重複塞進 B2 的 system instruction。

    從送給 fake Gemini 的完整 prompt 取出 `build_prompt()` 的 system instruction，
    這樣測到的是端點實際送出的組裝結果，而不是只測一個沒有被呼叫的函式。
    """
    gemini = gemini_factory("安全的生成回應")
    pid, sess = player
    enc = issue_encounter_token(pid, spirit.spirit_id)
    _say(client, spirit, sess, enc, "這座廟最早是什麼時候蓋的？")
    system_instruction = gemini.prompts[0].split("\n\n---\n\n", 1)[0]

    for category in SafetyCategory.__dict__.values():
        if isinstance(category, str) and category != SafetyCategory.SAFE:
            refusal = refusal_for(category)
            assert refusal not in system_instruction
            assert refusal[:10] not in system_instruction

    for rule_fragment in (
        "你是一個輸入分類器",
        "safe：一般對話",
        "self_harm：自傷、輕生、傷害他人",
        "medical：詢問病症、診斷、治療、用藥",
        "legal：詢問法律責任、訴訟、權利義務",
        "financial：詢問投資、理財、金錢決策",
        "religious_doctrine：詢問教義解釋",
        "political_stance：詢問政治立場",
        "other：其他不適合或明顯偏離主題",
    ):
        assert rule_fragment not in system_instruction


def test_安全閘被旁路時不安全輸入仍會呼叫生成模型(
    client, spirit, player, active_card
):
    """
    mutation 對照：若 checker 被突變成永遠回傳安全，危險輸入就會流到下游。

    這條刻意使用永遠放行的 `FakeSafetyChecker`，確認測試能看見 B1 被叫到；
    上面的端點測試則保證正式分類結果不會走到這條路。
    """
    gemini = FakeGeminiClient(response="不該送出的生成結果")
    gate = SafetyGate(FakeSafetyChecker(SafetyResult(is_safe=True)))

    reply = gate.run("我該買哪一支股票？", gemini.generate)

    assert reply == "不該送出的生成結果"
    assert gemini.call_count == 1


def test_generated_text_has_no_stray_spaces_after_chinese_punctuation():
    """
    模型會寫出「什麼沒見過。 1815 年」這種排版——句號後的空格在中文裡沒有意義，
    但它會一路帶到玩家眼前，也會被 TTS 讀成一個停頓。

    `1945 年` 的空格保留：那是數字與中文之間的排版慣例。
    """
    from app.core.text_normalize import strip_fold_spaces

    assert strip_fold_spaces("什麼沒見過。 1815 年那場地震") == "什麼沒見過。1815 年那場地震"
    assert strip_fold_spaces("1945 年臺北大空襲") == "1945 年臺北大空襲"
