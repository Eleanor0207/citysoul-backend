"""Import reviewed story-arc YAML into ``brain.story_arcs`` and ``story_beats``.

Usage::

    python -m scripts.import_story_arcs
    python -m scripts.import_story_arcs --dry-run

The source document is a Markdown file with a fenced YAML block.  Validation
is completed for the entire batch before a database transaction is opened.
That makes dangling references, cycles, and unreachable beats import-time
errors instead of silent runtime dead ends.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings


DEFAULT_STORY_FILE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "citysoul-doc"
    / "story"
    / "wanhua_district_storyline_aming_landmark_photo_v1.md"
)

_YAML_BLOCK = re.compile(r"(?ms)^```yaml\s*\n(.*?)^```\s*$")


class StoryImportError(ValueError):
    """The complete story batch is invalid and must not be written."""


def _json(value: Any) -> str:
    """Keep source values lossless and readable in the text columns."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_story_document(path: pathlib.Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    documents = []
    for match in _YAML_BLOCK.finditer(raw):
        candidate = yaml.safe_load(match.group(1))
        if isinstance(candidate, dict) and "arc" in candidate and "beats" in candidate:
            documents.append(candidate)

    if len(documents) != 1:
        raise StoryImportError(
            f"{path}: expected exactly one arc/beats YAML block, found {len(documents)}"
        )
    return documents[0]


def _beat_ids_in_trigger(value: Any) -> set[str]:
    """Extract explicit beat-completion dependencies from nested trigger YAML."""

    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "beat_completed" and isinstance(child, str):
                found.add(child)
            else:
                found.update(_beat_ids_in_trigger(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_beat_ids_in_trigger(child))
    return found


def _quest_ids_in_trigger(value: Any) -> set[str]:
    """Extract quest requirements expressed as ``quest_completed`` clauses.

    這些先前被安靜丟棄：匯入器只認得 ``beat_completed`` 與 ``has_item``，所以
    文件裡「做完任務才推得動」的設計從來沒有進過資料庫。缺口沒有症狀——玩家
    不做任務也能把主線推完（backend#71／#72，0028）。
    """
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "quest_completed" and isinstance(child, str):
                found.add(child)
            else:
                found.update(_quest_ids_in_trigger(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_quest_ids_in_trigger(child))
    return found


def _item_ids_in_trigger(value: Any) -> set[str]:
    """Extract item requirements expressed as ``has_item`` trigger clauses."""

    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "has_item" and isinstance(child, str):
                found.add(child)
            else:
                found.update(_item_ids_in_trigger(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_item_ids_in_trigger(child))
    return found


def _beat_ids_in_commands(value: Any) -> dict[str, set[str]]:
    """Return reverse edges for commands that unlock another beat."""

    edges: dict[str, set[str]] = defaultdict(set)
    if isinstance(value, Mapping):
        target = value.get("unlock_beat_id")
        if isinstance(target, str):
            edges[target].add("__current__")
        for child in value.values():
            for target_id, sources in _beat_ids_in_commands(child).items():
                edges[target_id].update(sources)
    elif isinstance(value, list):
        for child in value:
            for target_id, sources in _beat_ids_in_commands(child).items():
                edges[target_id].update(sources)
    return edges


def derive_prerequisites(
    arc: Mapping[str, Any],
    beats: list[Mapping[str, Any]],
    items: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Map source relationships into the database's prerequisite field.

    The current story format expresses dependencies through ``requires`` item
    IDs and ``beat_completed`` triggers.  Item ``source_beat_id`` values make
    the former unambiguous.  An explicit ``prerequisite_beat_ids`` field, when
    present, is also preserved for future story documents and test fixtures.
    """

    item_sources = {
        item["item_id"]: item["source_beat_id"]
        for item in items or arc.get("items") or []
        if isinstance(item, Mapping)
        and isinstance(item.get("item_id"), str)
        and isinstance(item.get("source_beat_id"), str)
    }

    reverse_unlocks: dict[str, set[str]] = defaultdict(set)
    for beat in beats:
        for target_id, sources in _beat_ids_in_commands(beat.get("commands")).items():
            if "__current__" in sources:
                reverse_unlocks[target_id].add(beat["beat_id"])

    result = []
    for beat in beats:
        beat_id = beat.get("beat_id")
        if not isinstance(beat_id, str) or not beat_id:
            raise StoryImportError("every beat must have a non-empty beat_id")

        prerequisites = set(beat.get("prerequisite_beat_ids") or [])
        prerequisites.update(_beat_ids_in_trigger(beat.get("trigger")))
        for item_id in beat.get("requires") or beat.get("required_item_ids") or []:
            source_beat_id = item_sources.get(item_id)
            if source_beat_id:
                prerequisites.add(source_beat_id)
        for item_id in _item_ids_in_trigger(beat.get("trigger")):
            source_beat_id = item_sources.get(item_id)
            if source_beat_id:
                prerequisites.add(source_beat_id)
        prerequisites.update(reverse_unlocks.get(beat_id, set()))
        result.append(
            {
                "source": beat,
                "beat_id": beat_id,
                "prerequisite_beat_ids": sorted(prerequisites) or None,
            }
        )
    return result


def validate_prerequisite_graph(beats: Iterable[Mapping[str, Any]]) -> None:
    """Reject dangling references, cycles, and islands with named beat IDs."""

    rows = list(beats)
    beat_ids = [row["beat_id"] for row in rows]
    duplicates = sorted({beat_id for beat_id in beat_ids if beat_ids.count(beat_id) > 1})
    if duplicates:
        raise StoryImportError(f"duplicate beat_id(s): {', '.join(duplicates)}")

    known = set(beat_ids)
    graph: dict[str, set[str]] = {}
    dangling: dict[str, set[str]] = {}
    for row in rows:
        beat_id = row["beat_id"]
        prerequisites = set(row.get("prerequisite_beat_ids") or [])
        missing = prerequisites - known
        if missing:
            dangling[beat_id] = missing
        graph[beat_id] = prerequisites & known
    if dangling:
        details = "; ".join(
            f"{beat_id} -> {', '.join(sorted(missing))}"
            for beat_id, missing in sorted(dangling.items())
        )
        raise StoryImportError(f"dangling prerequisite_beat_ids: {details}")

    state: dict[str, int] = {}
    cycle_nodes: set[str] = set()

    def visit(beat_id: str, stack: list[str]) -> None:
        state[beat_id] = 1
        for prerequisite in graph[beat_id]:
            if state.get(prerequisite) == 1:
                start = stack.index(prerequisite) if prerequisite in stack else 0
                cycle_nodes.update(stack[start:])
                cycle_nodes.add(prerequisite)
            elif state.get(prerequisite, 0) == 0:
                visit(prerequisite, [*stack, prerequisite])
        state[beat_id] = 2

    for beat_id in graph:
        if state.get(beat_id, 0) == 0:
            visit(beat_id, [beat_id])
    forward: dict[str, set[str]] = defaultdict(set)
    roots = {beat_id for beat_id, prerequisites in graph.items() if not prerequisites}
    # A graph with no roots is necessarily an all-cycle/deadlock batch.  Report
    # the cycle class directly; otherwise the same nodes would be described as
    # islands before the more useful cycle diagnostic below.
    if cycle_nodes and not roots:
        raise StoryImportError(f"cycle in prerequisite graph: {', '.join(sorted(cycle_nodes))}")

    for beat_id, prerequisites in graph.items():
        for prerequisite in prerequisites:
            forward[prerequisite].add(beat_id)
    reachable: set[str] = set(roots)
    queue = deque(roots)
    while queue:
        current = queue.popleft()
        for child in forward[current]:
            if child not in reachable:
                reachable.add(child)
                queue.append(child)
    islands = sorted(set(graph) - reachable)
    if islands:
        raise StoryImportError(f"island beat(s) unreachable from a root: {', '.join(islands)}")
    if cycle_nodes:
        raise StoryImportError(f"cycle in prerequisite graph: {', '.join(sorted(cycle_nodes))}")


def prepare_import(path: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = load_story_document(path)
    arc = document.get("arc")
    beats = document.get("beats")
    if not isinstance(arc, Mapping) or not isinstance(beats, list) or not beats:
        raise StoryImportError(f"{path}: arc must be a mapping and beats must be non-empty")

    prepared = derive_prerequisites(arc, beats, document.get("items") or [])
    validate_prerequisite_graph(prepared)

    # ⚠️ `variables:`、`items:`、`info_cards:` 是文件的**頂層** key，跟 `arc:`
    # 平行，不在 `arc:` 裡面。回傳前掛進來，呼叫端才不必知道這個結構差異。
    #
    # （`items:` 已經由 derive_prerequisites 另外收下，這裡不重複。）
    merged = dict(arc)
    for key in ("variables", "info_cards"):
        if document.get(key) is not None:
            merged[key] = document[key]
    return merged, prepared


UPSERT_ARC = text(
    """
    INSERT INTO brain.story_arcs
        (arc_id, title, district_id, intro_document_title, intro_document_content,
         intro_document_asset_id, summary, variables, active, reviewed_by)
    VALUES
        (:arc_id, :title, :district_id, :intro_document_title, :intro_document_content,
         :intro_document_asset_id, :summary, CAST(:variables AS JSONB), true, :reviewed_by)
    ON CONFLICT (arc_id) DO UPDATE SET
        title = EXCLUDED.title,
        district_id = EXCLUDED.district_id,
        intro_document_title = EXCLUDED.intro_document_title,
        intro_document_content = EXCLUDED.intro_document_content,
        intro_document_asset_id = EXCLUDED.intro_document_asset_id,
        summary = EXCLUDED.summary,
        variables = EXCLUDED.variables,
        active = true,
        reviewed_by = EXCLUDED.reviewed_by
    """
)

UPSERT_BEAT = text(
    """
    INSERT INTO brain.story_beats
        (beat_id, arc_id, character_id, sequence_order, trigger_condition,
         narrative_directive, prerequisite_beat_ids, required_item_ids,
         required_quest_ids, contingency_notes, one_time, active, reviewed_by)
    VALUES
        (:beat_id, :arc_id, :character_id, :sequence_order, :trigger_condition,
         :narrative_directive, :prerequisite_beat_ids, :required_item_ids,
         :required_quest_ids, :contingency_notes, :one_time, true, :reviewed_by)
    ON CONFLICT (beat_id) DO UPDATE SET
        arc_id = EXCLUDED.arc_id,
        character_id = EXCLUDED.character_id,
        sequence_order = EXCLUDED.sequence_order,
        trigger_condition = EXCLUDED.trigger_condition,
        narrative_directive = EXCLUDED.narrative_directive,
        prerequisite_beat_ids = EXCLUDED.prerequisite_beat_ids,
        required_item_ids = EXCLUDED.required_item_ids,
        required_quest_ids = EXCLUDED.required_quest_ids,
        contingency_notes = EXCLUDED.contingency_notes,
        one_time = EXCLUDED.one_time,
        active = true,
        reviewed_by = EXCLUDED.reviewed_by,
        updated_at = now()
    """
)


def _beat_row(arc: Mapping[str, Any], prepared: Mapping[str, Any], sequence_order: int) -> dict[str, Any]:
    source = prepared["source"]
    directive = {
        key: source[key]
        for key in ("nodes", "commands", "completion")
        if key in source
    }
    if not directive:
        raise StoryImportError(
            f"{source['beat_id']}: no source for required narrative_directive"
        )

    required_items = source.get("requires")
    if required_items is None:
        required_items = source.get("required_item_ids")

    # ⚠️ 任務**不會**變成 prerequisite_beat_ids（見 derive_prerequisites 的說明）。
    # 它是第三道門，存在自己的欄位。
    required_quests = sorted(_quest_ids_in_trigger(source.get("trigger")))
    return {
        "beat_id": source["beat_id"],
        "arc_id": arc["arc_id"],
        "character_id": source.get("character_id"),
        "sequence_order": sequence_order,
        "trigger_condition": _json(source.get("trigger")),
        "narrative_directive": _json(directive),
        "prerequisite_beat_ids": prepared["prerequisite_beat_ids"],
        "required_item_ids": required_items or None,
        "required_quest_ids": required_quests or None,
        "contingency_notes": source.get("contingency_notes"),
        "one_time": source.get("one_time", True),
        "reviewed_by": source.get("reviewed_by", arc.get("reviewed_by")),
    }


UPSERT_INFO_CARD = text(
    """
    INSERT INTO brain.story_info_cards
        (card_id, arc_id, historical_text_key, fiction_text_key, review_status, active)
    VALUES
        (:card_id, :arc_id, :historical_text_key, :fiction_text_key, :review_status, true)
    ON CONFLICT (card_id) DO UPDATE SET
        arc_id = EXCLUDED.arc_id,
        historical_text_key = EXCLUDED.historical_text_key,
        fiction_text_key = EXCLUDED.fiction_text_key,
        review_status = EXCLUDED.review_status,
        active = true
    """
)


def _info_card_rows(arc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """arc 文件 §12 的 `info_cards:` 區塊。

    先前完全沒有被匯入——文字進了 `story_strings`，卡片本身沒有，所以 beat 的
    `show_info_card` 指向一個查不到的 id。
    """
    rows = []
    for card in arc.get("info_cards") or []:
        if not isinstance(card, Mapping) or not card.get("card_id"):
            raise StoryImportError(f"info_cards 有一則缺 card_id：{card!r}")
        rows.append({
            "card_id": card["card_id"],
            "arc_id": arc["arc_id"],
            "historical_text_key": card.get("historical_text_key"),
            "fiction_text_key": card.get("fiction_text_key"),
            "review_status": card.get("review_status"),
        })
    return rows


def import_story(engine: Any, path: pathlib.Path) -> tuple[str, int]:
    """Validate and atomically upsert one story document."""

    arc, prepared = prepare_import(path)
    seen_orders: dict[str | None, int] = defaultdict(int)
    beat_rows = []
    for prepared_beat in prepared:
        character_id = prepared_beat["source"].get("character_id")
        seen_orders[character_id] += 1
        beat_rows.append(_beat_row(arc, prepared_beat, seen_orders[character_id]))

    arc_row = {
        "arc_id": arc["arc_id"],
        "title": arc["title"],
        "district_id": arc.get("district_id"),
        "intro_document_title": arc.get("intro_document_title"),
        "intro_document_content": arc.get("intro_document_content"),
        "intro_document_asset_id": arc.get("intro_document_asset_id"),
        "summary": arc.get("summary"),
        "reviewed_by": arc.get("reviewed_by"),
        # 每個劇情變數的合法值。存進來之後寫入端才驗得了客戶端送來的值。
        "variables": _json(arc.get("variables")) if arc.get("variables") else None,
    }
    card_rows = _info_card_rows(arc)
    with engine.begin() as conn:
        # 懸空的 required_quest_ids 跟懸空的 prerequisite_beat_ids 一樣是靜默的
        # 壞法：那個 beat 永遠解不開，玩家卡住，log 乾淨。前置鏈的環與懸空引用
        # 在 prepare_import() 就擋掉了，但任務要查資料庫才知道存不存在，所以
        # 這一道檢查只能在這裡做——寫入之前，同一個交易。
        known_quests = {
            row[0] for row in conn.execute(text("SELECT quest_id FROM quests"))
        }
        dangling = sorted(
            f"{row['beat_id']} → {quest_id}"
            for row in beat_rows
            for quest_id in (row["required_quest_ids"] or [])
            if quest_id not in known_quests
        )
        if dangling:
            raise StoryImportError(
                "required_quest_ids 指向不存在的任務（先跑 import_quests）："
                + "、".join(dangling)
            )

        # 卡片指向的 text_key 要真的存在，否則玩家點開卡片是空的。跟懸空的
        # required_quest_ids 一樣是靜默的壞法，所以在寫入前擋。
        known_strings = {
            row[0] for row in conn.execute(text("SELECT text_key FROM brain.story_strings"))
        }
        missing = sorted(
            f"{row['card_id']} → {key}"
            for row in card_rows
            for key in (row["historical_text_key"], row["fiction_text_key"])
            if key and key not in known_strings
        )
        if missing:
            raise StoryImportError(
                "info_cards 指向不存在的 text_key（先跑 import_story_strings）："
                + "、".join(missing)
            )

        conn.execute(UPSERT_ARC, arc_row)

        # 🔒 先把既有序號推到負數區間再 upsert。
        #
        # uq_story_beats_sequence 是 (arc_id, character_id, sequence_order) 唯一。
        # 某個角色一旦多出一個 beat，同一批裡的既有 beat 就要往後挪，而 upsert
        # 是一列一列做的——新的 1 會撞到還沒被挪走的舊 1，整批失敗。
        conn.execute(
            text(
                "UPDATE brain.story_beats SET sequence_order = -sequence_order - 1 "
                "WHERE arc_id = :arc_id AND sequence_order >= 0"
            ),
            {"arc_id": arc["arc_id"]},
        )

        for row in beat_rows:
            conn.execute(UPSERT_BEAT, row)
        for row in card_rows:
            conn.execute(UPSERT_INFO_CARD, row)

        # 還留在負數區間的，是資料庫有、但文件已經不再定義的 beat。玩家可能
        # 卡在那個節點上，而它不會出現在任何一次匯入的輸出裡。
        stranded = sorted(
            row[0]
            for row in conn.execute(
                text(
                    "SELECT beat_id FROM brain.story_beats "
                    "WHERE arc_id = :arc_id AND sequence_order < 0"
                ),
                {"arc_id": arc["arc_id"]},
            )
        )
        if stranded:
            raise StoryImportError(
                "資料庫裡有文件已經不再定義的 beat（整批未寫入）："
                + "、".join(stranded)
            )
    return arc["arc_id"], len(beat_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--story-file", default=str(DEFAULT_STORY_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = pathlib.Path(args.story_file)

    try:
        arc, prepared = prepare_import(path)
        # Build rows during dry-run too, so missing required source mappings fail
        # before a real invocation opens a connection.
        seen_orders: dict[str | None, int] = defaultdict(int)
        for prepared_beat in prepared:
            character_id = prepared_beat["source"].get("character_id")
            seen_orders[character_id] += 1
            _beat_row(arc, prepared_beat, seen_orders[character_id])
    except (OSError, yaml.YAMLError, StoryImportError) as exc:
        print(f"✗ story import rejected: {exc}")
        return 1

    print(f"{arc['arc_id']}  beats={len(prepared)}  active=true")
    if args.dry_run:
        print("--dry-run: no database writes")
        return 0

    engine = create_engine(settings.database_url)
    try:
        arc_id, count = import_story(engine, path)
    except (OSError, StoryImportError) as exc:
        print(f"✗ story import rejected: {exc}")
        return 1
    print(f"imported {arc_id}: {count} beat(s), active=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
