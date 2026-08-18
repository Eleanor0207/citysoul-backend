"""從文化部 iCulture 開放資料抓地標的官方公開活動，寫進 `landmark_events`。

用法：
    python -m scripts.fetch_landmark_events --dry-run   # 只看抓到什麼，不寫
    python -m scripts.fetch_landmark_events             # 驗完再整批寫入
    python -m scripts.fetch_landmark_events --radius 500
    python -m scripts.fetch_landmark_events --from-file tmp/iculture.json

冪等：同一個 `event_id` 重跑是 UPDATE，不會長出第二列。

## 🔒 預設只抓白名單上的地標

`landmark_events.VERIFIED_VENUE_SPIRITS`，目前兩個：西門紅樓、松山文創。

理由不是保守，是**地理比對回答不了「這場活動是誰辦的」**：霞海城隍廟 42 公尺外
就是大稻埕戲苑，比西門紅樓到自己的劇場還近。完整的實測數字與推論寫在那個常數的
註解裡。

要看某個地標值不值得加進白名單：

    python -m scripts.fetch_landmark_events --dry-run --diagnose --all-spirits

看它最近的場館**是不是它自己**。`--all-spirits` 刻意只能配 `--dry-run`。

## 🔒 這支腳本不會放行任何內容

寫進去的 `active` 一律 false。放行只有 `scripts/review_landmark_events.py`
那條人工路徑（見 `landmark_events_service` 的 docstring）。

**不要因為「這個場館一直都沒問題」而在這裡加自動放行**——那正是審核閘會消失
的方式。

## 資料源與授權

    https://cloud.culture.tw/frontsite/trans/SearchShowAction.do?method=doFindTypeJ&category=all

免金鑰、每日更新、政府資料開放授權條款第 1 版（免費，需標示出處）。出處標示
由後端隨回應帶給客戶端（`landmark_events.SOURCE_LABELS`），不是寫死在 App 裡。

## ⚠️ 欄位名沒有對過實際回應

`cloud.culture.tw` 擋 robots，開發當下沒辦法先驗一次真實 payload，欄位名是照
data.gov.tw 的資料集說明寫的。所以 `--dry-run` 的第一件事就是把實際的 key 印
出來——**第一次跑很可能要照實際欄位補 `landmark_events._first()` 的別名清單**。
那是預期，不是失誤。

`--from-file` 存在也是為了這件事：把回應先存成檔案，反覆調欄位對應時不必一直
去打人家的服務。

## 為什麼是獨立腳本而不是 migration

活動是**內容**，不是 schema。同 `load_districts.py` 的理由：把會每天變動的資料
塞進 migration，等於讓資料的歷史跟 schema 的歷史綁在一起。

## 為什麼不引入排程套件

排程交給作業系統（Windows 工作排程器／cron）。APScheduler 之類的東西會讓
「排程有沒有在跑」變成應用程式內部狀態，而那在單一容器重啟時會靜默消失。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.modules.body.geo import haversine_distance_m
from app.modules.body import landmark_events as rules
from app.modules.body import landmark_events_service as service

ICULTURE_URL = (
    "https://cloud.culture.tw/frontsite/trans/SearchShowAction.do"
    "?method=doFindTypeJ&category=all"
)

# 開放資料是全國的，整包好幾 MB。逾時放寬一點——這是每天跑一次的批次作業，
# 不是玩家在等的請求。
FETCH_TIMEOUT_SECONDS = 60


def fetch_payload(url: str, *, from_file: str | None) -> list:
    if from_file:
        path = pathlib.Path(from_file)
        return json.loads(path.read_text(encoding="utf-8"))

    request = urllib.request.Request(
        url,
        headers={
            # 有些政府網站對沒有 UA 的請求直接回 403。帶一個說得出自己是誰的
            # 字串，比假裝成瀏覽器誠實，出問題時對方也找得到我們。
            "User-Agent": "citysoul-taipei/1.0 (landmark daily events)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def describe_shape(payload) -> None:
    """
    把實際回應的欄位名印出來。見 docstring 的欄位名警告——這是調對應的依據。
    """
    items = payload
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("Data") or []

    if not isinstance(items, list) or not items:
        print("⚠️  回應不是非空陣列，無法推斷欄位。前 200 個字元：")
        print(f"    {str(payload)[:200]}")
        return

    first = items[0]
    print(f"\n實際回應共 {len(items)} 筆。第一筆的欄位：")
    for key in sorted(first.keys()) if isinstance(first, dict) else []:
        value = first[key]
        preview = str(value)
        if len(preview) > 60:
            preview = preview[:60] + "…"
        print(f"  {key:28s} {preview}")

    show_info = first.get("showInfo") if isinstance(first, dict) else None
    if isinstance(show_info, list) and show_info and isinstance(show_info[0], dict):
        print("\n第一筆的 showInfo[0] 欄位：")
        for key in sorted(show_info[0]):
            print(f"  {key:28s} {str(show_info[0][key])[:60]}")


def diagnose(candidates, records) -> None:
    """
    每個地標離它最近的三個場館與距離。

    「某個地標 0 筆」有三種完全不同的原因，光看筆數分不出來：

    1. **我們存的經緯度是錯的** —— 最近的場館距離會是好幾公里，而且那個場館的
       名字跟地標本身無關
    2. **半徑太小** —— 最近的場館就是它自己（名字對得上），只是差了幾十公尺
    3. **這個地標真的沒有藝文活動** —— 龍山寺、剝皮寮這種本來就不太會出現在
       文化部的藝文活動資料集裡

    印出「最近的是誰、差多遠」就把三種分開了。這比反覆試 `--radius` 有效率——
    調半徑只會告訴你「還是 0」，不會告訴你為什麼。
    """
    print("\n診斷：每個地標最近的三個場館")
    for spirit_id, lat, lon in candidates:
        scored = sorted(
            (
                (
                    haversine_distance_m(lat, lon, r["latitude"], r["longitude"]),
                    r.get("venue_name") or "（無場館名）",
                )
                for r in records
            ),
            key=lambda x: x[0],
        )[:3]

        print(f"  {spirit_id}")
        for distance, venue in scored:
            print(f"      {distance:8.0f} m  {venue[:36]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只驗證與列出，不寫入")
    ap.add_argument("--url", default=ICULTURE_URL)
    ap.add_argument("--from-file", help="改讀本地 JSON 檔（調欄位對應時用）")
    ap.add_argument(
        "--radius",
        type=float,
        default=rules.DEFAULT_MATCH_RADIUS_M,
        help=f"場館比對半徑（公尺），預設 {rules.DEFAULT_MATCH_RADIUS_M:.0f}",
    )
    ap.add_argument(
        "--show-unmatched",
        type=int,
        default=0,
        metavar="N",
        help="列出前 N 筆沒對上的場館，用來判斷半徑設得對不對",
    )
    ap.add_argument(
        "--diagnose",
        action="store_true",
        help="對每個地標印出離它最近的三個場館與距離（診斷 0 筆的原因）",
    )
    ap.add_argument(
        "--spirits",
        help=(
            "只抓這些地標（逗號分隔）。預設是 landmark_events.VERIFIED_VENUE_SPIRITS："
            + "、".join(rules.VERIFIED_VENUE_SPIRITS)
        ),
    )
    ap.add_argument(
        "--all-spirits",
        action="store_true",
        help="關掉白名單，對所有上架地標比對。**診斷用**，不要拿來寫入正式資料",
    )
    args = ap.parse_args()

    if args.all_spirits and not args.dry_run:
        # 關掉白名單就等於讓每個地標開始講隔壁機構的活動。要看可以，寫進去不行。
        print("✗ --all-spirits 只能配 --dry-run。理由見 landmark_events.VERIFIED_VENUE_SPIRITS")
        return 1

    print(f"抓取 {args.from_file or args.url}")
    try:
        payload = fetch_payload(args.url, from_file=args.from_file)
    except Exception as exc:  # noqa: BLE001
        # 抓不到不是程式壞了，是外部服務或網路的問題。訊息要說清楚，因為這支
        # 是排程跑的，人看到的只有 log。
        print(f"✗ 抓取失敗：{type(exc).__name__}: {exc}")
        return 1

    if args.dry_run:
        describe_shape(payload)

    records = rules.normalize_iculture(payload)
    print(f"\n正規化後 {len(records)} 筆（一個場館一筆，座標不合理的已剔除）")
    if not records:
        print("✗ 一筆都沒有。八成是欄位名對不上——用 --dry-run 看實際欄位。")
        return 1

    with SessionLocal() as db:
        if args.all_spirits:
            only = ()  # 空的 = 不套白名單
        elif args.spirits:
            only = tuple(s.strip() for s in args.spirits.split(",") if s.strip())
        else:
            only = None  # None = 用預設白名單

        candidates = service.spirit_candidates(db, only=only)
        if not candidates:
            print("✗ 沒有符合的地標。白名單裡的 spirit_id 打錯了，或那些地標已下架")
            return 1

        scope = "全部上架地標（診斷模式）" if args.all_spirits else "白名單"
        print(f"比對對象：{len(candidates)} 個地標（{scope}），半徑 {args.radius:.0f} m")

        matched, unmatched = rules.match_to_spirits(
            records, candidates, radius_m=args.radius
        )

        by_spirit = Counter(row["spirit_id"] for row in matched)
        print(f"\n對上 {len(matched)} 筆，沒對上 {len(unmatched)} 筆")
        for spirit_id, _lat, _lon in candidates:
            count = by_spirit.get(spirit_id, 0)
            mark = "  " if count else "⚠️"
            print(f"  {mark} {spirit_id:34s} {count:4d} 筆")

        # 沒對上的數量是判斷半徑的唯一依據。全部沒對上通常代表座標欄位讀錯了
        # （或經緯度寫反），而不是台北真的沒有活動。
        if args.show_unmatched:
            print(f"\n沒對上的前 {args.show_unmatched} 筆：")
            for row in unmatched[: args.show_unmatched]:
                print(
                    f"  {row['latitude']:.5f}, {row['longitude']:.5f}"
                    f"  {(row['venue_name'] or '（無場館名）')[:24]:24s} {row['title'][:30]}"
                )

        if args.diagnose:
            diagnose(candidates, records)

        if not matched:
            print("\n✗ 一筆都沒對上，不寫入。先確認座標欄位讀對了、半徑夠大。")
            return 1

        if args.dry_run:
            print("\n--dry-run：沒有寫入資料庫")
            return 0

        counts = service.upsert_events(
            db, matched, fetched_at=datetime.now(timezone.utc)
        )

    print(
        f"\n寫入完成：新增 {counts['inserted']} 筆、更新 {counts['updated']} 筆"
        f"（其中 {counts['pending_after_update']} 筆更新後仍未放行）"
    )
    print("🔒 全部都是未審核狀態。用 scripts.review_landmark_events 逐筆放行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
