"""每日排程：為所有上架地標生成今天的當日情境。

用法：
    python -m scripts.refresh_daily_events
    python -m scripts.refresh_daily_events --spirit longshan_temple
    python -m scripts.refresh_daily_events --dry-run    # 只列出會用哪場活動

排在**抓取與審核之後**跑：

    fetch_landmark_events  →  review_landmark_events  →  refresh_daily_events

## 為什麼交給作業系統排程

Windows 工作排程器／cron 呼叫這支就好，不引入 APScheduler 之類的套件。理由是
「排程有沒有在跑」應該是作業系統看得到的狀態；放進應用程式的話，容器重啟後
它會靜默消失，而我們只會在玩家發現內容停在三天前時才知道。

## 一個地標失敗不該讓整批停下來

九個地標各自獨立。某個地標的模型呼叫逾時，其他八個仍然該生成——所以這裡逐個
catch，最後用離開碼回報有幾個失敗。

⚠️ 這跟 `fetch_landmark_events` 的「整批一個交易」不同，兩者不矛盾：那裡是
**一批資料的完整性**（半套的匯入沒有人想收拾），這裡是**九件互相獨立的工作**。

## 沒有活動時仍然要跑

大多數地標大多數日子沒有展覽。B9 拿到空輸入會回人工預寫保底，而那**仍然要寫
進快取**——不寫的話端點每次都得走「完全沒有快取」的路徑，排程看起來像從沒
成功過，真正的排程故障就被掩蓋了。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.modules.body import daily_event_service
from app.modules.body import landmark_events_service as events
from app.modules.body.models import Spirit
from app.modules.body.quests import taipei_today
from app.modules.brain.gemini import VertexAIGeminiClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spirit", help="只跑某個地標")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出每個地標今天會用哪場活動，不呼叫模型、不寫快取",
    )
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    today = taipei_today(now)
    print(f"台北今天 {today.isoformat()}")

    with SessionLocal() as db:
        query = db.query(Spirit).filter(Spirit.is_active.is_(True))
        if args.spirit:
            query = query.filter(Spirit.spirit_id == args.spirit)
        spirits = query.order_by(Spirit.spirit_id).all()

        if not spirits:
            print("✗ 沒有符合的上架地標")
            return 1

        if args.dry_run:
            for spirit in spirits:
                row = events.featured_event(
                    db, spirit_id=spirit.spirit_id, on_date=today
                )
                if row is None:
                    # 常態，不是錯誤。標成「—」而不是警告符號。
                    print(f"  —  {spirit.spirit_id:34s} 今天沒有已放行的活動")
                else:
                    print(f"  ●  {spirit.spirit_id:34s} {row.title[:40]}")
            print("\n--dry-run：沒有呼叫模型、沒有寫入快取")
            return 0

        # ⚠️ 模型 client 在迴圈外建一次。每個地標各建一個的話，憑證與連線會被
        # 重複初始化九次，而那在 Cloud Run 冷啟動時是看得出來的延遲。
        client = VertexAIGeminiClient()

        failed: list[str] = []
        with_event = 0
        for spirit in spirits:
            try:
                row = daily_event_service.refresh_from_landmark_events(
                    db, client, place_id=spirit.spirit_id, now=now
                )
            except Exception as exc:  # noqa: BLE001
                # 見 docstring：一個地標失敗不該讓其他八個沒有內容。
                print(f"  ✗ {spirit.spirit_id:34s} {type(exc).__name__}: {exc}")
                failed.append(spirit.spirit_id)
                continue

            event = row.content.get("official_event")
            if event:
                with_event += 1
                print(f"  ● {spirit.spirit_id:34s} {event['title'][:40]}")
            else:
                print(f"  — {spirit.spirit_id:34s} （保底敘事）")

    print(
        f"\n完成 {len(spirits) - len(failed)}/{len(spirits)} 個地標，"
        f"其中 {with_event} 個有活動可推"
    )
    if failed:
        print(f"失敗：{'、'.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
