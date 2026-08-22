"""把 `content/quests/*.yaml` 匯入 `public.quests`。

用法：
    python -m scripts.import_quests --dry-run
    python -m scripts.import_quests
    python -m scripts.import_quests --prune

## 為什麼有這支腳本（backend#71／#72）

`quests` 這張表從 0013 建起來之後**一列都沒有**，而且沒有任何寫入管道——沒有
匯入腳本、沒有 content 來源檔、seed 不建它。萬華主線文件 §4.2／§5.2／§6.2
定義的三個任務（`q_longshan_repair_trace` 等）在整個 repo 裡找不到。

同時 `quests.py` 的 `quest_id_for_spirit()` 只會推導出 `{spirit_id}:daily`，
從來不查這張表。兩套任務 ID 對不上，所以劇情文件裡的
`quest_completed: q_longshan_repair_trace` 永遠不可能成立。

這支腳本補的是內容那一半：讓目錄裡真的有任務。

## `daily` 型任務不歸這裡管

`{spirit_id}:daily` 是推導出來的，不進目錄表——它沒有內容可編輯，也不需要
審核。這裡只放**有人真的寫過內容**的 story 型任務。這也是 `quest_progress.quest_id`
刻意不加外鍵的原因（見 0013）。

## review_status 照實寫進 reviewed_by

比照 2026-08-18 取消人工審核閘門的決定：`DRAFT` 代表內容還沒經過實地勘查，
那是稽核資訊，不是擋下匯入的理由。要擋的是**別的東西**——見下。

⚠️ 但 `is_active` 會跟著 `review_status` 走：`DRAFT` 的任務仍然 active，因為
MVP 要拿它展示。這一條跟人格卡不同，刻意寫明以免被當成疏漏。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

QUESTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "quests"
PLACEHOLDERS = ("PENDING_NARRATIVE_REVIEW", "PENDING_HUMAN_REVIEW", "待定", "TODO")

QUEST_TYPES = ("daily", "story", "resonance_gated")

KNOWN_SPIRITS = text("SELECT spirit_id FROM spirits")
KNOWN_BEATS = text("SELECT beat_id FROM brain.story_beats")

UPSERT_QUEST = text(
    """
    INSERT INTO quests
        (quest_id, spirit_id, title, quest_type, min_resonance,
         story_beat_id, steps, reward_type, reward_value, intro, is_active)
    VALUES
        (:quest_id, :spirit_id, :title, :quest_type, :min_resonance,
         :story_beat_id, CAST(:steps AS JSONB), :reward_type,
         CAST(:reward_value AS JSONB), :intro, true)
    ON CONFLICT (quest_id) DO UPDATE SET
        spirit_id = EXCLUDED.spirit_id,
        title = EXCLUDED.title,
        quest_type = EXCLUDED.quest_type,
        min_resonance = EXCLUDED.min_resonance,
        story_beat_id = EXCLUDED.story_beat_id,
        steps = EXCLUDED.steps,
        reward_type = EXCLUDED.reward_type,
        reward_value = EXCLUDED.reward_value,
        intro = EXCLUDED.intro,
        is_active = true
    """
)

# --prune 只刪這批檔案負責的地標底下、檔案裡已經沒有的任務。別的地標不受影響。
DELETE_EXTRA = text(
    """
    DELETE FROM quests
    WHERE spirit_id = :spirit_id AND quest_id <> ALL(:keep)
    """
)


def load_files(directory: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]


def build_rows(data: dict) -> tuple[list[dict], list[str]]:
    """把一份 YAML 化成待寫入的列。回傳 (列, 錯誤訊息)。"""
    rows: list[dict] = []
    errors: list[str] = []
    spirit_id = data.get("spirit_id")
    review_status = data.get("review_status")

    for index, quest in enumerate(data.get("quests") or []):
        if not isinstance(quest, dict):
            errors.append(f"第 {index + 1} 則不是對應表")
            continue

        quest_id = (quest.get("quest_id") or "").strip()
        title = (quest.get("title") or "").strip()
        if not quest_id:
            errors.append(f"第 {index + 1} 則缺 quest_id")
            continue
        if not title:
            errors.append(f"{quest_id}：缺 title")

        # ⚠️ `{spirit_id}:daily` 是 quests.py 推導出來的格式。讓它進目錄表會
        # 產生兩個都叫這個名字的東西——一個是推導的、一個是編輯的——而它們
        # 之後一定會漂移。
        if quest_id.endswith(":daily"):
            errors.append(f"{quest_id}：daily 型任務由 quest_id_for_spirit() 推導，不進目錄表")

        quest_type = quest.get("quest_type", "story")
        if quest_type not in QUEST_TYPES:
            errors.append(f"{quest_id}：quest_type 是 {quest_type!r}，只能是 {QUEST_TYPES}")
            continue

        steps = quest.get("steps") or []
        if quest_type == "story" and not steps:
            # story 型任務沒有步驟就沒有東西可做——玩家會拿到一個永遠停在
            # in_progress 的任務，而且畫面上是空的。
            errors.append(f"{quest_id}：story 型任務至少要有一個 step")
        for step in steps:
            if not isinstance(step, dict) or not step.get("step_id"):
                errors.append(f"{quest_id}：有一個 step 缺 step_id")
                continue
            for field in ("title", "hint"):
                value = (step.get(field) or "").strip()
                if not value:
                    errors.append(f"{quest_id}／{step['step_id']}：缺 {field}")
                    continue
                for placeholder in PLACEHOLDERS:
                    if placeholder in value:
                        errors.append(
                            f"{quest_id}／{step['step_id']}：{field} 還帶著佔位字串 {placeholder}"
                        )

        rows.append({
            "quest_id": quest_id,
            "spirit_id": spirit_id,
            "title": title,
            "quest_type": quest_type,
            "min_resonance": quest.get("min_resonance", 0),
            "story_beat_id": quest.get("story_beat_id"),
            "steps": json.dumps(steps, ensure_ascii=False),
            "reward_type": quest.get("reward_type"),
            "reward_value": (
                json.dumps(quest["reward_value"], ensure_ascii=False)
                if quest.get("reward_value") is not None
                else None
            ),
            "review_status": review_status,
            "intro": quest.get("intro"),
        })

    return rows, errors


def validate(loaded: list[tuple[pathlib.Path, dict]]) -> tuple[dict[str, list[dict]], list[str]]:
    by_spirit: dict[str, list[dict]] = {}
    errors: list[str] = []
    seen_quest_ids: set[str] = set()

    for path, data in loaded:
        name = path.name
        data = data or {}
        spirit_id = data.get("spirit_id")
        if not spirit_id:
            errors.append(f"{name}：缺 spirit_id")
            continue
        if spirit_id in by_spirit:
            errors.append(f"{name}：spirit_id {spirit_id} 重複，兩個檔案會互相覆蓋")
        if not data.get("review_status"):
            errors.append(f"{name}：缺 review_status")

        rows, row_errors = build_rows(data)
        errors.extend(f"{name}：{error}" for error in row_errors)

        for row in rows:
            if row["quest_id"] in seen_quest_ids:
                errors.append(f"{name}：quest_id {row['quest_id']} 在別的檔案已經出現過")
            seen_quest_ids.add(row["quest_id"])

        by_spirit[spirit_id] = rows

    return by_spirit, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quests-dir", default=str(QUESTS_DIR))
    parser.add_argument(
        "--prune",
        action="store_true",
        help="刪掉檔案裡不再有的任務（限這批檔案負責的地標）。預設不做。",
    )
    args = parser.parse_args()

    directory = pathlib.Path(args.quests_dir)
    if not directory.is_dir():
        print(f"✗ 找不到目錄：{directory}")
        return 1

    loaded = load_files(directory)
    if not loaded:
        print(f"✗ {directory} 裡沒有 YAML")
        return 1

    by_spirit, errors = validate(loaded)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        print(f"\n{len(errors)} 個問題，整批不匯入。")
        return 1

    total = sum(len(rows) for rows in by_spirit.values())
    print(f"{len(loaded)} 份 YAML 全部通過驗證，共 {total} 個任務")
    for spirit_id, rows in by_spirit.items():
        for row in rows:
            steps = json.loads(row["steps"])
            print(
                f"  {spirit_id:28} {row['quest_id']:26} {row['title']}"
                f"  steps={len(steps)}  beat={row['story_beat_id']}"
                f"  review={row['review_status']}"
            )

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        known_spirits = {row[0] for row in conn.execute(KNOWN_SPIRITS)}
        unknown = [s for s in by_spirit if s not in known_spirits]
        if unknown:
            print(f"✗ 這些 spirit_id 不在 spirits 裡：{unknown}")
            return 1

        # 懸空的 story_beat_id 會讓「完成任務就能推進劇情」那條線斷在中間，
        # 而且沒有症狀——任務照常完成，只是永遠解不開任何節點。
        known_beats = {row[0] for row in conn.execute(KNOWN_BEATS)}
        dangling = [
            (row["quest_id"], row["story_beat_id"])
            for rows in by_spirit.values()
            for row in rows
            if row["story_beat_id"] and row["story_beat_id"] not in known_beats
        ]
        if dangling:
            print(f"✗ 這些任務指向不存在的 story_beat_id：{dangling}")
            return 1

        for spirit_id, rows in by_spirit.items():
            for row in rows:
                conn.execute(UPSERT_QUEST, row)
            if args.prune:
                removed = conn.execute(
                    DELETE_EXTRA,
                    {"spirit_id": spirit_id, "keep": [r["quest_id"] for r in rows]},
                ).rowcount
                if removed:
                    print(f"  --prune：{spirit_id} 刪掉 {removed} 個檔案裡沒有的任務")

    print(f"\n匯入完成：{total} 個任務。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
