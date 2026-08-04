"""
Session Token 驗證中介層（SDD 第6節）。

這支只負責「證明我是哪個玩家」這一種 token。Encounter／Sense Token 的驗證
之後要各自寫成獨立的 dependency，**不要**把它們塞進這裡共用同一個函式——
SDD 第6節明文要求三種 token 用不同中介層區分。這裡多寫一次驗證邏輯是刻意的
重複，不是可以順手抽出來的共用程式碼。
"""
import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.modules.body.tokens import ALGORITHM

# auto_error=False：沒帶 Authorization header 時不要讓 FastAPI 直接回 403，
# 我們要自己回 401（驗收標準：沒帶或無效都回 401）。
_bearer = HTTPBearer(auto_error=False)


def require_session_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> uuid.UUID:
    """驗證 Bearer session token，回傳 player_id；沒帶或無效一律 401。"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing session token")

    try:
        decoded = jwt.decode(
            credentials.credentials,
            settings.session_token_secret,
            algorithms=[ALGORITHM],
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid session token")

    # 拿 encounter/sense token 來冒充 session token 時，簽章金鑰不同本來就會
    # 先被上面擋下；這裡再檢查一次 purpose，是為了讓「token 種類」這件事在
    # 程式碼裡是顯式的，而不是只靠金鑰湊巧不同才成立。
    if decoded.get("purpose") != "session":
        raise HTTPException(status_code=401, detail="invalid session token")

    try:
        return uuid.UUID(decoded["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="invalid session token")
