"""
B2．Prompt 組裝引擎（issue #12）。

依 SDD 第9節的順序組出 System Instruction 與 User Turn，交給 B1 生成。

    System Instruction:
      1. 人格（B3）
      2. 行政區基調（`brain.districts`，僅 active）
      3. 地標史實（`brain.landmark_souls`）
      4. 史實邊界規則（B5）
      5. ——安全邊界（B4）刻意**不在這裡**——

    User Turn:
      1. 當日情境摘要（B9，若今天有）
      2. 長期記憶 Top-K（B6）
      3. 短期記憶近期輪次（B7）
      4. 玩家本次輸入

## 順序是有意義的，不是排版

模型對 System Instruction 前段的內容給予較高權重。四段是「你是誰 → 你在什麼樣的
地方 → 你知道什麼 → 你不能怎麼講」——人格最先，然後由寬到窄收斂到具體史實，
最後才是限制：

人格排在最前，因為「你是誰」決定了「你怎麼說」。史實邊界規則排在最後，因為它是
對前兩段的**限制**——限制放在被限制的對象之後才讀得懂。倒過來排，模型會先看到
一串禁令，然後才知道那是給誰的、在管什麼。

行政區基調排在史實之前，因為它是**背景**而史實是**細節**：先知道自己在一條什麼
樣的街上，再去講那條街上發生過什麼事。反過來排，具體年代會先佔住注意力，基調
變成事後補充的形容詞。

史實與基調都在 System Instruction 而不是 User Turn：它們是**每次對話都相同的
知識**，跟當日情境、記憶那種每次不同的東西不是同一類。放 User Turn 會讓模型把
恆定的內容當成「這次特別提到的資訊」。

## 🔒 基調有審核閘，未審核的不會進來

`load_active_district()` 只回傳 `active=true` 的列。基調文字跟人格卡同級——會
被注入 prompt 的東西都要有人簽名，不因為「它只是背景描述」而放寬。

## B4 為什麼不在 System Instruction 裡

安全檢查已經在**輸入端**過濾掉了（#11，`SafetyGate`）。在這裡重複注入一次，
只是浪費 token 並稀釋人格描述——而且會讓「安全是誰的責任」出現兩個答案。

真正的差別在於：塞進 System Instruction 是**請求模型自律**，而自律是機率性的；
輸入端過濾是**下游根本不會被呼叫**。兩者不是同一件事的兩種寫法。

## 城市層還沒接進來

`brain.city_souls` 的內容目前還是 `PENDING_NARRATIVE_REVIEW` 佔位，沒有人在寫。
接進來也只是多付一段 token 換一段佔位字串，所以先不接。

它已經有審核閘（0018 一併加的），要接的時候條件跟行政區一樣：`active=true`。

## 缺項一律優雅省略

地標史實、當日情境、長期記憶、短期記憶都可能不存在（今天沒排程／冷啟動／首次對話）。
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
from app.modules.brain.loader import (
    load_active_district,
    load_active_persona,
    load_landmark_soul,
)
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


def _district_section(district) -> str | None:
    """
    第 2 段：行政區基調。角色所在的這條街是什麼樣的地方。

    🔒 呼叫端傳進來的必須已經是 `active=true` 的列（`load_active_district()`
    負責過濾）。這個函式本身不查資料庫，所以它擋不住未審核內容——真正的閘門
    在 loader，這裡只負責排版。

    基調是**形容詞不是事實**：`core_tone_descriptors` 講的是氣質，不是可以拿來
    當史料引用的東西。措辭上刻意用「這一帶給人的感覺」而不是「這一帶的歷史是」，
    免得模型把關鍵詞當成可以展開論述的史實。
    """
    if district is None:
        return None

    lines: list[str] = []
    if district.core_tone_descriptors:
        lines.append("這一帶給人的感覺：" + "、".join(district.core_tone_descriptors))
    if district.shared_values:
        lines.append("這裡的人共同在意的事：" + "、".join(district.shared_values))
    if district.macro_history_summary:
        lines.append(district.macro_history_summary)

    if not lines:
        return None

    name = district.name or "這一帶"
    return f"你屬於{name}這片區域。" + "\n\n" + "\n".join(lines)


def _format_fact(fact) -> str | None:
    """
    一條史實。`{year, event, detail, source, confidence}`。

    **`source` 與 `confidence` 不進 prompt。** 它們是給審查流程看的（哪裡查來的、
    能不能收），對角色怎麼講這件事沒有幫助——列出來反而會誘導模型講出「根據文化部
    資料……」這種像導覽員而不像地標記憶的句子。

    對格式寬鬆：JSONB 沒有 schema 約束，某一筆缺欄位不該讓整次組裝失敗。
    """
    if not isinstance(fact, dict):
        return None

    detail = fact.get("detail")
    if not detail:
        return None

    head = "，".join(x for x in (fact.get("year"), fact.get("event")) if x)
    return f"- {head}：{detail}" if head else f"- {detail}"


def _misconception_lines(items) -> list[str]:
    """
    常見誤解。**渲染方式跟史實刻意不同。**

    這一段裡的 `misconception` 欄位放的**就是那句錯的話**。跟史實用同一種列點格式
    呈現，模型沒有可靠的訊號知道要否定它——很可能就照著講了。所以每一條都寫成
    「有人以為 X／實際上 Y／可以這樣說 Z」的三段句，讓否定關係落在句子結構裡，
    而不是靠模型自己讀出來。

    `say_instead` 是重點：糾正玩家很容易講成訓話，預先給一句站得住的說法，
    比只給正確答案有用。
    """
    lines = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("misconception"):
            continue
        parts = [f"- 有人以為「{item['misconception']}」"]
        if item.get("correction"):
            parts.append(f"實際上：{item['correction']}")
        if item.get("say_instead"):
            parts.append(f"被問到時可以這樣說：{item['say_instead']}")
        lines.append("　".join(parts))
    return lines


def _landmark_section(landmark) -> str | None:
    """
    第 2 段：地標史實。決定角色「知道什麼」。

    整段注入，不做檢索——三層設定的資料量有界（一個地標十來條），RAG 是給
    `memory_embeddings` 那種會無限成長的東西用的。

    沒有任何內容時回傳 None，讓整段消失，而不是印一個空標題。
    """
    if landmark is None:
        return None

    blocks: list[str] = []

    facts = [x for x in (_format_fact(f) for f in (landmark.founding_facts or [])) if x]
    events = [x for x in (_format_fact(f) for f in (landmark.key_events or [])) if x]
    timeline = facts + events
    if timeline:
        blocks.append("你記得這些事：\n" + "\n".join(timeline))

    if landmark.cultural_significance:
        blocks.append(f"這個地方對人們的意義：{landmark.cultural_significance}")

    misconceptions = _misconception_lines(landmark.common_misconceptions)
    if misconceptions:
        blocks.append(
            "以下是常被誤傳的說法。**你不會主動提起這些錯誤說法**，"
            "但玩家提到時要溫和地把事實講清楚，不要糾正得像在指正對方：\n"
            + "\n".join(misconceptions)
        )

    if not blocks:
        return None

    name = landmark.name or "這個地方"
    return f"你是「{name}」這個地方本身累積下來的記憶。\n\n" + "\n\n".join(blocks)


def build_system_instruction(persona, landmark=None, district=None) -> str:
    """
    人格 → 地標史實 → 史實邊界規則。順序見模組註解。

    `landmark` 與 `district` 都預設 None：三層各自獨立缺席（見 loader）。少了史實
    仍然組得出可用的 prompt——角色只是不知道這座地標的往事，而不是不能講話；少了
    基調同理。

    🔒 `district` 必須是已經過 `load_active_district()` 過濾的列。這個函式不查
    資料庫，擋不住未審核內容。

    B5 的規則已經把人格自己的 `imagination_license` 疊進去了，所以這裡不需要、
    也不應該再列一次。
    """
    sections = [_persona_section(persona)]

    district_section = _district_section(district)
    if district_section:
        sections.append(district_section)

    landmark_section = _landmark_section(landmark)
    if landmark_section:
        sections.append(landmark_section)

    sections.append(factual_boundary_for_persona(persona))
    return "\n\n".join(sections)


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

    # 史實層缺席不阻擋組裝：人格過審了但研究還沒匯入，是實際會出現的狀態。
    landmark = load_landmark_soul(db, spirit_id)
    if landmark is None:
        logger.info("靈魂 %s 沒有對應的史實層，prompt 不含地標史實", spirit_id)

    # 🔒 只取審核通過的基調。未審核的一律當作不存在。
    district = load_active_district(db, spirit_id)

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
        system_instruction=build_system_instruction(persona, landmark, district),
        user_turn=build_user_turn(
            user_input=user_input,
            daily_context=daily_context,
            long_term_memories=memories,
            recent_turns=recent,
        ),
    )
