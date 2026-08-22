"""劇情台詞：把一個 beat 的節點解析成玩家真的讀得到的文字（backend#72）。

## 這一層先前完全不存在

`narrative_directive` 裡存著整包 `nodes`（台詞、觀察點選項），`brain.story_strings`
裡存著那些節點指向的實際文字——**但沒有任何端點吐得出來**。
`GET /story-arcs/{arcId}/state` 只回 beat id 清單，客戶端拿到 `beat_prologue_letter`
這個字串之後就沒有下一步了。

結果是：資料庫裡有完整的序章，玩家一個字都看不到。

## 台詞不經 LLM

`story_strings` 跟 `canned_greetings.response_text` 同一類：**人工撰寫、原樣顯示**。
這個模組只做「查表、組裝」，不碰任何腦袋模組。B11 生成的是共鳴值解鎖片段
（`resonance_unlockables`），那是另一條線。

## 查不到的 text_key 就跳過那個節點

缺字串是內容錯誤，但不該讓整段劇情打不開——跟 TTS 失敗時「文字照常顯示」
同一個原則（SDD §18.7.3：包裝失敗不該看起來像出錯）。跳過的節點會留在 log 上。

⚠️ 但**選項**不一樣：選項少一個會讓玩家看到一組不完整的選擇，而他無從得知
少了什麼。所以一組選項裡只要有任何一個 option 解析不出文字，整組選項跳過。

## `jump` 與 `target` 刻意不輸出

arc 文件的節點裡有 `target: prologue_person_reply` 這類指向，但**那些節點從來
沒有被寫出來**（見文件附錄 B：§5.1／§6.1 的「初次對話」只是敘事構想，沒有進
`beats:` 資料）。把它們原樣送給客戶端，等於邀請客戶端去跳一個不存在的節點。

所以這裡只輸出「有內容的東西」：台詞與選項。等那些節點真的被寫出來，再一起
加回分支能力——那是內容決定，不是這一層可以補的。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.modules.body.models import Spirit
from app.modules.brain.models import StoryBeat, StoryInfoCard, StoryString

logger = logging.getLogger(__name__)


@dataclass
class ScriptOption:
    """一個觀察點／選項。"""

    option_id: str
    # 玩家**選之前**看到的字（`[可看的位置]` 那一行）。
    text: str
    # 選之後年代簿說的話。None 代表這個選項沒有回應——舊資料只有一段文字時
    # 會落到這裡，見 build_script()。
    reply: str | None = None
    # 選這個會寫進哪個劇情變數，例如 `{"story_focus": "person"}`。
    #
    # ⚠️ 目前**沒有任何地方記錄玩家選了什麼**——那是另一件事（story_focus /
    # reveal_lens / ending_mark 還沒有欄位）。這裡先把它送出去，讓客戶端至少
    # 能在本機記住玩家的選擇，也讓之後接上寫入時不必再改一次契約。
    sets: dict = field(default_factory=dict)


@dataclass
class ScriptNode:
    """一個劇情節點。`type` 是 'line' 或 'choice'。"""

    type: str
    speaker: str | None = None
    text: str | None = None
    options: list[ScriptOption] = field(default_factory=list)
    # 看過幾個選項才能繼續。文件 §2.3 拍板「不強制看完三個」。
    min_viewed_to_proceed: int = 1
    # False 代表可以多選、已看過的仍可回看。
    exclusive: bool = False


@dataclass
class InfoCard:
    """一張資訊卡。史實與虛構分開，不合併成一段。"""

    card_id: str
    historical_text: str | None = None
    fiction_text: str | None = None


@dataclass
class BeatScript:
    beat_id: str
    character_id: str | None
    # 哪一隻靈魂在說話。`character_id` 是腦袋那邊的身分，客戶端要的是召喚點。
    spirit_id: str | None
    nodes: list[ScriptNode] = field(default_factory=list)
    # `show_info_card` 指令列出的卡片，含實際文字（0030 之後卡片定義才進資料庫）。
    #
    # 查不到定義的 card_id 會被跳過並留在 log 上——同缺字串的處理原則。
    info_cards: list[InfoCard] = field(default_factory=list)


def _directive(beat: StoryBeat) -> dict:
    raw = beat.narrative_directive
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # 舊資料的 narrative_directive 可能是純文字的敘事指令，不是 JSON。
        # 那種 beat 沒有可播放的腳本，不是錯誤。
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strings(db: Session, keys: set[str]) -> dict[str, str]:
    if not keys:
        return {}
    rows = (
        db.query(StoryString.text_key, StoryString.text)
        .filter(StoryString.text_key.in_(keys), StoryString.active.is_(True))
        .all()
    )
    return {key: text for key, text in rows}


def _collect_keys(nodes: list) -> set[str]:
    keys: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("text_key"):
            keys.add(node["text_key"])
        for value in (node.get("cases") or {}).values():
            if isinstance(value, str):
                keys.add(value)
        for option in node.get("options") or []:
            if not isinstance(option, dict):
                continue
            for field in ("text_key", "label_text_key"):
                if option.get(field):
                    keys.add(option[field])
    return keys


def build_script(
    db: Session, beat: StoryBeat, variables: dict | None = None
) -> BeatScript:
    """把一個 beat 解析成可播放的腳本。

    `variables` 是這個玩家在這條 arc 上已經定下來的劇情變數，用來挑
    `conditional_line` 要說哪一句。省略時那種節點一律跳過——純解析內容的
    呼叫端（例如測試與匯入驗證）不必先有一個玩家。
    """
    variables = variables or {}
    directive = _directive(beat)
    raw_nodes = [n for n in (directive.get("nodes") or []) if isinstance(n, dict)]
    texts = _strings(db, _collect_keys(raw_nodes))

    nodes: list[ScriptNode] = []
    for raw in raw_nodes:
        node_type = raw.get("type")

        if node_type == "line":
            text = texts.get(raw.get("text_key") or "")
            if text is None:
                logger.warning(
                    "beat %s: line 的 text_key %r 在 story_strings 裡查不到",
                    beat.beat_id, raw.get("text_key"),
                )
                continue
            nodes.append(
                ScriptNode(type="line", speaker=raw.get("speaker"), text=text)
            )
            continue

        # `look_points` 是文件的用語，對客戶端來說就是一組選項。統一叫 choice，
        # 免得客戶端要為同一種互動寫兩套。
        if node_type in {"look_points", "choice"}:
            options: list[ScriptOption] = []
            incomplete = False
            for raw_option in raw.get("options") or []:
                if not isinstance(raw_option, dict):
                    incomplete = True
                    continue
                # ⚠️ 標籤與回應是兩件事：label_text_key 是玩家**選之前**看到的
                # 字，text_key 是**選之後**的敘述。只送 text_key 的話，客戶端會
                # 把敘述當成選項畫出來——三個答案在選擇之前就全部攤開，「選哪
                # 一個」這個動作完全失去意義。
                #
                # 沒有 label_text_key 的舊資料退回單段文字（text 是它，沒有
                # 回應），這樣既有的 beat 不會因為這次擴充而消失。
                label_key = raw_option.get("label_text_key")
                reply_key = raw_option.get("text_key")

                if label_key:
                    label = texts.get(label_key)
                    reply = texts.get(reply_key or "")
                    if label is None or (reply_key and reply is None):
                        incomplete = True
                        break
                else:
                    label = texts.get(reply_key or "")
                    reply = None
                    if label is None:
                        incomplete = True
                        break

                options.append(
                    ScriptOption(
                        option_id=raw_option.get("id") or "",
                        text=label,
                        reply=reply,
                        sets=dict(raw_option.get("set_once") or {}),
                    )
                )

            if incomplete or not options:
                # 半組選項比沒有選項更糟：玩家看不出少了什麼。
                logger.warning(
                    "beat %s: 一組選項有 text_key 查不到，整組跳過", beat.beat_id
                )
                continue

            nodes.append(
                ScriptNode(
                    type="choice",
                    options=options,
                    min_viewed_to_proceed=raw.get("min_viewed_to_proceed", 1),
                    exclusive=bool(raw.get("exclusive", False)),
                )
            )
            continue

        if node_type == "conditional_line":
            # 依玩家的劇情變數挑一句。變數還沒有值（例如序章一處都沒看就
            # 往下走）時**整個節點跳過**——不硬選一句，也不給空白台詞。
            variable = raw.get("variable")
            chosen = variables.get(variable)
            text_key = (raw.get("cases") or {}).get(chosen)
            if not text_key:
                continue
            text = texts.get(text_key)
            if text is None:
                logger.warning(
                    "beat %s: conditional_line 的 text_key %r 查不到",
                    beat.beat_id, text_key,
                )
                continue
            nodes.append(
                ScriptNode(type="line", speaker=raw.get("speaker"), text=text)
            )
            continue

        # `jump` 與其他未知型別刻意不輸出——見模組說明。

    info_cards = _resolve_info_cards(db, directive)

    spirit_id = None
    if beat.character_id:
        row = (
            db.query(Spirit.spirit_id)
            .filter(Spirit.character_id == beat.character_id)
            .first()
        )
        spirit_id = row[0] if row else None

    return BeatScript(
        beat_id=beat.beat_id,
        character_id=beat.character_id,
        spirit_id=spirit_id,
        nodes=nodes,
        info_cards=info_cards,
    )


def _resolve_info_cards(db: Session, directive: dict) -> list[InfoCard]:
    """把 `show_info_card` 指令換成卡片內容。

    ⚠️ 史實與虛構**分成兩個欄位送出去**，不在後端合併。文件 §1 要求每張卡明確
    區分「史實可考」與「本作故事」——合成一段之後那條界線就只剩排版慣例，而
    它是這個專案對地標的基本承諾之一。
    """
    card_ids = [
        command["show_info_card"]
        for command in (directive.get("commands") or [])
        if isinstance(command, dict) and command.get("show_info_card")
    ]
    if not card_ids:
        return []

    rows = (
        db.query(StoryInfoCard)
        .filter(StoryInfoCard.card_id.in_(card_ids), StoryInfoCard.active.is_(True))
        .all()
    )
    by_id = {row.card_id: row for row in rows}

    keys = {
        key
        for row in rows
        for key in (row.historical_text_key, row.fiction_text_key)
        if key
    }
    texts = _strings(db, keys)

    cards: list[InfoCard] = []
    for card_id in card_ids:
        row = by_id.get(card_id)
        if row is None:
            logger.warning("show_info_card 指向查不到的卡片：%s", card_id)
            continue
        cards.append(
            InfoCard(
                card_id=card_id,
                historical_text=texts.get(row.historical_text_key or ""),
                fiction_text=texts.get(row.fiction_text_key or ""),
            )
        )
    return cards
