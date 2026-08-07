"""
B2．Prompt 組裝引擎（#12，SDD 第9節）。

依固定順序組出 System Instruction 與 User Turn，交給 B1（`gemini.GeminiClient
.generate`）生成。這裡只負責「組」：

- 不呼叫 B1——組裝完成後回傳給呼叫端（未來的 dialogue 端點，#42／#45）自己
  決定何時生成。
- 不做安全過濾。B4（#11）在輸入端先過濾，安全規則**不重複塞進** System
  Instruction，重複注入只會多花 token 又稀釋人格描述。

當日情境摘要（B9，#20）與 embedding 模型（SDD 第10節「待定案」）都還沒有
拍板/落地，所以分別用「呼叫端傳入 `daily_narrative`」與「注入
`EmbeddingClient` 抽象」接住，不在這支模組裡假裝它們已經存在。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.redis_client import get_session
from app.modules.brain.embeddings import EmbeddingClient
from app.modules.brain.historical_boundary import historical_boundary_rule_text
from app.modules.brain.loader import load_active_persona
from app.modules.brain.memory import retrieve_similar_memories
from app.modules.brain.models import CharacterPersona

# SDD 第10節「待定案數字」：本票先用文件建議預設值，做成可設定的參數
# （見 `build_prompt` 的 `top_k`／`short_term_turns`），不寫死在組裝邏輯裡。
DEFAULT_TOP_K = 3
DEFAULT_SHORT_TERM_TURNS = 6

_DAILY_NARRATIVE_HEADER = "【今日情境】"
_LONG_TERM_MEMORY_HEADER = "【長期記憶】"
_SHORT_TERM_HISTORY_HEADER = "【近期對話】"
_CURRENT_INPUT_HEADER = "【玩家本次輸入】"


@dataclass
class PromptResult:
    """
    B1 需要的兩段輸入。刻意不在這裡先接成一份字串——用
    `GenerateContentConfig.system_instruction` 傳，還是手動接在 `contents`
    前面，是呼叫端（B1 整合）的決定，不是組裝引擎該替它拍板的事。
    """

    system_instruction: str
    user_turn: str


def build_prompt(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    user_input: str,
    embedding_client: EmbeddingClient,
    daily_narrative: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    short_term_turns: int = DEFAULT_SHORT_TERM_TURNS,
) -> PromptResult | None:
    """
    組出這一次對話要送給 B1 的完整輸入。

    找不到生效人格卡時回傳 `None`——呼叫端要接住這個結果並走人工預寫的
    fallback，不能假設一定組得出東西（B3 的 `load_active_persona` 回
    `None` 本來就是既有契約，這裡只是原樣往上傳）。
    """
    persona = load_active_persona(db, spirit_id)
    if persona is None:
        return None

    return PromptResult(
        system_instruction=_build_system_instruction(persona),
        user_turn=_build_user_turn(
            db,
            player_id=player_id,
            spirit_id=spirit_id,
            user_input=user_input,
            embedding_client=embedding_client,
            daily_narrative=daily_narrative,
            top_k=top_k,
            short_term_turns=short_term_turns,
        ),
    )


def _build_system_instruction(persona: CharacterPersona) -> str:
    # 順序固定：人格卡在前、B5 史實邊界規則在後。B4 安全邊界刻意不出現在
    # 這裡——已經在輸入端過濾（#11），重複注入只是浪費 token。
    return "\n\n".join([_render_persona(persona), historical_boundary_rule_text()])


def _render_persona(persona: CharacterPersona) -> str:
    lines = [f"你是{persona.archetype}。{persona.speech_style}"]

    if persona.personality_traits:
        lines.append("性格特質：" + "、".join(persona.personality_traits))
    if persona.values:
        lines.append("重視的價值：" + "、".join(persona.values))
    if persona.not_this_character:
        lines.append(persona.not_this_character)
    if persona.imagination_license:
        lines.append(persona.imagination_license)
    if persona.tone_override:
        lines.append(persona.tone_override)
    if persona.taboos:
        # 安全下限：taboos 只能往上加，不能移除（見 models.py 的欄位註解）。
        lines.append("絕對不要：" + "、".join(persona.taboos))

    return "\n".join(lines)


def _build_user_turn(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    user_input: str,
    embedding_client: EmbeddingClient,
    daily_narrative: str | None,
    top_k: int,
    short_term_turns: int,
) -> str:
    sections: list[str] = []

    # 1. 當日情境摘要（B9，#20，若今天有）。B9 還沒落地，這裡只接住呼叫端
    #    傳進來的值；缺項（None 或空字串）優雅省略，不留空白佔位符。
    if daily_narrative:
        sections.append(f"{_DAILY_NARRATIVE_HEADER}\n{daily_narrative}")

    # 2. 長期記憶 Top-K（B6）。查詢向量在這一層才算出來——memory.py 的模組
    #    註解說得清楚：「文字查詢→embedding→Top-K 檢索」的完整串接是更上層
    #    （也就是這裡）的事，B6 本身只處理已經算好的向量。
    query_embedding = embedding_client.embed(user_input)
    memories = retrieve_similar_memories(
        db,
        player_id=player_id,
        spirit_id=spirit_id,
        query_embedding=query_embedding,
        k=top_k,
    )
    if memories:
        memory_lines = "\n".join(f"- {memory.summary_text}" for memory in memories)
        sections.append(f"{_LONG_TERM_MEMORY_HEADER}\n{memory_lines}")

    # 3. 短期記憶近期輪次（B7）。只取最後 short_term_turns 輪，不是全部歷史。
    turns = get_session(str(player_id), spirit_id)
    if turns:
        recent_turns = turns[-short_term_turns:] if short_term_turns > 0 else []
        if recent_turns:
            turn_lines = "\n".join(
                f"{turn.get('role', '')}：{turn.get('text', '')}" for turn in recent_turns
            )
            sections.append(f"{_SHORT_TERM_HISTORY_HEADER}\n{turn_lines}")

    # 4. 玩家本次輸入——永遠存在，不受任何缺項規則影響。
    sections.append(f"{_CURRENT_INPUT_HEADER}\n{user_input}")

    return "\n\n".join(sections)
