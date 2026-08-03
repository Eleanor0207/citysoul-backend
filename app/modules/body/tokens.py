"""
Session Token 核發（S6，對應 SDD 第6節）。

刻意跟未來的 Encounter Token／Sense Token 分開實作、分開簽章金鑰
（見 app.core.config.Settings.session_token_secret），避免共用同一套
驗證邏輯——玩家不應該拿 90 天的 session token 冒充 15 分鐘的相遇憑證，
反之亦然。這支模組只處理 session token 這一種。
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

SESSION_TOKEN_EXPIRE_DAYS = 90
ALGORITHM = "HS256"


def issue_session_token(player_id: uuid.UUID | str) -> str:
    """
    核發 90 天效期的 session token。
    建立玩家或同一 device_id 重複呼叫時都會呼叫這支函式核發新的一張
    （見 SDD 第6節：重複呼叫「重新核發」token，順便延長效期）。
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(player_id),
        "purpose": "session",
        "iat": now,
        "exp": now + timedelta(days=SESSION_TOKEN_EXPIRE_DAYS),
        # 每次核發都給一個獨立的 jti，確保同一秒內重複核發時 token 字串不同
        # （純粹用來保證核發的唯一性，不做撤銷名單查詢）。
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.session_token_secret, algorithm=ALGORITHM)
