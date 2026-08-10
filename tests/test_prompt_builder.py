"""
Ticket #12．Prompt 組裝引擎（B2）。

驗收標準對照見 GitHub issue #12。

`build_system_instruction`／`build_user_turn` 是純函式（不碰資料庫），用假的
`CharacterPersona` 物件（未 add 進 session，純粹當資料容器用）跟手造的
list/dict 測；`assemble_dialogue_prompt` 是接資料庫／Redis 的組裝入口，走
真實 Postgres／Redis，沿用 `test_canned_greetings.py` 的 city→landmark→
character→persona→spirit 鏈接建立方式。
"""
from datetime import datetime, timezone

import pytest

from app.core.redis_client import _session_key, append_session_turn, redis_client
from app.modules.brain import safety as safety_module
from app.modules.brain.historical_boundary import combine_with_persona_boundary
from app.modules.brain.memory import EMBEDDING_DIM, write_memory
from app.modules.brain.models import Character, CharacterPersona, CitySoul, LandmarkSoul
from app.modules.brain.prompt_builder import (
    DEFAULT_SHORT_TERM_TURNS,
    DEFAULT_TOP_K,
    assemble_dialogue_prompt,
    build_system_instruction,
    build_user_turn,
)
from app.modules.body.models import Spirit


def _persona(**overrides) -> CharacterPersona:
    """未附著到 session 的人格卡物件，純粹當資料容器給純函式用。"""
    defaults = dict(
        character_id="test-character",
        version=1,
        archetype="守望者",
        speech_style="溫和、話不多",
        reviewed_by="test-reviewer",
        reviewed_at=datetime.now(timezone.utc),
        active=True,
    )
    defaults.update(overrides)
    return CharacterPersona(**defaults)


def _embedding(seed: float) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[0] = seed
    return v


# ── build_system_instruction（AC1／AC2）─────────────────────────────────

def test_persona_appears_before_historical_boundary():
    """
    AC1：人格卡內容出現在 B5 規則之前。順序影響模型的權重感知，對調這兩段
    的 mutation 必須讓這條測試變紅。
    """
    persona = _persona(archetype="獨一無二的原型標記")
    boundary = "獨一無二的史實邊界標記"

    instruction = build_system_instruction(persona, boundary)

    assert instruction.index("獨一無二的原型標記") < instruction.index("獨一無二的史實邊界標記")


def test_system_instruction_contains_all_persona_fields():
    persona = _persona(
        archetype="守望者",
        speech_style="溫和",
        personality_traits=["沉靜", "耐心"],
        values=["記憶", "傾聽"],
        taboos=["求籤吉凶判定"],
        not_this_character="不是廟方人員、不是神明本尊",
        quest_themes=["尋找失物"],
        tone_override="對孩童更輕聲",
    )

    instruction = build_system_instruction(persona, "通則")

    for expected in ["守望者", "溫和", "沉靜", "耐心", "記憶", "傾聽", "求籤吉凶判定",
                      "不是廟方人員、不是神明本尊", "尋找失物", "對孩童更輕聲"]:
        assert expected in instruction


def test_optional_persona_fields_can_all_be_absent():
    """人格草稿只填必填欄位時仍可組裝，不拋例外、不留空白標籤行。"""
    persona = _persona(
        personality_traits=None, values=None, taboos=None,
        not_this_character=None, quest_themes=None, tone_override=None,
    )

    instruction = build_system_instruction(persona, "通則文字")

    assert "守望者" in instruction
    assert "通則文字" in instruction
    # 沒有內容的欄位不該留下光禿禿的標籤行。
    assert "性格特質：\n" not in instruction
    assert "不是誰：\n" not in instruction


def test_system_instruction_does_not_repeat_safety_refusal_copy():
    """
    AC2：安全規則不重複塞進 System Instruction。B4（issue #11）沒有任何
    要注入 system instruction 的文字——這裡直接確認組裝結果不含 B4 的婉拒
    模板內容，把「沒有這件事」變成看得見的斷言，而不是只靠沒 import 來保證。
    """
    persona = _persona()
    instruction = build_system_instruction(persona, "通則文字")

    for refusal_text in safety_module._REFUSAL_TEMPLATES.values():
        assert refusal_text not in instruction


# ── build_user_turn（AC3）───────────────────────────────────────────────

def test_user_turn_with_all_sections_present_and_ordered():
    result = build_user_turn(
        daily_context="今天廟埕比較安靜。",
        long_term_memory_summaries=["上次你問過關於重建的事。"],
        short_term_turns=[{"role": "user", "text": "你好"}],
        player_input="這座廟幾點開門？",
    )

    for expected in ["今天廟埕比較安靜。", "上次你問過關於重建的事。", "你好", "這座廟幾點開門？"]:
        assert expected in result

    assert (
        result.index("今天廟埕比較安靜。")
        < result.index("上次你問過關於重建的事。")
        < result.index("你好")
        < result.index("這座廟幾點開門？")
    )


def test_user_turn_without_daily_context():
    result = build_user_turn(
        daily_context=None,
        long_term_memory_summaries=["記憶片段"],
        short_term_turns=[{"role": "user", "text": "嗨"}],
        player_input="輸入",
    )

    assert "今日情境" not in result
    assert "記憶片段" in result
    assert "輸入" in result


def test_user_turn_without_long_term_memory():
    """冷啟動：玩家還沒有任何長期記憶。"""
    result = build_user_turn(
        daily_context="情境", long_term_memory_summaries=[],
        short_term_turns=[{"role": "user", "text": "嗨"}], player_input="輸入",
    )

    assert "過去記憶" not in result
    assert "情境" in result
    assert "輸入" in result


def test_user_turn_without_short_term_memory():
    """首次對話：還沒有任何近期對話輪次。"""
    result = build_user_turn(
        daily_context="情境", long_term_memory_summaries=["記憶片段"],
        short_term_turns=[], player_input="輸入",
    )

    assert "近期對話" not in result
    assert "記憶片段" in result
    assert "輸入" in result


def test_user_turn_with_nothing_but_player_input():
    """四項都缺，只剩玩家輸入——不拋例外，結果只含輸入本身那一段。"""
    result = build_user_turn(
        daily_context=None, long_term_memory_summaries=[], short_term_turns=[],
        player_input="只有我",
    )

    assert result == "【玩家本次輸入】\n只有我"


# ── assemble_dialogue_prompt（AC4／AC5／AC6，接資料庫與 Redis）──────────

@pytest.fixture
def persona_setup(db_session, unique_spirit_id):
    """city → landmark → character → spirit 鏈，回傳 character_id。"""
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
        Spirit(
            spirit_id=unique_spirit_id, display_name="測試地標",
            character_id=character_id, landmark_id=landmark_id,
            latitude=25.0, longitude=121.5, summon_radius_meters=50, is_active=True,
        )
    )
    db_session.add(
        CharacterPersona(
            character_id=character_id, version=1, archetype="守望者", speech_style="溫和",
            imagination_license="不宣稱代替神明發言。",
            reviewed_by="test-reviewer", reviewed_at=datetime.now(timezone.utc), active=True,
        )
    )
    db_session.commit()

    yield character_id

    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def player_id():
    import uuid

    return uuid.uuid4()


@pytest.fixture(autouse=True)
def _redis_cleanup(unique_spirit_id, player_id):
    yield
    redis_client.delete(_session_key(str(player_id), unique_spirit_id))


def test_no_active_persona_returns_none(db_session, unique_spirit_id, player_id):
    """AC5：沒有生效人格卡時優雅處理，回傳 None，不拋例外。"""
    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="你好",
    )
    assert result is None


def test_historical_boundary_is_combined_with_persona_imagination_license(
    db_session, unique_spirit_id, player_id, persona_setup,
):
    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="你好",
    )

    assert result is not None
    assert "不宣稱代替神明發言。" in result.system_instruction
    assert combine_with_persona_boundary(None) in result.system_instruction


def test_default_top_k_limits_long_term_memory(db_session, unique_spirit_id, player_id, persona_setup):
    for i in range(5):
        write_memory(
            db_session, player_id=player_id, spirit_id=unique_spirit_id,
            summary_text=f"記憶{i}", embedding=_embedding(1.0), source="test",
        )

    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="你好",
        query_embedding=_embedding(1.0),
    )

    matched = sum(1 for i in range(5) if f"記憶{i}" in result.user_turn)
    assert matched == DEFAULT_TOP_K == 3


def test_custom_top_k_is_respected(db_session, unique_spirit_id, player_id, persona_setup):
    for i in range(5):
        write_memory(
            db_session, player_id=player_id, spirit_id=unique_spirit_id,
            summary_text=f"記憶{i}", embedding=_embedding(1.0), source="test",
        )

    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="你好",
        query_embedding=_embedding(1.0), top_k=5,
    )

    matched = sum(1 for i in range(5) if f"記憶{i}" in result.user_turn)
    assert matched == 5


def test_no_query_embedding_skips_long_term_memory(db_session, unique_spirit_id, player_id, persona_setup):
    write_memory(
        db_session, player_id=player_id, spirit_id=unique_spirit_id,
        summary_text="不該出現的記憶", embedding=_embedding(1.0), source="test",
    )

    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="你好",
    )

    assert "不該出現的記憶" not in result.user_turn
    assert "過去記憶" not in result.user_turn


def test_default_short_term_turn_limit(db_session, unique_spirit_id, player_id, persona_setup):
    for i in range(10):
        append_session_turn(str(player_id), unique_spirit_id, {"role": "user", "text": f"第{i}句"})

    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="最新輸入",
    )

    kept = sum(1 for i in range(10) if f"第{i}句" in result.user_turn)
    assert kept == DEFAULT_SHORT_TERM_TURNS == 6
    # 保留的必須是「最近」6輪，不是隨便6輪：第9句（最新）在，第0句（最舊）不在。
    assert "第9句" in result.user_turn
    assert "第0句" not in result.user_turn


def test_custom_short_term_turn_limit(db_session, unique_spirit_id, player_id, persona_setup):
    for i in range(10):
        append_session_turn(str(player_id), unique_spirit_id, {"role": "user", "text": f"第{i}句"})

    result = assemble_dialogue_prompt(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, player_input="最新輸入",
        short_term_turn_limit=2,
    )

    kept = sum(1 for i in range(10) if f"第{i}句" in result.user_turn)
    assert kept == 2


def test_long_term_memory_is_isolated_by_player(db_session, unique_spirit_id, persona_setup):
    """
    AC6：長期記憶檢索依 player_id + spirit_id 隔離。玩家 P 的組裝結果不該
    含玩家 Q 對同一靈魂的記憶文字。
    """
    import uuid

    player_p, player_q = uuid.uuid4(), uuid.uuid4()

    write_memory(
        db_session, player_id=player_p, spirit_id=unique_spirit_id,
        summary_text="P的記憶內容", embedding=_embedding(1.0), source="test",
    )
    write_memory(
        db_session, player_id=player_q, spirit_id=unique_spirit_id,
        summary_text="Q的記憶內容", embedding=_embedding(1.0), source="test",
    )

    result = assemble_dialogue_prompt(
        db_session, player_id=player_p, spirit_id=unique_spirit_id, player_input="你好",
        query_embedding=_embedding(1.0),
    )

    assert "P的記憶內容" in result.user_turn
    assert "Q的記憶內容" not in result.user_turn
