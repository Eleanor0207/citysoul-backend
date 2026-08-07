"""
B2．Prompt 組裝引擎（issue #12；SDD 第9節）。

依 SDD 第9節的順序組出 System Instruction 與 User Turn，交給 B1（GeminiClient）
生成。

    System Instruction 組成順序：
      1. 人格卡本體（B3）：archetype／speech_style／personality_traits／values／
         taboos／not_this_character／quest_themes／tone_override
      2. 史實邊界規則（B5）：通則 + 人格卡個別史實立場疊加後的結果
      3. 角色安全邊界（B4）：不注入——安全檢查在輸入端先過濾（issue #11），
         這裡重複塞一次只會多花 token、稀釋人格描述，不會讓角色更遵守規則

    User Turn 組成順序（缺項優雅省略，不拋例外、不留空白佔位符）：
      1. 當日情境摘要（B9 產出，若今天有）
      2. 長期記憶 Top-K（B6）
      3. 短期記憶近期輪次（B7）
      4. 玩家本次輸入

## 為什麼人格卡本體不含 `imagination_license`

跟 issue #19（B5）採同一個決定：`imagination_license` 是這個角色對「什麼算
史實、什麼是想像」的個別立場，語意上屬於「史實邊界」這件事，所以只在第 2
順位跟 B5 的通則疊加後出現一次，不在第 1 順位的人格卡本體裡重複列出。細節
與理由見 `historical_boundary.py` 模組開頭的說明。

## 為什麼 city_souls／landmark_souls 沒有出現在這裡

三層 schema（`Replace persona_cards with the three brain layers`）把史實資料
（`LandmarkSoul.founding_facts` 等）跟語氣/人格資料（`CharacterPersona`）分開
存放，但 SDD 對 B2 的定義（「人格卡 + 短期記憶 + 安全邊界 → Vertex AI」）與
issue #12 的驗收標準都只涵蓋人格卡本體，不包含另外組裝 landmark／city 層的
史實資料。這兩張表目前沒有被任何模組讀取——不在這張票的範圍內，之後真的要
把「地標知道什麼史實」餵進 prompt 時，需要另開票，不要在這裡先斬後奏。

## 為什麼 `query_embedding`／`daily_context` 是呼叫端傳入的參數

「文字輸入怎麼變成向量」（embedding 模型）跟「今天的情境摘要文字」（B9，
issue #20）都還沒落地或還沒定案（SDD 第10節）。這支模組的職責只是「這些
東西存在的話要放在哪個位置、用什麼順序」，不是「怎麼生出這些東西」——跟
B6 `memory.py` 模組開頭「文字查詢→embedding→Top-K 檢索的完整串接會在更
上層決定模型並組裝」的說法一致，這裡就是那個「更上層」，但決定模型本身
仍然不屬於這裡：呼叫端（未來的對話端點）算好向量再傳進來。
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.redis_client import get_session
from app.modules.brain.historical_boundary import combine_with_persona_boundary
from app.modules.brain.loader import load_active_persona
from app.modules.brain.memory import retrieve_similar_memories
from app.modules.brain.models import CharacterPersona

# 待定案數字（SDD 第10節）：Top-K 與短期記憶輪數的最終值待實測調整，這裡先用
# 文件建議預設值。數字只在這裡出現一次，組裝邏輯本身不寫死字面量（issue #12
# AC4：換模型或調參數時只改這裡，不用去 grep 邏輯本體裡散落的 3／6）。
DEFAULT_TOP_K = 3
DEFAULT_SHORT_TERM_TURNS = 6


@dataclass(frozen=True)
class AssembledPrompt:
    system_instruction: str
    user_turn: str


def build_system_instruction(persona: CharacterPersona, historical_boundary: str) -> str:
    """
    純函式：人格卡本體 + 已疊加好的史實邊界文字。不碰資料庫，`historical_boundary`
    由呼叫端算好傳入（通常是 `combine_with_persona_boundary(persona.imagination_license)`
    的結果）——拆成兩個參數而不是這支函式自己去疊加，是為了讓「人格卡怎麼格式化」
    跟「史實邊界怎麼疊加」（issue #19 的職責）保持獨立，各自能單獨測試。

    人格卡區塊永遠在史實邊界文字**之前**（issue #12 AC1）：組裝順序影響模型的
    權重感知，不是隨意排列，對調這兩段的測試必須變紅。
    """
    return f"{_format_persona(persona)}\n\n{historical_boundary}"


def _format_persona(persona: CharacterPersona) -> str:
    """
    把人格卡的各欄位格式化成一段文字。陣列欄位（`personality_traits` 等）在
    schema 裡是 nullable——人格草稿可以先只填必填欄位，這裡對每個欄位都做
    「沒有就跳過該行」，不印出空白的「性格特質：」這種沒有內容的標籤行。
    """
    lines = [f"角色原型：{persona.archetype}", f"說話風格：{persona.speech_style}"]
    lines += _list_line("性格特質", persona.personality_traits)
    lines += _list_line("核心價值", persona.values)
    lines += _list_line("禁忌話題（不可觸碰）", persona.taboos)
    if persona.not_this_character:
        lines.append(f"不是誰：{persona.not_this_character}")
    lines += _list_line("任務主題", persona.quest_themes)
    if persona.tone_override:
        lines.append(f"語氣調整：{persona.tone_override}")
    return "\n".join(lines)


def _list_line(label: str, values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    return [f"{label}：{'、'.join(values)}"]


def build_user_turn(
    *,
    daily_context: str | None,
    long_term_memory_summaries: Sequence[str],
    short_term_turns: Sequence[dict],
    player_input: str,
) -> str:
    """
    純函式：四個區塊依指定順序組裝，缺的那段優雅省略（issue #12 AC3）——
    `daily_context` 為 None、`long_term_memory_summaries`／`short_term_turns`
    為空，都只是該區塊不出現，不拋例外、不留下空白佔位符。玩家本次輸入永遠
    是最後一段，且永遠存在（呼叫端保證非空，同 `DialogueRequest` 的驗證）。
    """
    sections: list[str] = []

    if daily_context:
        sections.append(f"【今日情境】\n{daily_context}")

    if long_term_memory_summaries:
        joined = "\n".join(f"- {summary}" for summary in long_term_memory_summaries)
        sections.append(f"【與這位玩家的過去記憶】\n{joined}")

    if short_term_turns:
        joined = "\n".join(
            f"{turn.get('role', '?')}：{turn.get('text', '')}" for turn in short_term_turns
        )
        sections.append(f"【近期對話】\n{joined}")

    sections.append(f"【玩家本次輸入】\n{player_input}")

    return "\n\n".join(sections)


def assemble_dialogue_prompt(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    player_input: str,
    daily_context: str | None = None,
    query_embedding: Sequence[float] | None = None,
    top_k: int = DEFAULT_TOP_K,
    short_term_turn_limit: int = DEFAULT_SHORT_TERM_TURNS,
) -> AssembledPrompt | None:
    """
    真正接資料庫／Redis／B6 的組裝入口。

    沒有生效人格卡時回傳 `None`（issue #12 AC5）——B3 `load_active_persona`
    回傳 `None` 是既有契約，這裡原樣接住並往上傳遞，呼叫端（未來的對話端點）
    要回退到既有的人工預寫 fallback 台詞，不是讓例外一路往外拋到 HTTP 層。

    `query_embedding` 為 `None` 時整段跳過長期記憶檢索，不呼叫 B6——這涵蓋
    「embedding 服務暫時不可用或還沒接上」的情況；「玩家還沒有任何長期記憶」
    （AC3 講的冷啟動）是不同的情況，那種狀況下 `query_embedding` 通常還是有
    值，只是 B6 的檢索結果本來就是空 list，兩者都會讓長期記憶那段優雅省略，
    但原因不同。
    """
    persona = load_active_persona(db, spirit_id)
    if persona is None:
        return None

    historical_boundary = combine_with_persona_boundary(persona.imagination_license)
    system_instruction = build_system_instruction(persona, historical_boundary)

    long_term_summaries: list[str] = []
    if query_embedding is not None:
        memories = retrieve_similar_memories(
            db,
            player_id=player_id,
            spirit_id=spirit_id,
            query_embedding=query_embedding,
            k=top_k,
        )
        long_term_summaries = [memory.summary_text for memory in memories]

    short_term_turns = get_session(str(player_id), spirit_id)
    short_term_turns = short_term_turns[-short_term_turn_limit:] if short_term_turn_limit > 0 else []

    user_turn = build_user_turn(
        daily_context=daily_context,
        long_term_memory_summaries=long_term_summaries,
        short_term_turns=short_term_turns,
        player_input=player_input,
    )

    return AssembledPrompt(system_instruction=system_instruction, user_turn=user_turn)
