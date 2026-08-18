"""審核 `landmark_events`：看待審清單、逐筆放行或收回。

用法：
    python -m scripts.review_landmark_events                     # 列出待審
    python -m scripts.review_landmark_events --spirit longshan_temple
    python -m scripts.review_landmark_events --approve iculture:123:0 --reviewer AL
    python -m scripts.review_landmark_events --revoke iculture:123:0
    python -m scripts.review_landmark_events --list-active

## 🔒 這是 active 唯一會變成 true 的路徑

`scripts/fetch_landmark_events.py` 只寫內容，寫進去一律未審核。跟
`brain.districts`（0018）與 `brain.character_personas` 同一個慣例：**沒有任何
自動路徑會放行內容**。

## 為什麼要有人審

抓回來的是政府開放資料、由場館自報，符合 SDD 白名單第二類「地標官方公開活動」
——問題不在來源可不可信，在於我們事先不知道抓到什麼：

- 場館報錯期程（展期填成去年）
- 活動性質跟地標調性不合：龍山寺三百公尺內辦的搖滾演唱會，haversine 會對上，
  但把它掛在龍山寺的靈魂底下講出來是另一回事
- 單純的測試資料

這三種都不是資料源的錯，也不是程式能判斷的。

## --reviewer 沒有預設值

預設成 `system` 之類的東西，等於允許一個沒有人負責的簽名——那審核閘就只剩
形式。放行時一定要指名是誰放的。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.modules.body import landmark_events as rules
from app.modules.body import landmark_events_service as service
from app.modules.body.models import LandmarkEvent
from app.modules.body.quests import taipei_today


def _period(row: LandmarkEvent) -> str:
    start = row.start_date.isoformat() if row.start_date else "?"
    end = row.end_date.isoformat() if row.end_date else "?"
    return f"{start} ~ {end}"


def _state(row, *, today) -> str:
    """
    這一筆現在是什麼狀態。**三種，不是兩種。**

    ⚠️ 2026-08-18 這裡出過錯：原本寫成「不是正在進行就標過期」，結果 8/23 的
    音樂會被標成「已結束」——22 筆裡有 18 筆被誤標。審核的人照著清單看，會把
    整批還沒開演的節目跳過去。

    「未開始」跟「已結束」對審核的人是完全相反的意思，不能共用一個標籤。
    """
    if row.end_date is None:
        # 沒有結束日期的一律不會被推（見 rules.is_featurable_on），所以標出來——
        # 審了也沒有用。
        return "無期程"
    if today > row.end_date:
        return "已結束"
    if rules.is_running_on(row.start_date, row.end_date, today):
        return "進行中"
    if rules.is_featurable_on(row.start_date, row.end_date, today):
        # 還沒開演但已經進入前置期，放行之後就會開始推。
        return "即將推"
    return "未開始"


def _print_rows(rows, *, today) -> None:
    if not rows:
        print("（沒有符合的資料）")
        return

    for row in rows:
        # 已經結束的活動仍然會出現在待審清單裡——刪掉它們是另一支清理工作的事，
        # 不是審核的事。標出來讓人知道不必費心審它。
        print(f"{_state(row, today=today)} {row.event_id}")
        print(f"     {row.spirit_id:34s} {_period(row)}")
        print(f"     {row.title}")
        if row.venue_name:
            print(f"     場館：{row.venue_name}")
        if row.source_url:
            print(f"     {row.source_url}")
        if row.summary:
            print(f"     簡介：{row.summary[:70]}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spirit", help="只看某個地標")
    ap.add_argument("--approve", nargs="+", metavar="EVENT_ID", help="放行（可多筆）")
    ap.add_argument("--revoke", nargs="+", metavar="EVENT_ID", help="收回放行（可多筆）")
    ap.add_argument("--reviewer", help="放行者。--approve 時必填")
    ap.add_argument("--list-active", action="store_true", help="改列出已放行的")
    args = ap.parse_args()

    if args.approve and not args.reviewer:
        # 見 docstring：不給預設值是刻意的。
        print("✗ --approve 需要 --reviewer，審核要有人負責")
        return 1

    today = taipei_today(datetime.now(timezone.utc))

    with SessionLocal() as db:
        if args.approve:
            failed = []
            for event_id in args.approve:
                if service.approve(db, event_id, reviewer=args.reviewer):
                    print(f"✓ 放行 {event_id}")
                else:
                    failed.append(event_id)
            for event_id in failed:
                print(f"✗ 找不到 {event_id}")
            return 1 if failed else 0

        if args.revoke:
            failed = []
            for event_id in args.revoke:
                if service.revoke(db, event_id):
                    print(f"✓ 收回 {event_id}")
                else:
                    failed.append(event_id)
            for event_id in failed:
                print(f"✗ 找不到 {event_id}")
            return 1 if failed else 0

        if args.list_active:
            query = db.query(LandmarkEvent).filter(LandmarkEvent.active.is_(True))
            if args.spirit:
                query = query.filter(LandmarkEvent.spirit_id == args.spirit)
            rows = query.order_by(
                LandmarkEvent.spirit_id, LandmarkEvent.end_date, LandmarkEvent.event_id
            ).all()
            print(f"已放行 {len(rows)} 筆：\n")
            _print_rows(rows, today=today)
            return 0

        rows = service.pending_events(db, spirit_id=args.spirit)
        print(f"待審 {len(rows)} 筆（台北今天 {today.isoformat()}）")
        print(
            f"狀態：進行中／即將推（{rules.FEATURE_LEAD_DAYS} 天內開演，放行就會推）"
            "／未開始（還太早）／已結束／無期程（推不出去）\n"
        )
        _print_rows(rows, today=today)

    # ⚠️ cmd 會把 < > 當成重新導向。這裡不寫尖括號，免得有人整行貼上去。
    print("放行：python -m scripts.review_landmark_events --approve 事件ID --reviewer 你的名字")
    print("多筆可以一次給：--approve 事件ID1 事件ID2 --reviewer 你的名字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
