from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import models, schemas
from app.modules.body.tokens import issue_session_token

router = APIRouter(prefix="/api/v1", tags=["body"])


@router.post("/players", response_model=schemas.PlayerResponse)
def create_or_get_player(payload: schemas.PlayerCreateRequest, db: Session = Depends(get_db)):
    """
    S6．匿名玩家身分系統。

    同一個 device_id 重複呼叫要回傳同一個 player（不能每次開 App 都產生新玩家），
    這是「App 首次啟動產生、保存於裝置安全儲存區」這條定義的自然結果——
    裝置端只要遺失 install_id 才會走到「升級綁定帳號」的路徑，不在這支 API 範圍內。

    每次呼叫（不論新建或既有玩家）都重新核發一張 session token（SDD第6節：
    重複呼叫「重新核發」，順便延長效期，玩家不會感覺到任何登入過期）。
    """
    existing = db.query(models.Player).filter_by(device_id=payload.device_id).first()
    player = existing

    if player is None:
        player = models.Player(device_id=payload.device_id)
        db.add(player)
        db.commit()
        db.refresh(player)

    session_token = issue_session_token(player.player_id)

    return schemas.PlayerResponse(
        player_id=player.player_id,
        device_id=player.device_id,
        account_id=player.account_id,
        created_at=player.created_at,
        session_token=session_token,
    )


@router.get("/spirits/{place_id}", response_model=schemas.SpiritResponse)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """對應對外 API 清單：GET /api/v1/spirits/{placeId}（Sprint1 先只回基本資料）。"""
    spirit = db.query(models.Spirit).filter_by(place_id=place_id).first()
    if not spirit:
        raise HTTPException(status_code=404, detail="spirit not found")
    return spirit
