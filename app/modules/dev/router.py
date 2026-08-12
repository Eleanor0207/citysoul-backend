"""
開發測試主控台。

一個純瀏覽器頁面，用來手動走完「選身分 → 選地標 → 召喚 → 對話」這條鏈，
不需要 Unity client 也不需要真的站在龍山寺前面。

## 這裡的東西不是產品的一部分

三個原則，違反其中任何一條都表示東西放錯地方了：

1. **不動 `/api/v1` 的契約。** 這個 router 掛在 `/dev`，而且整組
   `include_in_schema=False`——它不會出現在 `contracts/openapi.json` 裡，
   `citysoul-client` 不會看到它，contract-gate 也不會因為它而變紅。
2. **不提供正式 API 沒有的權限。** 這裡只回「有哪些玩家、有哪些地標」，
   而拿到 session token 仍然要走真正的 `POST /api/v1/players`。主控台能做的
   事，任何人拿 curl 都能做。
3. **不繞過任何驗證。** 頁面上的召喚一樣打 `/api/v1/summon`、一樣要通過在場
   驗證。差別只在座標是從地標本身帶進去的，省掉手key經緯度。

## 為什麼需要「列出玩家／地標」這兩支

玩家 API 是以裝置為中心的：App 知道自己的 `device_id`，所以正式 API 沒有、
也不該有「列出所有玩家」。但測試的人要能在幾個身分之間切換來看共鳴值差異，
手上沒有那份清單就只能自己記 device_id。地標同理——正式 API 只有
`GET /spirits/{placeId}`，因為客戶端是從地圖上點選的。

這兩支的存在理由是「測試時人要看得到有什麼」，不是「產品缺了這個功能」。
不要因為主控台有，就把它們搬進 `/api/v1`。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import models

router = APIRouter(prefix="/dev", tags=["dev"], include_in_schema=False)

_CONSOLE_HTML = Path(__file__).with_name("console.html")


@router.get("/console", response_class=HTMLResponse)
def console() -> HTMLResponse:
    """
    主控台頁面。

    每次請求都重讀檔案而不是啟動時讀進記憶體：改 HTML 之後重新整理瀏覽器就
    看得到，不必重啟服務。頁面是給人開的，一次多讀一個檔案不值得拿來換
    「改一行要重啟」。
    """
    return HTMLResponse(_CONSOLE_HTML.read_text(encoding="utf-8"))


@router.get("/players")
def list_players(db: Session = Depends(get_db)) -> list[dict]:
    """
    最近建立的玩家，新的在前。

    回傳 `device_id` 是刻意的：主控台要切換身分時，走的是正式的
    `POST /api/v1/players`（同一個 device_id 會拿回同一個玩家與新的 session
    token）。少了它，切換身分就只能靠這裡發 token，那才是繞過驗證。
    """
    rows = (
        db.query(models.Player)
        .order_by(models.Player.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "player_id": str(row.player_id),
            "device_id": row.device_id,
            "display_name": row.display_name,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/spirits")
def list_spirits(db: Session = Depends(get_db)) -> list[dict]:
    """
    可選的地標清單。

    含經緯度與兩個半徑：主控台要用它們算出「站在召喚範圍內」的假座標，
    否則每次測試都要自己查龍山寺的緯度。**下架的靈魂也會出現**（帶
    `is_active`），因為測試的人需要看得到「為什麼這個地標打 404」。
    """
    rows = db.query(models.Spirit).order_by(models.Spirit.spirit_id).all()
    return [
        {
            "spirit_id": row.spirit_id,
            "display_name": row.display_name,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "summon_radius_m": row.summon_radius_meters,
            "sense_radius_m": row.sense_radius_meters,
            "is_active": row.is_active,
        }
        for row in rows
    ]
