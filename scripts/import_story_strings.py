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


DEFAULT_STORY_FILE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "citysoul-doc"
    / "story"
    / "wanhua_district_storyline_aming_landmark_photo_v1.md"
)

_PROLOGUE_LOOKS = {
    "她的側影": "wanhua.prologue.look.figure",
    "信紙的摺痕": "wanhua.prologue.look.crease",
    "畫面一角": "wanhua.prologue.look.corner",
}
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
    }
    texts.update(_extract_prologue_looks(raw))
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
                if key == "text_key" and isinstance(child, str):
                    references.add(child)
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


def validate_text_key_coverage(
    references: set[str], texts: Mapping[str, str]
) -> None:
    """Require exact two-way coverage between script references and seed rows."""

    missing = sorted(references - set(texts))
    orphaned = sorted(set(texts) - references)
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
