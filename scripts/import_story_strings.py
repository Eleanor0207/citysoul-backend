"""Import reviewed player-facing story strings from the Wanhua story source.

The source document keeps the text-key references in its story YAML and the
actual prose in the reviewed script sections.  All source parsing and the
two-way reference check happen before a database engine or transaction is
opened.  The database write is one transaction containing the string UPSERTs
and the Wanhua arc intro backfill.

Usage::

    python -m scripts.import_story_strings
    python -m scripts.import_story_strings --dry-run
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any

from sqlalchemy import create_engine, text

from app.core.config import settings
from scripts.import_story_arcs import load_story_document


# 劇本正本在隔壁的 citysoul-doc；`content/story/` 是給容器讀的副本。
#
# ⚠️ **優先讀正本，讀不到才用副本。** 反過來的話，Lead 改了 doc、忘了同步，
# 本機匯入會安靜地匯進舊內容——而那種錯誤沒有症狀，只是劇情停在上一版。
#
# 容器裡沒有隔壁 repo（Cloud Run 的 Job 用 backend 的映像檔），所以那邊一定
# 落到副本。兩份的一致性由 tests/test_story_source_sync.py 擋。
_STORY_FILENAME = "wanhua_district_storyline_aming_landmark_photo_v1.md"
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_STORY_SOURCE = _REPO_ROOT.parent / "citysoul-doc" / "story" / _STORY_FILENAME
_STORY_VENDORED = _REPO_ROOT / "content" / "story" / _STORY_FILENAME

DEFAULT_STORY_FILE = _STORY_SOURCE if _STORY_SOURCE.is_file() else _STORY_VENDORED

_PROLOGUE_LOOKS = {
    "她的側影": "wanhua.prologue.look.figure",
    "信紙的摺痕": "wanhua.prologue.look.crease",
    "畫面一角": "wanhua.prologue.look.corner",
}
# 選項**標籤**（玩家選之前看到的字），來自 §11.1 的 `[可看的位置]` 區塊。
#
# ⚠️ 標籤與敘述是兩件事：`wanhua.prologue.look.figure` 是看了之後年代簿說的話，
# `.label` 才是清單上那一行。先前只抽敘述，客戶端因此把答案當成選項畫出來——
# 三處的內容在玩家選擇之前就全部攤開，「看向哪裡」這個動作失去意義。
_PROLOGUE_LOOK_LABELS = {
    "她的側影": "wanhua.prologue.look.figure.label",
    "信紙的摺痕": "wanhua.prologue.look.crease.label",
    "畫面一角": "wanhua.prologue.look.corner.label",
}

# 終章三個結局標記。左邊是玩家選項的字面，右邊是 (標籤 key, 回應 key)。
_FINALE_MARKS = {
    "給那條街。": (
        "wanhua.finale.mark.street.label",
        "wanhua.finale.mark.street",
    ),
    "給那些沒有留下名字的人。": (
        "wanhua.finale.mark.people.label",
        "wanhua.finale.mark.people",
    ),
    "給還在找回家路的人。": (
        "wanhua.finale.mark.home.label",
        "wanhua.finale.mark.home",
    ),
}

# §11.2／§11.3／§11.4 三章的設定。
#
# 三章結構相同：一段 ```text 開場、一組 `[玩家選項]` 標籤、一張「選項｜回應」
# 表格。所以抽取用通用函式，這裡只放各章不一樣的部分。
#
# `options` 依**表格出現的順序**對應 id，跟 §12 的 options 順序一致。順序錯開
# 的話台詞會接到別的選項上，而且不會有任何錯誤——只有玩家會發現靈魂答非所問。
_CHAPTERS = (
    {
        "section": ("### 11.2", "### 11.3"),
        "prefix": "wanhua.longshan",
        "speaker": "龍山寺",
        "gate_options": ("ask_letter", "ask_person", "ask_place"),
        # 線索對話：開場、依 story_focus 的補句、收尾。
        "clue_conditions": (
            ("story_focus=person", "clue.focus.person"),
            ("story_focus=history", "clue.focus.history"),
            ("story_focus=home", "clue.focus.home"),
        ),
    },
    {
        "section": ("### 11.3", "### 11.4"),
        "prefix": "wanhua.redhouse",
        "speaker": "紅樓",
        "gate_options": ("ask_why", "ask_where", "ask_past"),
        # 中間那段不是紅樓說的，是畫背面的殘字。
        "clue_fragment_speaker": "畫背殘句",
    },
    {
        "section": ("### 11.4", "### 11.5"),
        "prefix": "wanhua.bopiliao",
        "speaker": "剝皮寮",
        "gate_options": ("ask_portrait", "ask_return", "ask_street"),
        # 揭露對話自己也有一組選項，寫入 reveal_lens。
        "reveal_options": ("lens_place", "lens_return", "lens_memory"),
    },
)

_CARD_SECTIONS = {
    "4": ("wanhua.card.longshan.historical", "wanhua.card.longshan.fiction"),
    "5": ("wanhua.card.redhouse.historical", "wanhua.card.redhouse.fiction"),
    "6": ("wanhua.card.bopiliao.historical", "wanhua.card.bopiliao.fiction"),
}


class StoryStringImportError(ValueError):
    """The complete story-string batch is invalid and must not be written."""


def _section(raw: str, heading: str, next_heading: str) -> str:
    match = re.search(
        rf"(?ms)^{re.escape(heading)}.*?\n(.*?)(?=^{re.escape(next_heading)}|\Z)",
        raw,
    )
    if not match:
        raise StoryStringImportError(
            f"story source is missing section {heading!r} before {next_heading!r}"
        )
    return match.group(1)


def _extract_prologue_open(raw: str) -> tuple[str, str]:
    section = _section(raw, "### 11.1", "### 11.2")
    match = re.search(r"(?ms)^\[系統／年代簿\]\s*\n(?P<text>[^\r\n]+)", section)
    if not match:
        raise StoryStringImportError(
            "story source is missing the §11.1 [系統／年代簿] opening line"
        )
    title_match = re.search(r"^### 11\.1\s+序章：(?P<title>.+?)\s*$", raw, re.MULTILINE)
    if not title_match:
        raise StoryStringImportError("story source is missing the §11.1 intro title")
    return match.group("text").strip(), title_match.group("title").strip()


def _extract_prologue_letter_body(raw: str) -> str:
    """The letter's own words — the `[信]` block, not the `[系統／年代簿]` line
    above it. `StoryArc.intro_document_content` docstring says this field holds
    "信件全文"; the narrator's scene-setting sentence is a different speaker
    and was the wrong backfill source (see Appendix C, 2026-08-19).
    """

    section = _section(raw, "### 11.1", "### 11.2")
    match = re.search(r"(?ms)^\[信\]\s*\n(?P<text>.+?)(?=\n\s*\n)", section)
    if not match:
        raise StoryStringImportError(
            "story source is missing the §11.1 [信] letter body"
        )
    lines = [line.strip() for line in match.group("text").splitlines() if line.strip()]
    if not lines:
        raise StoryStringImportError(
            "story source's §11.1 [信] letter body is empty"
        )
    return "\n".join(lines)


def _extract_painting_body(raw: str) -> str:
    """The §2.2 description of 〈回家的畫〉 — the text shown for the item itself.

    Item bodies are not spoken lines: nothing in `beats:` references this key,
    and adding a node for it would insert a paragraph into the played script
    that no one wrote as dialogue.  See `_ITEM_BODY_KEYS`.
    """

    section = _section(raw, "### 2.2", "### 2.3")
    paragraphs = [
        block.strip()
        for block in re.split(r"\n\s*\n", section)
        if block.strip() and not block.strip().startswith("#")
    ]
    if not paragraphs:
        raise StoryStringImportError(
            "story source is missing the §2.2 painting description"
        )

    # 去掉 Markdown 的粗體記號。這段文字的讀者是背包畫面，不是 Markdown
    # 算繪器——原樣送出去玩家看到的會是「**〈回家的畫〉**」連星號一起。
    body = paragraphs[0].replace("**", "")

    return _strip_meta_sentences(body)


# §2.2 最後一句是**寫給製作團隊看的規格說明**（「畫名不是作者留下的題字，而是
# 玩家在年代簿中為其使用的暫稱」），不是玩家該讀到的敘述。原樣匯進去的話，
# 背包裡那件道具會在描述完畫面之後，突然用旁白口吻解釋這個命名慣例——2026-08-26
# 實機回報「這些應該不是寫出來讓玩家閱讀的」。
#
# 用句子裡的措辭來認，而不是「砍掉最後一句」：後者會在有人替這段補上一句真正的
# 敘述時默默砍錯東西，而那種錯誤沒有任何症狀浮上來。
_META_SENTENCE_MARKERS = (
    "玩家在年代簿",
    "畫名不是作者",
)


def _strip_meta_sentences(body: str) -> str:
    """Drop spec-facing sentences from a player-facing body."""

    sentences = [part for part in re.split(r"(?<=。)", body) if part.strip()]
    kept = [
        sentence
        for sentence in sentences
        if not any(marker in sentence for marker in _META_SENTENCE_MARKERS)
    ]
    if not kept:
        raise StoryStringImportError(
            "painting description is entirely spec prose after stripping meta sentences"
        )

    return "".join(kept).strip()


def _extract_prologue_looks(raw: str) -> dict[str, str]:
    section = _section(raw, "## 2.", "## 3.")
    result: dict[str, str] = {}
    for label, text_key in _PROLOGUE_LOOKS.items():
        match = re.search(
            rf"(?m)^\|\s*\*\*{re.escape(label)}\*\*\s*\|[^|]*\|\s*(?P<text>[^|]+?)\s*\|\s*$",
            section,
        )
        if not match:
            raise StoryStringImportError(
                f"story source is missing the §2.3 look-point text for {text_key}"
            )
        result[text_key] = match.group("text").strip()
    return result


def _extract_prologue_look_labels(raw: str) -> dict[str, str]:
    """`[可看的位置]` 那三行——玩家在選之前看到的字。"""

    section = _section(raw, "### 11.1", "### 11.2")
    result: dict[str, str] = {}
    for label, text_key in _PROLOGUE_LOOK_LABELS.items():
        if not re.search(r"(?m)^\u00b7\s*" + re.escape(label) + r"\s*$", section):
            raise StoryStringImportError(
                f"story source is missing the §11.1 look-point label for {text_key}"
            )
        result[text_key] = label
    return result


def _narrator_blocks(section: str, speaker: str) -> list[str]:
    """一節裡所有 ```text 圍籬中，指定說話者說的每一段。

    ⚠️ **一個圍籬可以有好幾個說話者。** §11.3 的線索對話就是三段放在同一個
    圍籬裡（紅樓 → 畫背殘句 → 紅樓）。早期版本假設「一個圍籬 ＝ 一個說話者」，
    會把整個圍籬當成第一段的內容——而且不報錯，只是台詞黏在一起。
    """

    blocks: list[str] = []
    for fence in re.findall(r"(?ms)```text\s*\n(?P<body>.*?)\n```", section):
        current: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            if current == speaker and buffer:
                blocks.append("\n".join(buffer))

        for line in fence.splitlines():
            stripped = line.strip()
            header = re.fullmatch(r"\[(?P<name>[^\]]+)\]", stripped)
            if header:
                flush()
                current = header.group("name")
                buffer = []
                continue
            if stripped:
                buffer.append(stripped)
        flush()
    return blocks


def _extract_prologue_merge(raw: str) -> str:
    """§11.1 的「匯流文字」——看過一處之後收束的那一句。

    先前是 `jump: prologue_merge`，而 prologue_merge 從來沒有被寫成節點；
    文字倒是一直都在這裡。
    """

    section = _section(raw, "### 11.1", "### 11.2")
    blocks = _narrator_blocks(section, "系統／年代簿")
    if len(blocks) < 2:
        raise StoryStringImportError("story source is missing the §11.1 匯流文字")
    return blocks[-1]


def _extract_finale(raw: str) -> dict[str, str]:
    """§11.5 終章：開場、三個結局標記的標籤與回應、收尾。

    這一整節的文字早就寫好了，但從來沒有進 §12 的 `beats:` 資料——結局因此是
    一個沒有任何台詞、也不可能寫入 ending_mark 的空節點。
    """

    section = _section(raw, "### 11.5", "## 12.")
    blocks = _narrator_blocks(section, "年代簿")
    if len(blocks) != 2:
        raise StoryStringImportError(
            "story source's §11.5 should have exactly two [年代簿] blocks, "
            f"found {len(blocks)}"
        )

    result = {
        "wanhua.finale.open": blocks[0],
        "wanhua.finale.close": blocks[1],
    }

    for choice, (label_key, reply_key) in _FINALE_MARKS.items():
        match = re.search(
            r"(?m)^\|\s*" + re.escape(choice) + r"\s*\|\s*(?P<reply>[^|]+?)\s*\|",
            section,
        )
        if not match:
            raise StoryStringImportError(
                f"story source is missing the §11.5 ending reply for {reply_key}"
            )
        result[label_key] = choice
        result[reply_key] = match.group("reply").strip()
    return result


def _option_labels(section: str) -> list[str]:
    """`[玩家選項]` 區塊裡的編號清單，依出現順序。"""

    match = re.search(r"(?ms)^\[玩家選項\]\s*\n(?P<body>(?:\d+\..*\n?)+)", section)
    if not match:
        raise StoryStringImportError("chapter is missing its [玩家選項] block")
    return [
        line.split(".", 1)[1].strip()
        for line in match.group("body").splitlines()
        if line.strip() and "." in line
    ]


def _table_reply(section: str, label: str) -> str:
    """「選項｜回應｜…」表格裡，該選項那一列的回應欄。"""

    match = re.search(
        r"(?m)^\|\s*" + re.escape(label) + r"\s*\|\s*(?P<reply>[^|]+?)\s*\|",
        section,
    )
    if not match:
        raise StoryStringImportError(f"chapter is missing the reply for {label!r}")
    return match.group("reply").strip()


def _chapter_texts(raw: str, chapter: dict) -> dict[str, str]:
    """一章的所有台詞：初次對話、線索對話，含選項標籤與回應。"""

    section = _section(raw, *chapter["section"])
    prefix = chapter["prefix"]
    speaker_blocks = _narrator_blocks(section, chapter["speaker"])
    if len(speaker_blocks) < 2:
        raise StoryStringImportError(
            f"{prefix}: expected at least two [{chapter['speaker']}] blocks, "
            f"found {len(speaker_blocks)}"
        )

    result = {
        f"{prefix}.gate.open": speaker_blocks[0],
        f"{prefix}.clue.open": speaker_blocks[1],
    }

    labels = _option_labels(section)
    gate_ids = chapter["gate_options"]
    reveal_ids = chapter.get("reveal_options") or ()
    expected = len(gate_ids) + len(reveal_ids)
    if len(labels) != expected:
        raise StoryStringImportError(
            f"{prefix}: expected {expected} player options, found {len(labels)}"
        )

    for option_id, label in zip(gate_ids, labels[: len(gate_ids)]):
        result[f"{prefix}.gate.{option_id}.label"] = label
        result[f"{prefix}.gate.{option_id}"] = _table_reply(section, label)

    for option_id, label in zip(reveal_ids, labels[len(gate_ids):]):
        result[f"{prefix}.reveal.{option_id}.label"] = label
        result[f"{prefix}.reveal.{option_id}"] = _table_reply(section, label)

    if reveal_ids:
        # 揭露章的第二個區塊就是揭露開場，沒有另外的收尾。
        result[f"{prefix}.reveal.open"] = result.pop(f"{prefix}.clue.open")

    for condition, suffix in chapter.get("clue_conditions") or ():
        variable, _, value = condition.partition("=")
        match = re.search(
            r"(?m)^\|\s*`" + re.escape(variable) + r"=" + re.escape(value)
            + r"`\s*\|\s*(?P<text>[^|]+?)\s*\|",
            section,
        )
        if not match:
            raise StoryStringImportError(
                f"{prefix}: missing the conditional line for {condition}"
            )
        result[f"{prefix}.{suffix}"] = match.group("text").strip()

    fragment_speaker = chapter.get("clue_fragment_speaker")
    if fragment_speaker:
        fragments = _narrator_blocks(section, fragment_speaker)
        if not fragments:
            raise StoryStringImportError(
                f"{prefix}: missing the [{fragment_speaker}] block"
            )
        result[f"{prefix}.clue.fragment"] = fragments[0]

    # 線索對話的收尾：說話者的第三個區塊（揭露章沒有）。
    if not reveal_ids:
        if len(speaker_blocks) < 3:
            raise StoryStringImportError(
                f"{prefix}: missing the closing [{chapter['speaker']}] block"
            )
        result[f"{prefix}.clue.close"] = speaker_blocks[2]

    return result


def _extract_card_texts(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for section_number, keys in _CARD_SECTIONS.items():
        section = _section(raw, f"## {section_number}.", f"## {int(section_number) + 1}.")
        for label, text_key in zip(("史實可考", "本作敘事"), keys):
            match = re.search(rf"(?m)^- 【{label}】：?(?P<text>.+?)\s*$", section)
            if not match:
                raise StoryStringImportError(
                    f"story source is missing the §{section_number} information-card text for {text_key}"
                )
            result[text_key] = match.group("text").strip()
    return result


def parse_story_strings(raw: str) -> tuple[dict[str, str], str]:
    """Extract the current, reviewed prose without rewriting it."""

    opening, intro_title = _extract_prologue_open(raw)
    texts = {
        "wanhua.prologue.open": opening,
        "wanhua.prologue.letter_body": _extract_prologue_letter_body(raw),
        "wanhua.item.homeward_painting": _extract_painting_body(raw),
    }
    texts.update(_extract_prologue_looks(raw))
    texts.update(_extract_prologue_look_labels(raw))
    texts["wanhua.prologue.merge"] = _extract_prologue_merge(raw)
    texts.update(_extract_finale(raw))
    for chapter in _CHAPTERS:
        texts.update(_chapter_texts(raw, chapter))
    texts.update(_extract_card_texts(raw))
    return texts, intro_title


def collect_text_key_references(
    document: Mapping[str, Any], raw: str | None = None
) -> set[str]:
    """Collect references from beat nodes/completions and the card-key list."""

    references: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                # `label_text_key` 是選項標籤（玩家選之前看到的字），跟
                # `text_key`（選之後的敘述）一樣是真的被引用的字串。漏掉它的話
                # 孤兒檢查會把所有標籤判成沒人用而整批拒收。
                if key in {"text_key", "label_text_key"} and isinstance(child, str):
                    references.add(child)
                # conditional_line 的 cases：值就是 text_key，鍵是變數的值
                # （person／history／home）。漏掉的話那三句補句會被判成孤兒。
                elif key == "cases" and isinstance(child, Mapping):
                    references.update(
                        value for value in child.values() if isinstance(value, str)
                    )
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for beat in document.get("beats") or []:
        if isinstance(beat, Mapping):
            visit(beat.get("nodes"))
            visit(beat.get("completion"))

    for card in document.get("info_cards") or []:
        if isinstance(card, Mapping):
            for field in ("historical_text_key", "fiction_text_key"):
                value = card.get(field)
                if isinstance(value, str):
                    references.add(value)

    # Keep the check correct if a future source moves the §7.4 card list out
    # of the YAML block.  The current document repeats these fields in
    # ``info_cards``; set semantics make the duplicate harmless.
    if raw:
        for match in re.finditer(
            r"(?m)^\s*(?:historical_text_key|fiction_text_key):\s*([A-Za-z0-9_.-]+)\s*$",
            raw,
        ):
            references.add(match.group(1))
    return references


# 道具正文：`GET /inventory` 顯示用，**沒有任何 beat 節點會引用它**。
#
# 兩way 覆蓋檢查的用意是抓「打錯的 text_key」與「寫了沒人用的台詞」，而道具正文
# 兩者都不是——它是既有的已審文字（§2.2），只是它的讀者是背包，不是劇本播放器。
#
# ⚠️ 例外只給道具正文。要為別的東西加進來之前先問：那段文字有沒有可能其實
# 是漏接的節點？孤兒檢查擋下的正是那種東西。
#
# 信的正文不在這裡：`wanhua.prologue.letter_body` 真的有一個 `speaker: 信` 的
# 節點在念它（§11.1），它同時是台詞也是道具正文。
_ITEM_BODY_KEYS = frozenset({"wanhua.item.homeward_painting"})


def validate_text_key_coverage(
    references: set[str], texts: Mapping[str, str]
) -> None:
    """Require exact two-way coverage between script references and seed rows."""

    missing = sorted(references - set(texts))
    orphaned = sorted(set(texts) - references - _ITEM_BODY_KEYS)
    problems = []
    if missing:
        problems.append("missing text_key(s): " + ", ".join(missing))
    if orphaned:
        problems.append("orphan text_key(s): " + ", ".join(orphaned))
    if problems:
        raise StoryStringImportError("; ".join(problems))

    empty = sorted(key for key in references if not texts[key])
    if empty:
        raise StoryStringImportError("empty text for text_key(s): " + ", ".join(empty))


def prepare_import(path: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse and validate one complete batch before opening a DB connection."""

    document = load_story_document(path)
    raw = path.read_text(encoding="utf-8")
    texts, intro_title = parse_story_strings(raw)
    references = collect_text_key_references(document, raw)
    validate_text_key_coverage(references, texts)

    arc = document.get("arc")
    if not isinstance(arc, Mapping) or arc.get("arc_id") != "arc_wanhua_homeward_painting":
        raise StoryStringImportError(
            "story strings must be imported from arc_wanhua_homeward_painting"
        )

    reviewed_by = arc.get("reviewed_by")
    prepared = [
        {
            "text_key": key,
            "text": texts[key],
            "reviewed_by": reviewed_by,
            "intro_document_title": intro_title,
        }
        for key in sorted(texts)
    ]
    return dict(arc), prepared


UPSERT_STORY_STRING = text(
    """
    INSERT INTO brain.story_strings (text_key, text, reviewed_by, active)
    VALUES (:text_key, :text, :reviewed_by, true)
    ON CONFLICT (text_key) DO UPDATE SET
        text = EXCLUDED.text,
        reviewed_by = EXCLUDED.reviewed_by,
        active = true
    """
)

BACKFILL_ARC_INTRO = text(
    """
    UPDATE brain.story_arcs
    SET intro_document_title = :intro_document_title,
        intro_document_content = :intro_document_content
    WHERE arc_id = :arc_id
    """
)


def import_story(engine: Any, path: pathlib.Path) -> tuple[str, int]:
    """Validate, upsert strings, and backfill the arc intro atomically."""

    arc, prepared = prepare_import(path)
    # intro_document_content is "信件全文" — the letter's own words, not the
    # 年代簿 narrator line that opens the scene. See Appendix C.
    letter_body = next(
        row for row in prepared if row["text_key"] == "wanhua.prologue.letter_body"
    )
    with engine.begin() as conn:
        for row in prepared:
            conn.execute(UPSERT_STORY_STRING, row)
        conn.execute(
            BACKFILL_ARC_INTRO,
            {
                "arc_id": arc["arc_id"],
                "intro_document_title": letter_body["intro_document_title"],
                "intro_document_content": letter_body["text"],
            },
        )
    return arc["arc_id"], len(prepared)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-file", default=str(DEFAULT_STORY_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = pathlib.Path(args.story_file)

    try:
        arc, prepared = prepare_import(path)
    except (OSError, ValueError) as exc:
        print(f"story string import rejected: {exc}")
        return 1

    print(f"{arc['arc_id']}  story_strings={len(prepared)}  active=true")
    if args.dry_run:
        print("--dry-run: no database writes")
        return 0

    engine = create_engine(settings.database_url)
    try:
        arc_id, count = import_story(engine, path)
    except (OSError, ValueError) as exc:
        print(f"story string import rejected: {exc}")
        return 1
    print(f"imported {arc_id}: {count} story string(s), active=true; intro backfilled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
