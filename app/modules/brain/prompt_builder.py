"""
B2．Prompt 組裝引擎（issue #12）。

依 SDD 第9節的順序組出 System Instruction 與 User Turn，交給 B1 生成。

    System Instruction:
      1. 人格（B3）
      2. 史實邊界規則（B5）
      3. ——安全邊界（B4）刻意**不在這裡**——

    User Turn:
      1. 當日情境摘要（B9，若今天有）
      2. 長期記憶 Top-K（B6）
      3. 短期記憶近期輪次（B7）
      4. 玩家本次輸入

## 順序是有意義的，不是排版

模型對 System Instruction 前段的內容給予較高權重。人格排在史實規則之前，是
因為「你是誰」決定了「你怎麼說」，而史實規則是對說話方式的**限制**——限制放在
被限制的對象之後才讀得懂。倒過來排，模型會先看到一串禁令，然後才知道那是給誰的。

## B4 為什麼不在 System Instruction 裡

安全檢查已經在**輸入端**過濾掉了（#11，`SafetyGate`）。在這裡重複注入一次，
只是浪費 token 並稀釋人格描述——而且會讓「安全是誰的責任」出現兩個答案。

真正的差別在於：塞進 System Instruction 是**請求模型自律**，而自律是機率性的；
輸入端過濾是**下游根本不會被呼叫**。兩者不是同一件事的兩種寫法。

## 缺項一律優雅省略

當日情境、長期記憶、短期記憶三者都可能不存在（今天沒排程／冷啟動／首次對話）。
缺的那段直接不出現，**不留空白佔位符**——一個寫著「（無）」的段落會讓模型以為
那是一個需要被解釋的狀態，而它其實只是還沒有資料。

## ⚠️ 人格卡欄位對應（0005 之後）

issue #12 寫的 `core_personality` / `speaking_style` / `factual_boundary` /
`taboo_topics` 是 #2 的 `persona_cards` JSONB 欄位名，那張表已在 `0005` 被
drop。實際對應：

| issue 寫的 | 實際欄位 |
|---|---|
| `core_personality` | `archetype` ＋ `personality_traits` ＋ `values` |
| `speaking_style` | `speech_style`（＋ `tone_override`） |
| `factual_boundary` | `imagination_license`（由 B5 疊加，見下） |
| `taboo_topics` | `taboos` |

**`imagination_license` 只出現一次。** 它由 B5 的 `factual_boundary_for_persona()`
疊進史實規則那一段，所以第 1 段不再重複列出——同一條界線寫兩次不會讓模型更遵守，
只會讓兩處日後不同步。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis_client import get_session
from app.modules.brain.historical_boundary import factual_boundary_for_persona
from app.modules.brain.loader import load_active_persona
from app.modules.brain.memory import retrieve_similar_memories

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prompt:
    """組裝結果。frozen——組好的 prompt 不該在送出前被改寫。"""

    system_instruction: str
    user_turn: str

    def as_single_text(self) -> str:
        """
        合併成單一字串。

        B1 目前的 `generate(prompt)` 只收一段文字（google-genai 的 `contents`），
        所以需要這個。兩段仍然分開保存，因為 SDK 哪天支援 system instruction
        參數時，改的是呼叫端而不是組裝邏輯。
        """
        return f"{self.system_instruction}\n\n---\n\n{self.user_turn}"


def _persona_section(persona) -> str:
    """
    第 1 段：人格。

    只列有值的欄位。空欄位跳過而不是印成「（無）」——人格卡是人工編輯的內容，
    還沒填的欄位就是還沒填，不需要讓模型知道有這麼一個空欄位存在。
    """
    lines = ["你是一個城市地標的擬人化集體意識。以下是你的角色設定。", ""]

    def add(label: str, value) -> None:
        if not value:
            return
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value if v)
            if not value:
                return
        lines.append(f"{label}：{value}")

    add("核心性格", persona.archetype)
    add("性格特質", persona.personality_traits)
    add("在意的事", persona.values)
    add("說話風格", persona.speech_style)
    add("語氣調整", persona.tone_override)
    add("你不是誰", persona.not_this_character)
    add("不談論的主題", persona.taboos)

    return "\n".join(lines)


def build_system_instruction(persona) -> str:
    """
    人格 → 史實邊界規則。順序見模組註解。

    B5 的規則已經把人格自己的 `imagination_license` 疊進去了，所以這裡不需要、
    也不應該再列一次。
    """
    return "\n\n".join(
        [
            _persona_section(persona),
            factual_boundary_for_persona(persona),
        ]
    )


def _format_turn(turn) -> str | None:
    """
    短期記憶的一輪。Redis 裡存的是 `{"role": ..., "text": ...}`。

    對格式寬鬆：那是 JSON，不是有 schema 約束的資料表，舊格式的殘留輪次不該
    讓整次對話組裝失敗。認不出來的就跳過。
    """
    if not isinstance(turn, dict):
        return None

    text = turn.get("text") or turn.get("content")
    if not text:
        return None

    speaker = "玩家" if turn.get("role") == "user" else "你"
    return f"{speaker}：{text}"


def build_user_turn(
    *,
    user_input: str,
    daily_context: str | None = None,
    long_term_memories: Sequence[str] = (),
    recent_turns: Sequence[dict] = (),
) -> str:
    """
    當日情境 → 長期記憶 → 短期記憶 → 本次輸入。

    收的是**已經取好的資料**而不是查詢參數，所以順序與缺項的行為可以完全單獨
    測試，不需要資料庫。
    """
    sections: list[str] = []

    if daily_context:
        sections.append(f"【今天的城市情境】\n{daily_context}")

    memories = [m for m in long_term_memories if m]
    if memories:
        body = "\n".join(f"- {m}" for m in memories)
        sections.append(f"【你對這位玩家的長期記憶】\n{body}")

    formatted = [t for t in (_format_turn(turn) for turn in recent_turns) if t]
    if formatted:
        sections.append("【剛才的對話】\n" + "\n".join(formatted))

    sections.append(f"【玩家現在說】\n{user_input}")

    return "\n\n".join(sections)


def build_prompt(
    db: Session,
    *,
    spirit_id: str,
    player_id,
    user_input: str,
    query_embedding: Sequence[float] | None = None,
    daily_context: str | None = None,
    top_k: int | None = None,
    recent_turns: int | None = None,
) -> Prompt | None:
    """
    完整組裝。**沒有生效人格卡時回傳 `None`**，不拋例外。

    那是 B3 `load_active_persona()` 的既有契約（封閉測試期人格長期是
    `active=False`，那是常態不是例外），這一層負責接住它。呼叫端據此走人工
    預寫台詞——那正是 CONTEXT.md 對「無合格輸入」的既定處置。

    `query_embedding` 為 None 時**跳過長期記憶檢索**。目前 repo 裡還沒有產生
    embedding 的模組（B6 只做了 schema 與檢索），所以這是實際的預設路徑，
    不是假設性的分支。
    """
    persona = load_active_persona(db, spirit_id)
    if persona is None:
        logger.info("靈魂 %s 沒有生效中的人格卡，無法組裝 prompt", spirit_id)
        return None

    k = settings.prompt_memory_top_k if top_k is None else top_k
    turns_limit = settings.prompt_recent_turns if recent_turns is None else recent_turns

    memories: list[str] = []
    if query_embedding is not None:
        rows = retrieve_similar_memories(
            db,
            player_id=player_id,
            spirit_id=spirit_id,
            query_embedding=query_embedding,
            k=k,
        )
        memories = [row.summary_text for row in rows]

    # 取最後 N 輪。`get_session` 已經依 player_id + spirit_id 分 key，所以
    # 隔離是 key 結構保證的，不是這裡再過濾一次。
    history = get_session(str(player_id), spirit_id) if turns_limit > 0 else []
    recent = history[-turns_limit:] if turns_limit > 0 else []

    return Prompt(
        system_instruction=build_system_instruction(persona),
        user_turn=build_user_turn(
            user_input=user_input,
            daily_context=daily_context,
            long_term_memories=memories,
            recent_turns=recent,
        ),
    )
