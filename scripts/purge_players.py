"""刪除玩家與其所有衍生資料。**測試用，不是營運工具。**

用法：
    python -m scripts.purge_players --dry-run              # 只列出，不刪
    python -m scripts.purge_players --all                  # 全部刪除
    python -m scripts.purge_players --prefix audit-        # 只刪符合前綴的

## ⚠️ 這支腳本會不可逆地刪除資料

沒有軟刪除、沒有備份。跑之前先 `--dry-run` 看清楚要刪什麼。

## 刪除順序不能亂

`players` 被八張表引用，其中七張有外鍵——順序錯了資料庫會擋下來（那是好事）。
真正危險的是第八張：

    brain.memory_embeddings.player_id 是 UUID 但**沒有外鍵**（WBS-API 決策4：
    身體與腦袋的表不建跨 schema 外鍵）

所以刪 players 時資料庫**不會**提醒你那裡還有資料。漏掉它的結果是一堆指向不存在
玩家的向量，不會報錯、不會被發現，只會慢慢累積並在記憶檢索時混進別人的結果。
這支腳本明確處理它，順序寫死在 `_DEPENDENTS` 裡。

## 為什麼不用 ON DELETE CASCADE

級聯刪除讓「刪一個玩家」變成一個看不見範圍的操作——寫 `DELETE FROM players`
的人不會知道自己同時清掉了對話日誌與共鳴值。這裡逐張刪並逐張回報筆數，
刪掉什麼是看得見的。

而且級聯救不了 `memory_embeddings`，沒有外鍵就沒有級聯。

## 這不處理帳號刪除的法遵需求

真正的「玩家刪除帳號」流程（citysoul_data_schema.md §13 列為未實作）還需要處理
Cloud Storage 上的照片、以及刪除的稽核紀錄。這支只清資料表。
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings

# 順序：先刪引用者，最後刪 players。
#
# `memory_embeddings` 排第一是刻意的——它是唯一沒有外鍵保護的，漏掉不會有任何
# 錯誤訊息，所以放在最顯眼的位置。
_DEPENDENTS = [
    ("brain.memory_embeddings", "player_id", "⚠️ 值關聯，無外鍵"),
    ("dialogue_turns", "player_id", ""),
    ("players_story_progress", "player_id", ""),
    ("player_inventory", "player_id", ""),
    ("encounter_collections", "player_id", ""),
    ("quest_progress", "player_id", ""),
    ("resonance_events", "player_id", ""),
    ("resonance", "player_id", ""),
    ("push_subscriptions", "player_id", ""),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true", help="刪除所有玩家")
    ap.add_argument("--prefix", help="只刪 device_id 以此開頭的玩家")
    args = ap.parse_args()

    if not args.all and not args.prefix:
        print("✗ 要 --all，或用 --prefix 指定範圍。不給範圍不會刪任何東西。")
        return 2

    where = "TRUE" if args.all else "device_id LIKE :pattern"
    params = {} if args.all else {"pattern": f"{args.prefix}%"}

    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        targets = conn.execute(
            text(f"SELECT player_id, device_id FROM players WHERE {where} ORDER BY created_at"),
            params,
        ).all()

    if not targets:
        print("沒有符合的玩家。")
        return 0

    print(f"符合條件的玩家 {len(targets)} 筆：")
    for _, device_id in targets:
        print(f"  {device_id}")

    ids = [t[0] for t in targets]

    with engine.connect() as conn:
        print("\n衍生資料：")
        for table, column, note in _DEPENDENTS:
            n = conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {column} = ANY(:ids)"), {"ids": ids}
            ).scalar()
            if n:
                print(f"  {table:32s} {n:5d} 筆  {note}")

    if args.dry_run:
        print("\n--dry-run：沒有刪除任何東西")
        return 0

    with engine.begin() as conn:
        total = 0
        for table, column, _ in _DEPENDENTS:
            n = conn.execute(
                text(f"DELETE FROM {table} WHERE {column} = ANY(:ids)"), {"ids": ids}
            ).rowcount
            total += n
            if n:
                print(f"刪除 {table} {n} 筆")
        n = conn.execute(
            text("DELETE FROM players WHERE player_id = ANY(:ids)"), {"ids": ids}
        ).rowcount
        print(f"刪除 players {n} 筆（衍生資料共 {total} 筆）")

    with engine.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM players")).scalar()
        orphan = conn.execute(
            text(
                """SELECT count(*) FROM brain.memory_embeddings m
                   WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.player_id = m.player_id)"""
            )
        ).scalar()
    print(f"\n剩餘玩家 {left} 筆")
    print(f"孤兒 memory_embeddings：{orphan} 筆" + ("（正常）" if orphan == 0 else " ⚠️"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
