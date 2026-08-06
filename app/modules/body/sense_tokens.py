"""
Sense Token 核發（S2-new，對應 SDD 第6節／第8.3節）。

這是第三支獨立的 token 模組，跟 `tokens.py`（Session）與 `encounter_tokens.py`
（Encounter）**完全分開**：不同檔案、不同金鑰、不同 `purpose` claim、不共用
任何編碼／解碼函式。

看起來像是把 encounter_tokens.py 抄了一份改幾個字——**這是刻意的**。SDD 第6節
明文要求「三種 token 驗證邏輯必須用不同中介層區分，不可共用同一套驗證邏輯」。
抽成共用函式的誘惑很大，但那正是這條規則要擋的事：一旦共用，某天有人為了某個
情境放寬其中一種的條件（例如「過期一點點就算了吧」），三種 token 會一起被放寬，
而 code review 只會看到一個看似無害的參數變更。

三者的語意差異也不容混淆：
- Session（90 天）＝你是誰
- Sense（30 分鐘）＝你在這個地標的感應範圍內（150m），可以隔空聊天
- Encounter（15 分鐘）＝你真的到了現場（50m），可以召喚與挑戰任務
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException

from app.core.config import settings

# SDD 第6節：exp - iat 固定 1800 秒。刻意不跟 Encounter 的 900 秒共用常數
# ——改動其中一個的效期，不該連帶影響另一個。
SENSE_TOKEN_EXPIRE_SECONDS = 1800
ALGORITHM = "HS256"

# 跟 Encounter 一樣走獨立 header，不佔用 Authorization——那個位置給 session token。
# 對話端點會接受 Encounter 或 Sense 其中一張，兩者用不同 header 才分得開。
SENSE_TOKEN_HEADER = "X-Sense-Token"


def issue_sense_token(player_id: uuid.UUID | str, spirit_id: str) -> str:
    """
    進入感應範圍後核發 30 分鐘效期的感應憑證。

    payload 恰好只有 `sub`／`spirit_id`／`purpose`／`iat`／`exp`——
    **不放任何 GPS 座標**（SDD 第6節表格「內含GPS座標：否」）。憑證只證明
    「這個玩家在這個時間點通過了這個地標的感應範圍驗證」，當初用來驗證的座標
    在驗證完成的那一刻就該消失，不該被 token 夾帶著到處走。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(player_id),
        "spirit_id": spirit_id,
        "purpose": "sense",
        "iat": now,
        "exp": now + timedelta(seconds=SENSE_TOKEN_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, settings.sense_token_secret, algorithm=ALGORITHM)


def require_sense_token(spirit_id: str, raw_token: str | None) -> uuid.UUID:
    """
    驗證感應憑證，回傳 player_id。

    `spirit_id` 不符時回 403 而非 401：憑證本身是有效的，只是不適用於這個地標
    （比照 encounter 的處理，SDD 第8.5節錯誤碼定義）。
    """
    if not raw_token:
        raise HTTPException(status_code=401, detail="missing sense token")

    try:
        decoded = jwt.decode(raw_token, settings.sense_token_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid sense token")

    if decoded.get("purpose") != "sense":
        raise HTTPException(status_code=401, detail="invalid sense token")

    if decoded.get("spirit_id") != spirit_id:
        raise HTTPException(status_code=403, detail="sense token is for a different spirit")

    try:
        return uuid.UUID(decoded["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid sense token")
