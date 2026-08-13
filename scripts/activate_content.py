"""把審核通過的人格卡／行政區基調上線。**這是人的動作，不是流程的一環。**

用法：
    python -m scripts.activate_content persona --character longshan_watcher \\
        --version 3 --reviewed-by "你的名字"
    python -m scripts.activate_content district --district wanhua \\
        --reviewed-by "你的名字"
    python -m scripts.activate_content status

## 為什麼要有這支腳本，而不是直接下 SQL

雲端資料庫沒有可以直接連的終端機——要跑一句 UPDATE 就得開 Cloud SQL Studio 或
架 proxy，兩者都繞過任何記錄。這支腳本讓「誰在什麼時候把什麼上線」這件事有一個
固定的入口，而且 `--reviewed-by` 是**必填**：沒有名字就不能上線。

## 為什麼它不在匯入器裡

匯入器會被排程、會被 CI 呼叫、會在部署流程裡自動跑。上線一份人格卡不該是那種
會自己發生的事——CONTEXT.md 對人格卡的定義是「經人工審核的角色定義」，而審核的
意思就是有人看過並且願意具名。

分成兩支腳本之後，「自動匯入」與「人工上線」在**指令層級**就是兩件事，不需要靠
誰記得不要在 CI 裡加上某個旗標。

## 換版是一個交易

partial unique index 保證一個角色同時只有一個生效版本，所以舊版必須先關、新版
才開得成。兩句 UPDATE 中間若有 commit，那個瞬間會是「沒有任何生效人格」——
剛好在那時對話的玩家會拿到 fallback。
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings


def show_status(conn) -> None:
    print("brain.character_personas：")
    for r in conn.execute(
        text(
            """SELECT character_id, version, active, reviewed_by, reviewed_at
               FROM brain.character_personas ORDER BY character_id, version"""
        )
    ).all():
        state = "生效中" if r[2] else "草稿  "
        when = r[4].strftime("%Y-%m-%d") if r[4] else "—"
        print(f"  {r[0]} v{r[1]}  {state}  {r[3]}  {when}")

    print("brain.districts：")
    for r in conn.execute(
        text(
            """SELECT district_id, name, active, reviewed_by
               FROM brain.districts ORDER BY district_id"""
        )
    ).all():
        state = "生效中" if r[2] else "草稿  "
        print(f"  {r[0]:10s} {r[1]:6s} {state}  {r[3] or '—'}")


def activate_persona(engine, character: str, version: int, reviewer: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """SELECT active FROM brain.character_personas
                   WHERE character_id = :c AND version = :v"""
            ),
            {"c": character, "v": version},
        ).one_or_none()
        if row is None:
            print(f"✗ {character} v{version} 不存在。先跑 scripts.import_personas")
            return 1
        if row[0]:
            print(f"{character} v{version} 已經是生效中，沒有動作")
            return 0

        # 見 docstring：先關舊版再開新版，同一個交易內完成。
        closed = conn.execute(
            text(
                """UPDATE brain.character_personas SET active = false
                   WHERE character_id = :c AND active"""
            ),
            {"c": character},
        ).rowcount
        conn.execute(
            text(
                """UPDATE brain.character_personas
                      SET active = true, reviewed_by = :who, reviewed_at = now()
                    WHERE character_id = :c AND version = :v"""
            ),
            {"c": character, "v": version, "who": reviewer},
        )
    print(f"✅ {character} v{version} 上線（關閉舊版 {closed} 筆），審核者 {reviewer}")
    return 0


def activate_district(engine, district: str, reviewer: str) -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """SELECT active, core_tone_descriptors IS NOT NULL
                   FROM brain.districts WHERE district_id = :d"""
            ),
            {"d": district},
        ).one_or_none()
        if row is None:
            print(f"✗ {district} 不存在")
            return 1
        if not row[1]:
            print(f"✗ {district} 沒有基調內容，沒有東西可以上線")
            return 1
        if row[0]:
            print(f"{district} 已經是生效中，沒有動作")
            return 0

        conn.execute(
            text(
                """UPDATE brain.districts
                      SET active = true, reviewed_by = :who, reviewed_at = now()
                    WHERE district_id = :d"""
            ),
            {"d": district, "who": reviewer},
        )
    print(f"✅ {district} 基調上線，審核者 {reviewer}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="what", required=True)

    p = sub.add_parser("persona")
    p.add_argument("--character", required=True)
    p.add_argument("--version", type=int, required=True)
    # 必填。沒有名字就不能上線，見 docstring。
    p.add_argument("--reviewed-by", required=True)

    d = sub.add_parser("district")
    d.add_argument("--district", required=True)
    d.add_argument("--reviewed-by", required=True)

    sub.add_parser("status")

    args = ap.parse_args()
    engine = create_engine(settings.database_url)

    if args.what == "status":
        with engine.connect() as conn:
            show_status(conn)
        return 0

    if args.what == "persona":
        code = activate_persona(engine, args.character, args.version, args.reviewed_by)
    else:
        code = activate_district(engine, args.district, args.reviewed_by)

    print()
    with engine.connect() as conn:
        show_status(conn)
    return code


if __name__ == "__main__":
    sys.exit(main())
