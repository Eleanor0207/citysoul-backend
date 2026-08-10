"""
B2．Prompt 組裝引擎（SDD 第9節）。

組裝 System Instruction 與 User Turn 交給 B1 (Gemini) 生成：

System Instruction 順序：
  1. 人格卡 (B3)：core_personality, speaking_style, factual_boundary, taboo_topics
  2. 史實邊界規則 (B5)：注入不對敏感歷史武斷定論等通則
  3. (B4 安全邊界在輸入端處理，刻意不重複塞進 System Instruction)

User Turn 順序：
  1. 當日情境摘要 (B9)
  2. 長期記憶 Top-K (B6)
  3. 短期對話歷史 (B7)
  4. 玩家本次輸入
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from sqlalchemy.orm import Session

from app.core.redis_client import get_session
from app.modules.brain.loader import load_active_persona
from app.modules.brain.memory import retrieve_similar_memories
from app.modules.brain.models import CharacterPersona

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 3
DEFAULT_MAX_SHORT_TERM_TURNS = 6

# B5 史實與立場規則通則文字
HISTORICAL_BOUNDARY_RULE = (
    "【史實與立場規則】\n"
    "不對敏感歷史、政治或宗教教義議題做武斷定論。保持對歷史脈絡的尊重與客觀，引導玩家體驗故事與場域氛圍。"
)

# 無生效人格卡時的預設系統指示
DEFAULT_SYSTEM_INSTRUCTION = (
    "【角色設定】\n"
    "你是城市的守護靈魂，語氣親切、沉穩且富含故事感。\n\n"
    f"{HISTORICAL_BOUNDARY_RULE}"
)


def build_system_instruction(persona: CharacterPersona | None) -> str:
    """
    依 SDD 第9節順序組裝 System Instruction：
    1. 人格卡 (B3)
    2. 史實邊界規則 (B5)
    (刻意不包含 B4 安全檢查規則，避免重複 token 與干擾角色感)
    """
    if persona is None:
        return DEFAULT_SYSTEM_INSTRUCTION

    sections = []

    # 1. 核心性格與立場
    core_parts = []
    if persona.archetype:
        core_parts.append(f"角色原型：{persona.archetype}")
    if persona.personality_traits:
        core_parts.append(f"性格特質：{', '.join(persona.personality_traits)}")
    if persona.values:
        core_parts.append(f"核心價值：{', '.join(persona.values)}")
    if core_parts:
        sections.append("【核心性格與立場】\n" + "\n".join(core_parts))

    # 2. 說話風格與語調
    style_parts = []
    if persona.speech_style:
        style_parts.append(f"說話風格：{persona.speech_style}")
    if persona.tone_override:
        style_parts.append(f"語調調整：{persona.tone_override}")
    if style_parts:
        sections.append("【說話風格】\n" + "\n".join(style_parts))

    # 3. 史實與授權邊界
    boundary_parts = []
    if persona.imagination_license:
        boundary_parts.append(f"虛構授權：{persona.imagination_license}")
    if persona.not_this_character:
        boundary_parts.append(f"角色邊界：{persona.not_this_character}")
    if boundary_parts:
        sections.append("【事實與想像邊界】\n" + "\n".join(boundary_parts))

    # 4. 禁忌話題
    if persona.taboos:
        sections.append("【禁忌話題與限制】\n" + f"切勿涉入或發言：{', '.join(persona.taboos)}")

    # 5. 史實邊界規則 (B5)
    sections.append(HISTORICAL_BOUNDARY_RULE)

    return "\n\n".join(sections)


def build_user_turn(
    user_input: str,
    *,
    daily_event_summary: str | None = None,
    memories: Sequence[str] | None = None,
    short_term_turns: Sequence[dict[str, Any]] | None = None,
    max_short_term_turns: int = DEFAULT_MAX_SHORT_TERM_TURNS,
) -> str:
    """
    依 SDD 第9節順序組裝 User Turn：
    1. 當日情境摘要 (B9)
    2. 長期記憶 Top-K (B6)
    3. 短期對話歷史 (B7)
    4. 玩家本次輸入
    """
    parts = []

    # 1. 當日情境
    if daily_event_summary and daily_event_summary.strip():
        parts.append(f"【當日情境】\n{daily_event_summary.strip()}")

    # 2. 長期記憶 Top-K
    if memories:
        valid_mems = [m.strip() for m in memories if m and m.strip()]
        if valid_mems:
            mem_text = "\n".join(f"- {m}" for m in valid_mems)
            parts.append(f"【過去相關記憶】\n{mem_text}")

    # 3. 短期對話歷史
    if short_term_turns:
        recent_turns = list(short_term_turns)[-max_short_term_turns:]
        history_lines = []
        for turn in recent_turns:
            role = turn.get("role", "")
            text = turn.get("text", "")
            if role == "user":
                history_lines.append(f"玩家：{text}")
            elif role in ("assistant", "spirit"):
                history_lines.append(f"靈魂：{text}")
        if history_lines:
            parts.append("【近期對話歷史】\n" + "\n".join(history_lines))

    # 4. 玩家本次輸入
    parts.append(f"玩家：{user_input.strip()}")

    return "\n\n".join(parts)


def assemble_prompt(
    db: Session,
    *,
    player_id: str,
    spirit_id: str,
    user_input: str,
    daily_event_summary: str | None = None,
    query_embedding: Sequence[float] | None = None,
    top_k: int = DEFAULT_TOP_K,
    max_short_term_turns: int = DEFAULT_MAX_SHORT_TERM_TURNS,
) -> tuple[str, str]:
    """
    一站式 Prompt 組裝接口，回傳 (system_instruction, user_turn)。
    """
    persona = load_active_persona(db, spirit_id)
    system_instruction = build_system_instruction(persona)

    # 取長期記憶 (B6)
    memories = []
    if query_embedding is not None:
        try:
            mem_rows = retrieve_similar_memories(
                db,
                player_id=player_id,
                spirit_id=spirit_id,
                query_embedding=query_embedding,
                k=top_k,
            )
            memories = [m.summary_text for m in mem_rows]
        except Exception as err:
            logger.warning("檢索長期記憶失敗，進行無記憶降級: %s", err)
            memories = []

    # 取短期記憶 (B7)
    short_term_turns = []
    try:
        short_term_turns = get_session(str(player_id), spirit_id)
    except Exception as err:
        logger.warning("讀取短期記憶失敗，進行無歷史對話降級: %s", err)
        short_term_turns = []

    user_turn = build_user_turn(
        user_input,
        daily_event_summary=daily_event_summary,
        memories=memories,
        short_term_turns=short_term_turns,
        max_short_term_turns=max_short_term_turns,
    )

    return system_instruction, user_turn
