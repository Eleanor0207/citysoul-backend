"""
Encounter Token 核發（S2，對應 SDD 第6節）。

這支模組刻意跟 `tokens.py`（Session Token）完全分開：不同檔案、不同金鑰
（`settings.encounter_token_secret`）、不同 `purpose` claim、不共用任何
編碼／解碼函式。SDD 第6節明文要求「三種token驗證邏輯必須用不同中介層區分，
不可共用同一套驗證邏輯」——因為一旦共用，玩家就能拿 90 天的 session token
冒充 15 分鐘的相遇憑證，「在場」這件事就形同虛設。

刻意不含 `jti`（SDD 第6節：Encounter Token 不查資料庫、不做撤銷名單），
這跟 Session Token 為了保證同秒核發唯一性而加 jti 的理由不同，不要照抄過去。
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

# SDD 第6節：exp - iat 固定 900 秒，不因任何情境放寬。
ENCOUNTER_TOKEN_EXPIRE_SECONDS = 900
ALGORITHM = "HS256"


def issue_encounter_token(player_id: uuid.UUID | str, spirit_id: str) -> str:
    """
    在場驗證通過後核發 15 分鐘效期的相遇憑證。

    payload 刻意只放 `sub`／`spirit_id`／`purpose`／`iat`／`exp`——
    特別是**不放任何 GPS 座標**（SDD 第6節表格「內含GPS座標：否」）。
    憑證本身只證明「這個玩家在這個時間點通過了這個地標的在場驗證」，
    不攜帶、也不需要攜帶當初驗證用的座標。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(player_id),
        "spirit_id": spirit_id,
        "purpose": "encounter",
        "iat": now,
        "exp": now + timedelta(seconds=ENCOUNTER_TOKEN_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, settings.encounter_token_secret, algorithm=ALGORITHM)
