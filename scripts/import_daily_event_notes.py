"""把 `content/daily_event_notes/*.yaml` 匯入 `brain.daily_event_curated_notes`。

用法：
    python -m scripts.import_daily_event_notes --dry-run
    python -m scripts.import_daily_event_notes

## 這一池是「核心迴圈有沒有東西可看」的唯一輸入

B9 的當日情境有三種來源：節慶日曆、官方活動、人工審核輪播池。前兩者只在特定
日子有東西，所以**平常日子能不能產出非 fallback 的敘事，完全取決於這一池**。
池是空的，玩家每天看到的都是同一句寫死的保底文案——「隔天回來會看到變化」這件
CONTEXT.md 定義的核心迴圈就不成立。

## 輪播是純函式，不存游標

`daily_event_sources.py:86-93` 用 `event_date.toordinal() % len(active notes)` 選
一則，沒有任何 per-day 狀態。所以：

- 七則就是七天一輪；改成八則，整個輪播的相位會跟著變（這是預期行為，不是 bug）
- `rotation_order` 只決定穩定排序，不決定哪天出現哪一則

## UPSERT 的鍵是 (place_id, rotation_order)

跟人格卡「新版本 append、舊版本留著」的模式不同：輪播池沒有版本概念，同一個
位置就是同一則素材的最新文字。`uq_daily_event_curated_notes_rotation` 是這件事
在資料庫層的保證。

檔案裡少掉的位置**不會**被刪除——縮短一池要明確用 `--prune`，因為那會改變輪播
的長度與相位，是內容決定不是匯入細節。

## active 一律寫 true

比照 2026-08-18 取消人工審核閘門的決定（見 scripts/import_personas.py）。
`reviewed_by` 照實寫進資料庫：`MVP_NO_REVIEW` 代表沒有人讀過，那是稽核資訊，
不是擋下匯入的理由。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml
from sqlalchemy import create_engine, text

from app.core.config import settings

NOTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "daily_event_notes"
PLACEHOLDERS = ("PENDING_NARRATIVE_REVIEW", "PENDING_HUMAN_REVIEW", "待定", "TODO")

UPSERT_NOTE = text(
    """
    INSERT INTO brain.daily_event_curated_notes
        (place_id, rotation_order, note_text, active, reviewed_by, reviewed_at)
    VALUES
        (:place_id, :rotation_order, :note_text, true, :reviewed_by, now())
    ON CONFLICT (place_id, rotation_order) DO UPDATE SET
        note_text = excluded.note_text,
        active = true,
        reviewed_by = excluded.reviewed_by,
        reviewed_at = now()
    """
)

DELETE_EXTRA = text(
    """
    DELETE FROM brain.daily_event_curated_notes
    WHERE place_id = :place_id AND rotation_order >= :keep_from
    """
)

KNOWN_SPIRITS = text("SELECT spirit_id FROM spirits")


def load_files(directory: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [
        (path, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]


def validate(loaded: list[tuple[pathlib.Path, dict]]) -> list[str]:
    errors: list[str] = []
    seen_places: set[str] = set()
    for path, data in loaded:
        name = path.name
        place_id = (data or {}).get("place_id")
        if not place_id:
            errors.append(f"{name}：缺 place_id")
            continue
        if place_id in seen_places:
            errors.append(f"{name}：place_id {place_id} 重複，兩個檔案會互相覆蓋")
        seen_places.add(place_id)
        if not data.get("reviewed_by"):
            errors.append(f"{name}：缺 reviewed_by")
        notes = data.get("notes") or []
        if not notes:
            errors.append(f"{name}：notes 是空的")
            continue
        orders = [n.get("rotation_order") for n in notes]
        if sorted(orders) != list(range(len(notes))):
            errors.append(
                f"{name}：rotation_order 必須是從 0 開始、不重複、不跳號的連續整數，"
                f"目前是 {orders}"
            )
        for note in notes:
            text_value = (note.get("note_text") or "").strip()
            if not text_value:
                errors.append(f"{name}：rotation_order {note.get('rotation_order')} 的 note_text 是空的")
                continue
            for placeholder in PLACEHOLDERS:
                if placeholder in text_value:
                    errors.append(
                        f"{name}：rotation_order {note.get('rotation_order')} 還帶著佔位字串 {placeholder}"
                    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--notes-dir", default=str(NOTES_DIR))
    parser.add_argument(
        "--prune",
        action="store_true",
        help="刪掉檔案裡不再有的 rotation_order。會改變輪播長度與相位，預設不做。",
    )
    args = parser.parse_args()

    directory = pathlib.Path(args.notes_dir)
    if not directory.is_dir():
        print(f"✗ 找不到目錄：{directory}")
        return 1

    loaded = load_files(directory)
    if not loaded:
        print(f"✗ {directory} 裡沒有 YAML")
        return 1

    errors = validate(loaded)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        print(f"\n{len(errors)} 個問題，整批不匯入。")
        return 1

    print(f"{len(loaded)} 份 YAML 全部通過驗證")
    for path, data in loaded:
        notes = data["notes"]
        print(
            f"  {data['place_id']:38s} {len(notes)} 則"
            f"（{len(notes)} 天一輪）  reviewed_by={data['reviewed_by']}"
        )

    if args.dry_run:
        print("\n--dry-run：沒有寫入資料庫")
        return 0

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        known = {row[0] for row in conn.execute(KNOWN_SPIRITS)}
        unknown = [data["place_id"] for _, data in loaded if data["place_id"] not in known]
        if unknown:
            # 沒有外鍵擋著（這張表的 place_id 不是外鍵），所以自己擋：打錯字的
            # place_id 會安靜地永遠不被讀到，那種錯誤沒有任何症狀。
            print(f"✗ 這些 place_id 不在 spirits 裡：{unknown}")
            return 1

        for _, data in loaded:
            for note in data["notes"]:
                conn.execute(
                    UPSERT_NOTE,
                    {
                        "place_id": data["place_id"],
                        "rotation_order": note["rotation_order"],
                        "note_text": note["note_text"].strip(),
                        "reviewed_by": data["reviewed_by"],
                    },
                )
            if args.prune:
                removed = conn.execute(
                    DELETE_EXTRA,
                    {"place_id": data["place_id"], "keep_from": len(data["notes"])},
                ).rowcount
                if removed:
                    print(f"  --prune：{data['place_id']} 刪掉 {removed} 則多出來的")

    print("\n匯入完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
