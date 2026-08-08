"""
Ticket #12．Prompt 組裝引擎（B2，SDD 第9節）。

驗收標準對照見 GitHub issue #12。人格卡與記憶都用測試自己建的夾具，不依賴
seed data；風格比照 `tests/test_persona_loader.py`（三層鏈的建法）與
`tests/test_memory_embeddings.py`（向量與隔離的測法）。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.redis_client import append_session_turn, redis_client
from app.modules.body.models import Spirit
from app.modules.brain.embeddings import FakeEmbeddingClient
from app.modules.brain.historical_boundary import HISTORICAL_BOUNDARY_RULE_TEXT
from app.modules.brain.loader import load_active_persona
from app.modules.brain.memory import write_memory
from app.modules.brain.models import (
    EMBEDDING_DIM,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)
from app.modules.brain.prompt_builder import (
    _CURRENT_INPUT_HEADER,
    _DAILY_NARRATIVE_HEADER,
    _LONG_TERM_MEMORY_HEADER,
    _SHORT_TERM_HISTORY_HEADER,
    build_prompt,
)


def _vec(value: float = 1.0) -> list[float]:
    """給 `write_memory` 用的隨便一個合法維度向量——語意內容不重要。"""
    v = [0.0] * EMBEDDING_DIM
    v[0] = value
    return v


@pytest.fixture
def player_id():
    return uuid.uuid4()


@pytest.fixture
def persona_spirit(db_session, unique_spirit_id):
    """
    一整條 city → landmark → character → spirit → 生效人格 的鏈。

    人格卡的每個欄位都填值，讓 `_render_persona` 的每個分支都有東西可以
    斷言，而不是只測到「有沒有炸」。
    """
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
            spirit_id=unique_spirit_id,
            display_name="測試地標",
            character_id=character_id,
            landmark_id=landmark_id,
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            is_active=True,
        )
    )
    db_session.add(
        CharacterPersona(
            character_id=character_id,
            version=1,
            archetype="一位溫和的守護者",
            speech_style="用溫暖平靜的語氣說話",
            personality_traits=["溫和", "耐心"],
            values=["守護", "傾聽"],
            taboos=["不代神明發言", "不預測吉凶"],
            not_this_character="你不是廟方人員，也不是神明本身。",
            imagination_license="你可以用詩意的方式描述自己的存在，但不宣稱擁有超自然能力。",
            tone_override="偶爾使用台語詞彙增添親切感。",
            reviewed_by="test-reviewer",
            reviewed_at=datetime.now(timezone.utc),
            active=True,
        )
    )
    db_session.commit()

    yield unique_spirit_id

    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def embedding_client():
    return FakeEmbeddingClient(_vec())


@pytest.fixture(autouse=True)
def _cleanup_redis(player_id):
    """
    只依賴 `player_id`，刻意不依賴 `persona_spirit`——autouse fixture 依賴
    另一個 fixture 會強迫它在每個測試都被實例化一次，而 `persona_spirit`
    內部也用了 `unique_spirit_id`；如果測試自己又直接要了一份
    `unique_spirit_id`（兩者在同一個測試裡是同一個快取值），就會撞成
    同一個 spirit_id 被插入兩次。用 pattern 掃 key 沒有這個耦合問題。
    """
    yield
    for key in redis_client.keys(f"session:{player_id}:*"):
        redis_client.delete(key)


# ── System Instruction 順序（AC1／AC2） ─────────────────────────────────


def test_system_instruction_has_persona_before_historical_boundary_rule(
    db_session, player_id, persona_spirit, embedding_client
):
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="這座廟最早是什麼時候蓋的？",
        embedding_client=embedding_client,
    )

    persona_index = result.system_instruction.index("一位溫和的守護者")
    rule_index = result.system_instruction.index(HISTORICAL_BOUNDARY_RULE_TEXT)
    assert persona_index < rule_index


def test_system_instruction_does_not_duplicate_safety_boundary_content(
    db_session, player_id, persona_spirit, embedding_client
):
    """
    B4（#11）在輸入端先過濾，安全規則不重複塞進 System Instruction——
    這裡直接驗證組裝結果恰好是「人格卡 + B5 規則」兩段，沒有第三段。
    """
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="這座廟最早是什麼時候蓋的？",
        embedding_client=embedding_client,
    )

    parts = result.system_instruction.split("\n\n")
    assert len(parts) == 2
    assert HISTORICAL_BOUNDARY_RULE_TEXT in parts[1]


# ── User Turn 順序與缺項（AC3） ─────────────────────────────────────────


def test_user_turn_includes_all_four_sections_in_order(
    db_session, player_id, persona_spirit, embedding_client
):
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        summary_text="上次聊到廟的歷史",
        embedding=_vec(),
        source="dialogue_summary",
    )
    append_session_turn(str(player_id), persona_spirit, {"role": "user", "text": "早安"})

    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="今天天氣真好",
        embedding_client=embedding_client,
        daily_narrative="今天廟埕有陣頭表演",
    )

    headers_in_order = [
        _DAILY_NARRATIVE_HEADER,
        _LONG_TERM_MEMORY_HEADER,
        _SHORT_TERM_HISTORY_HEADER,
        _CURRENT_INPUT_HEADER,
    ]
    indices = [result.user_turn.index(h) for h in headers_in_order]
    assert indices == sorted(indices)

    for header in headers_in_order:
        assert header in result.user_turn


def test_user_turn_omits_daily_narrative_when_absent(
    db_session, player_id, persona_spirit, embedding_client
):
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="今天天氣真好",
        embedding_client=embedding_client,
        daily_narrative=None,
    )

    assert _DAILY_NARRATIVE_HEADER not in result.user_turn
    assert _CURRENT_INPUT_HEADER in result.user_turn


def test_user_turn_omits_long_term_memory_when_none_exists(
    db_session, player_id, persona_spirit, embedding_client
):
    """冷啟動：玩家對這個靈魂還沒有任何長期記憶。"""
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="這是我第一次跟你說話",
        embedding_client=embedding_client,
    )

    assert _LONG_TERM_MEMORY_HEADER not in result.user_turn


def test_user_turn_omits_short_term_history_when_none_exists(
    db_session, player_id, persona_spirit, embedding_client
):
    """首次對話：Redis 裡還沒有這個 (player, spirit) 的 session。"""
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="這是我第一次跟你說話",
        embedding_client=embedding_client,
    )

    assert _SHORT_TERM_HISTORY_HEADER not in result.user_turn


def test_user_turn_never_raises_and_never_leaves_blank_placeholders(
    db_session, player_id, persona_spirit, embedding_client
):
    """(b)(c)(d) 三種缺項組合都不拋例外，也不留下空白佔位符。"""
    result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="哈囉",
        embedding_client=embedding_client,
        daily_narrative=None,
    )

    assert "None" not in result.user_turn
    assert result.user_turn.strip() != ""


# ── Top-K／短期記憶輪數可設定（AC4） ─────────────────────────────────────


def test_top_k_and_short_term_turns_are_configurable(
    db_session, player_id, persona_spirit, embedding_client
):
    for i in range(5):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=persona_spirit,
            summary_text=f"長期記憶第{i}筆",
            embedding=_vec(),
            source="dialogue_summary",
        )
    for i in range(4):
        append_session_turn(
            str(player_id), persona_spirit, {"role": "user", "text": f"第{i}輪"}
        )

    default_result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="哈囉",
        embedding_client=embedding_client,
    )
    custom_result = build_prompt(
        db_session,
        player_id=player_id,
        spirit_id=persona_spirit,
        user_input="哈囉",
        embedding_client=embedding_client,
        top_k=1,
        short_term_turns=2,
    )

    default_memory_lines = default_result.user_turn.count("長期記憶第")
    custom_memory_lines = custom_result.user_turn.count("長期記憶第")
    assert default_memory_lines == 3  # DEFAULT_TOP_K
    assert custom_memory_lines == 1

    default_turn_lines = default_result.user_turn.count("輪")
    custom_turn_lines = custom_result.user_turn.count("輪")
    assert default_turn_lines > custom_turn_lines


# ── 沒有生效人格卡（AC5） ────────────────────────────────────────────────


def test_returns_none_when_no_active_persona(db_session, player_id, unique_spirit_id):
    row = Spirit(
        spirit_id=unique_spirit_id,
        display_name="還沒接上人格的地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    try:
        db_session.add(row)
        db_session.commit()

        assert load_active_persona(db_session, unique_spirit_id) is None
        result = build_prompt(
            db_session,
            player_id=player_id,
            spirit_id=unique_spirit_id,
            user_input="哈囉",
            embedding_client=FakeEmbeddingClient(_vec()),
        )
        assert result is None
    finally:
        db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
        db_session.commit()


# ── 長期記憶依 player_id 隔離（AC6） ────────────────────────────────────


def test_long_term_memory_is_isolated_by_player_id(
    db_session, persona_spirit, embedding_client
):
    player_p = uuid.uuid4()
    player_q = uuid.uuid4()

    write_memory(
        db_session,
        player_id=player_p,
        spirit_id=persona_spirit,
        summary_text="P的記憶內容",
        embedding=_vec(),
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=player_q,
        spirit_id=persona_spirit,
        summary_text="Q的記憶內容",
        embedding=_vec(),
        source="dialogue_summary",
    )

    result = build_prompt(
        db_session,
        player_id=player_p,
        spirit_id=persona_spirit,
        user_input="哈囉",
        embedding_client=embedding_client,
    )

    assert "P的記憶內容" in result.user_turn
    assert "Q的記憶內容" not in result.user_turn
