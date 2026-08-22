"""把 `citysoul-doc` 的劇本正本同步一份到 `content/story/`。

用法：
    python -m scripts.sync_story_source --check    # 只比對，不同就以 1 結束
    python -m scripts.sync_story_source            # 覆蓋副本

## 為什麼要有這份副本

`import_story_arcs` 與 `import_story_strings` 讀的是隔壁 repo 的劇本正本
（`../citysoul-doc/story/*.md`）。本機跑得動，因為兩個 repo 並排；但 Cloud Run
的 Job 用的是 backend 的映像檔，裡面**沒有那個目錄**：

    No such file or directory: '/citysoul-doc/story/wanhua_...md'

結果是劇情內容從來沒有正式的上線路徑——線上那幾個 beat 是某次本機直連正式
資料庫匯進去的，不在任何流程裡。

放一份副本進 `content/`，劇情就跟人格卡、輪播池、任務走同一條路：
**改內容 → 重建映像檔 → 跑 Job**，不需要記第二套流程。

## 正本仍然是 citysoul-doc

副本只是給容器讀的。兩支匯入器**優先讀隔壁 repo**，讀不到才用副本——
本機一定用到最新的正本，不會因為忘了同步而匯進舊內容。

漂移由 `tests/test_story_source_sync.py` 擋：兩份都在而內容不同就紅燈。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

STORY_FILENAME = "wanhua_district_storyline_aming_landmark_photo_v1.md"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDORED = REPO_ROOT / "content" / "story" / STORY_FILENAME
SOURCE = REPO_ROOT.parent / "citysoul-doc" / "story" / STORY_FILENAME


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比對，不覆蓋。不一致時以 1 結束。",
    )
    args = parser.parse_args()

    if not SOURCE.is_file():
        print(f"✗ 找不到劇本正本：{SOURCE}")
        print("  這支腳本要在 citysoul-doc 與 citysoul-backend 並排的環境跑。")
        return 1

    source_text = SOURCE.read_text(encoding="utf-8")
    vendored_text = (
        VENDORED.read_text(encoding="utf-8") if VENDORED.is_file() else None
    )

    if source_text == vendored_text:
        print(f"✓ 副本與正本一致：{VENDORED.relative_to(REPO_ROOT)}")
        return 0

    if args.check:
        state = "內容不同" if vendored_text is not None else "副本不存在"
        print(f"✗ {state}：{VENDORED.relative_to(REPO_ROOT)}")
        print("  跑 `python -m scripts.sync_story_source` 同步，然後一起 commit。")
        return 1

    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, VENDORED)
    print(f"✓ 已同步：{SOURCE} → {VENDORED.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
