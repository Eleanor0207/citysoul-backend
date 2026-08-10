"""
B2．Prompt 組裝引擎單元與邏輯測試。
"""
from datetime import datetime, timezone
import pytest

from app.modules.brain.models import CharacterPersona
from app.modules.brain.prompt_builder import (
    DEFAULT_SYSTEM_INSTRUCTION,
    HISTORICAL_BOUNDARY_RULE,
    build_system_instruction,
    build_user_turn,
)


@pytest.fixture
def mock_persona():
    return CharacterPersona(
        character_id="test_char",
        version=1,
        archetype="守護神明",
        speech_style="語氣溫和、帶有古風",
        personality_traits=["慈悲", "莊嚴"],
        values=["庇佑眾生", "傳承歷史"],
        taboos=["不代神明發言", "不預測吉凶"],
        not_this_character="不是輕浮的娛樂角色",
        imagination_license="允許想像古建築歷史風貌",
        quest_themes=["香火傳承", "建築欣賞"],
        reviewed_by="admin",
        reviewed_at=datetime.now(timezone.utc),
        active=True,
    )


# ── 1. System Instruction 順序與內容測試 ────────────────────────────────────

def test_system_instruction_order(mock_persona):
    """驗證 System Instruction 依指定順序包含人格卡 (B3) 與史實邊界 (B5)。"""
    sys_inst = build_system_instruction(mock_persona)

    pos_archetype = sys_inst.find("角色原型：守護神明")
    pos_style = sys_inst.find("說話風格：語氣溫和、帶有古風")
    pos_boundary = sys_inst.find("【史實與立場規則】")

    assert pos_archetype != -1
    assert pos_style != -1
    assert pos_boundary != -1

    # 人格卡內容必須出現在 B5 史實邊界規則之前
    assert pos_archetype < pos_boundary
    assert pos_style < pos_boundary


def test_safety_rules_not_in_system_instruction(mock_persona):
    """驗證 B4 安全檢查規則不重複塞進 System Instruction。"""
    sys_inst = build_system_instruction(mock_persona)
    assert "安全檢查中介層" not in sys_inst
    assert "SafetyChecker" not in sys_inst


def test_missing_active_persona_graceful():
    """驗證沒有生效人格卡時回傳預設指示，不拋出例外。"""
    sys_inst = build_system_instruction(None)
    assert sys_inst == DEFAULT_SYSTEM_INSTRUCTION
    assert HISTORICAL_BOUNDARY_RULE in sys_inst


# ── 2. User Turn 順序與缺項處理測試 ───────────────────────────────────────

def test_user_turn_order_all_present():
    """驗證當所有資訊皆存在時，User Turn 依序包含當日情境、長期記憶、短期歷史與玩家輸入。"""
    user_turn = build_user_turn(
        "今晚有什麼活動？",
        daily_event_summary="今日有廟會香火祭典。",
        memories=["玩家曾詢問過香爐年代。", "玩家曾參觀過前殿。"],
        short_term_turns=[
            {"role": "user", "text": "你好"},
            {"role": "assistant", "text": "你好，旅人。"},
        ],
    )

    pos_event = user_turn.find("【當日情境】")
    pos_mem = user_turn.find("【過去相關記憶】")
    pos_hist = user_turn.find("【近期對話歷史】")
    pos_input = user_turn.find("玩家：今晚有什麼活動？")

    assert pos_event != -1
    assert pos_mem != -1
    assert pos_hist != -1
    assert pos_input != -1

    assert pos_event < pos_mem < pos_hist < pos_input


def test_user_turn_graceful_missing_sections():
    """驗證缺項（冷啟動、首次對話、無當日情境）時優雅省略，無空白佔位符。"""
    # 首次對話：無當日情境、無長期記憶、無短期歷史
    user_turn = build_user_turn("這座寺廟建於何時？")
    assert user_turn == "玩家：這座寺廟建於何時？"
    assert "【當日情境】" not in user_turn
    assert "【過去相關記憶】" not in user_turn
    assert "【近期對話歷史】" not in user_turn


def test_configurable_short_term_turns():
    """驗證短期對話輪數限制可配置。"""
    turns = [{"role": "user", "text": f"Q{i}"} for i in range(10)]
    user_turn = build_user_turn("最新的問題", short_term_turns=turns, max_short_term_turns=2)

    assert "Q8" in user_turn
    assert "Q9" in user_turn
    assert "Q0" not in user_turn
