"""
B2．Prompt 組裝引擎（issue #12）。

順序、缺項與隔離。組裝的兩半刻意可以分開測：`build_system_instruction` 與
`build_user_turn` 都是純函式，只有 `build_prompt` 需要資料庫。
"""
import uuid

import pytest

from app.core.redis_client import append_session_turn, redis_client
from app.modules.body import models as body_models
from app.modules.brain import models as brain_models
from app.modules.brain.historical_boundary import get_historical_boundary_rules
from app.modules.brain.memory import write_memory
from app.modules.brain.models import EMBEDDING_DIM
from app.modules.brain.prompt_builder import (
    build_prompt,
    build_system_instruction,
    build_user_turn,
)
from app.modules.brain.safety import SafetyCategory, refusal_for


class _Persona:
    """
    不碰資料庫的人格替身，欄位名跟 `brain.character_personas` 一致。

    純函式測試用這個而不是真的建一列——組裝順序跟資料庫怎麼存無關。
    """

    archetype = "沉靜、耐心的守望者"
    personality_traits = ["沉靜", "耐心"]
    values = ["艋舺的市井生活"]
    speech_style = "溫和、不疾不徐"
    tone_override = None
    not_this_character = "不是廟方人員，不是神明本身"
    taboos = ["代替神明給予指示或應許"]
    imagination_license = "神祕感來自時間累積的記憶本身；不宣稱靈驗"


# ── System Instruction 順序 ───────────────────────────────────────────

def test_persona_comes_before_the_historical_rules():
    """
    AC：人格卡內容出現在 B5 規則**之前**。

    順序影響模型的權重感知，不是隨意排列——「你是誰」決定「你怎麼說」，而史實
    規則是對說話方式的限制。限制放在被限制的對象之後才讀得懂。
    """
    text = build_system_instruction(_Persona())

    persona_at = text.index("沉靜、耐心的守望者")
    rules_at = text.index("談到歷史時")

    assert persona_at < rules_at


def test_system_instruction_contains_both_layers():
    text = build_system_instruction(_Persona())

    assert "溫和、不疾不徐" in text  # 人格
    assert "定論" in text  # B5 通則
    assert "不是廟方人員" in text  # not_this_character
    assert "代替神明給予指示或應許" in text  # taboos


def test_persona_imagination_license_appears_exactly_once():
    """
    `imagination_license` 由 B5 疊進史實規則那一段，第 1 段不再重複列出。

    同一條界線寫兩次不會讓模型更遵守，只會讓兩處日後不同步。
    """
    text = build_system_instruction(_Persona())

    assert text.count("神祕感來自時間累積的記憶本身") == 1


def test_empty_persona_fields_are_omitted_not_printed_as_blank():
    """
    空欄位跳過，不印成「（無）」。

    人格卡是人工編輯的內容，還沒填的欄位就是還沒填——讓模型知道有一個空欄位
    存在，只會讓它試圖解釋那個空白。
    """

    class _Sparse(_Persona):
        personality_traits = []
        values = None
        tone_override = None
        taboos = []

    text = build_system_instruction(_Sparse())

    assert "性格特質" not in text
    assert "在意的事" not in text
    assert "不談論的主題" not in text
    assert "（無）" not in text


# ── B4 不重複注入 ─────────────────────────────────────────────────────

def test_safety_refusal_text_is_not_injected_into_system_instruction():
    """
    AC：System Instruction **不含** B4 的安全檢查規則文字。

    安全已在輸入端過濾（#11 的 `SafetyGate`）。重複注入只是浪費 token 並稀釋
    人格描述，而且會讓「安全是誰的責任」出現兩個答案。

    真正的差別：塞進 System Instruction 是**請求模型自律**，而自律是機率性的；
    輸入端過濾是**下游根本不會被呼叫**。
    """
    text = build_system_instruction(_Persona())

    for category in [
        SafetyCategory.SELF_HARM,
        SafetyCategory.MEDICAL,
        SafetyCategory.LEGAL,
        SafetyCategory.FINANCIAL,
    ]:
        assert refusal_for(category) not in text


def test_system_instruction_does_not_contain_classification_labels():
    """B4 的分類標籤也不該外洩到 prompt 裡。"""
    text = build_system_instruction(_Persona())

    assert "self_harm" not in text
    assert "religious_doctrine" not in text


# ── User Turn 順序與缺項 ───────────────────────────────────────────────

_DAILY = "今夜的香火比平常更盛一些。"
_MEMORY = "這位玩家上次問過廟埕的石獅子。"
_TURNS = [{"role": "user", "text": "你好"}, {"role": "assistant", "text": "你來了。"}]
_INPUT = "這座廟最早是什麼時候蓋的？"


def test_all_four_sections_appear_in_order():
    """AC (a)：四項齊全時依序出現。"""
    text = build_user_turn(
        user_input=_INPUT,
        daily_context=_DAILY,
        long_term_memories=[_MEMORY],
        recent_turns=_TURNS,
    )

    positions = [text.index(_DAILY), text.index(_MEMORY), text.index("你來了。"), text.index(_INPUT)]

    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "missing,absent_marker",
    [
        ("daily_context", "今天的城市情境"),
        ("long_term_memories", "長期記憶"),
        ("recent_turns", "剛才的對話"),
    ],
)
def test_missing_sections_are_omitted_gracefully(missing, absent_marker):
    """
    AC (b)(c)(d)：缺的那段優雅省略，其餘順序不變，不拋例外、不出現空白佔位符。
    """
    kwargs = {
        "user_input": _INPUT,
        "daily_context": _DAILY,
        "long_term_memories": [_MEMORY],
        "recent_turns": _TURNS,
    }
    kwargs[missing] = None if missing == "daily_context" else []

    text = build_user_turn(**kwargs)

    assert absent_marker not in text
    assert _INPUT in text
    assert "（無）" not in text and "N/A" not in text


def test_user_input_is_always_last():
    """
    玩家本次輸入永遠在最後。

    模型對最靠近結尾的內容反應最強，而「玩家現在說什麼」正是它要回應的東西。
    被記憶或情境墊在後面的話，回應會開始漂向背景資訊。
    """
    text = build_user_turn(user_input=_INPUT, daily_context=_DAILY, long_term_memories=[_MEMORY])

    assert text.rstrip().endswith(_INPUT)


def test_only_user_input_when_everything_else_is_missing():
    """完全冷啟動：沒有情境、沒有記憶、首次對話。"""
    text = build_user_turn(user_input=_INPUT)

    assert _INPUT in text
    assert "長期記憶" not in text
    assert "剛才的對話" not in text


def test_malformed_history_turns_are_skipped_not_fatal():
    """
    短期記憶存在 Redis 的 JSON 裡，沒有 schema 約束。舊格式的殘留輪次不該讓
    整次對話組裝失敗——認不出來就跳過。
    """
    text = build_user_turn(
        user_input=_INPUT,
        recent_turns=[{"role": "user", "text": "有效"}, "不是 dict", {"role": "user"}, None],
    )

    assert "有效" in text
    assert _INPUT in text


# ── 需要資料庫的整合部分 ───────────────────────────────────────────────

@pytest.fixture
def spirit_with_persona(db_session, unique_spirit_id):
    """一個有生效人格卡的靈魂。測試結束後整組刪掉。"""
    character_id = f"char-{uuid.uuid4().hex[:8]}"
    landmark_id = f"landmark-{uuid.uuid4().hex[:8]}"
    city_id = f"city-{uuid.uuid4().hex[:8]}"

    db_session.add(
        brain_models.CitySoul(
            city_id=city_id,
            name="測試城市",
            macro_history_summary="—",
            core_tone_descriptors=["—"],
            shared_values=["—"],
        )
    )
    db_session.add(
        brain_models.LandmarkSoul(
            landmark_id=landmark_id,
            city_id=city_id,
            name="測試地標",
            founding_facts=[{"year": "—", "event": "—", "detail": "—"}],
        )
    )
    db_session.flush()
    db_session.add(brain_models.Character(character_id=character_id, landmark_id=landmark_id))
    db_session.add(
        brain_models.CharacterPersona(
            character_id=character_id,
            version=1,
            archetype="沉靜、耐心的守望者",
            speech_style="溫和、不疾不徐",
            not_this_character="不是廟方人員",
            imagination_license="不宣稱靈驗",
            active=True,
            reviewed_by="test",
            reviewed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
    )
    db_session.add(
        body_models.Spirit(
            spirit_id=unique_spirit_id,
            display_name="測試地標",
            character_id=character_id,
            landmark_id=landmark_id,
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )
    db_session.commit()

    yield unique_spirit_id

    db_session.query(body_models.Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(brain_models.CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(brain_models.Character).filter_by(character_id=character_id).delete()
    db_session.query(brain_models.LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(brain_models.CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def player_id():
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def _clear_sessions():
    yield
    for key in redis_client.scan_iter("session:*"):
        redis_client.delete(key)


def test_build_prompt_returns_none_without_an_active_persona(db_session, unique_spirit_id, player_id):
    """
    AC：沒有生效人格卡時**不拋例外**。

    封閉測試期人格長期是 `active=False`，那是常態不是例外——B3 的
    `load_active_persona` 回 None 是既有契約，這一層負責接住它。
    """
    db_session.add(
        body_models.Spirit(
            spirit_id=unique_spirit_id,
            display_name="沒有人格的靈魂",
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            sense_radius_meters=150,
            is_active=True,
        )
    )
    db_session.commit()

    try:
        assert build_prompt(
            db_session, spirit_id=unique_spirit_id, player_id=player_id, user_input=_INPUT
        ) is None
    finally:
        db_session.query(body_models.Spirit).filter_by(spirit_id=unique_spirit_id).delete()
        db_session.commit()


def test_build_prompt_assembles_both_halves(db_session, spirit_with_persona, player_id):
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert prompt is not None
    assert "沉靜、耐心的守望者" in prompt.system_instruction
    assert _INPUT in prompt.user_turn
    # 合併形式含兩半。
    assert prompt.system_instruction in prompt.as_single_text()
    assert prompt.user_turn in prompt.as_single_text()


def test_recent_turns_limit_is_configurable(db_session, spirit_with_persona, player_id):
    """AC：輪數可設定。放 20 輪，只取最後 N 輪。"""
    for i in range(20):
        append_session_turn(str(player_id), spirit_with_persona, {"role": "user", "text": f"第{i}句"})

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        recent_turns=2,
    )

    assert "第19句" in prompt.user_turn
    assert "第18句" in prompt.user_turn
    assert "第17句" not in prompt.user_turn


def test_default_turn_limit_comes_from_settings(db_session, spirit_with_persona, player_id):
    """
    預設值來自設定，不是組裝邏輯裡的字面量。

    SDD §10 標明這個數字待實測調整——寫死的話，調整就要改程式碼。
    """
    from app.core.config import settings

    for i in range(20):
        append_session_turn(str(player_id), spirit_with_persona, {"role": "user", "text": f"第{i}句"})

    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    oldest_kept = 20 - settings.prompt_recent_turns
    assert f"第{oldest_kept}句" in prompt.user_turn
    assert f"第{oldest_kept - 1}句" not in prompt.user_turn


def test_long_term_memory_is_skipped_without_an_embedding(db_session, spirit_with_persona, player_id):
    """
    `query_embedding` 為 None 時跳過長期記憶檢索。

    repo 裡還沒有產生 embedding 的模組（B6 只做了 schema 與檢索），所以這是
    **實際的預設路徑**，不是假設性的分支。
    """
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert "長期記憶" not in prompt.user_turn


def test_long_term_memory_is_isolated_per_player(db_session, spirit_with_persona, player_id):
    """
    🔒 AC：組裝結果中**不含其他玩家的記憶文字**。

    這是資料隔離，不是相關性問題（見 B6 `retrieve_similar_memories` 的註解）。
    """
    other_player = uuid.uuid4()
    embedding = [0.1] * EMBEDDING_DIM

    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=spirit_with_persona,
        summary_text="我自己的記憶",
        embedding=embedding,
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=other_player,
        spirit_id=spirit_with_persona,
        summary_text="別人的記憶不該出現",
        embedding=embedding,
        source="dialogue_summary",
    )

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        query_embedding=embedding,
    )

    assert "我自己的記憶" in prompt.user_turn
    assert "別人的記憶不該出現" not in prompt.user_turn


def test_top_k_is_configurable(db_session, spirit_with_persona, player_id):
    """AC：Top-K 可設定。"""
    embedding = [0.1] * EMBEDDING_DIM
    for i in range(10):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=spirit_with_persona,
            summary_text=f"記憶{i}",
            embedding=embedding,
            source="dialogue_summary",
        )

    prompt = build_prompt(
        db_session,
        spirit_id=spirit_with_persona,
        player_id=player_id,
        user_input=_INPUT,
        query_embedding=embedding,
        top_k=5,
    )

    assert prompt.user_turn.count("記憶") >= 5
    kept = sum(1 for i in range(10) if f"記憶{i}" in prompt.user_turn)
    assert kept == 5


def test_short_term_memory_is_isolated_per_player(db_session, spirit_with_persona, player_id):
    """短期記憶的隔離靠 Redis 的 key 結構（session:{player}:{spirit}），不是事後過濾。"""
    other_player = uuid.uuid4()
    append_session_turn(str(other_player), spirit_with_persona, {"role": "user", "text": "別人說的話"})

    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    assert "別人說的話" not in prompt.user_turn


def test_historical_rules_are_the_shared_ones(db_session, spirit_with_persona, player_id):
    """B5 的通則原封不動出現，不是 B2 自己抄一份。"""
    prompt = build_prompt(
        db_session, spirit_id=spirit_with_persona, player_id=player_id, user_input=_INPUT
    )

    general = get_historical_boundary_rules()
    assert general in prompt.system_instruction
