import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import models, schemas
from app.modules.body.anticheat import run_observation_checks
from app.modules.body.auth import require_session_token
from app.modules.body.encounter_tokens import issue_encounter_token
from app.modules.body.geo import haversine_distance_m
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


@router.post("/summon", response_model=schemas.SummonResponse)
def summon(
    payload: schemas.SummonRequest,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S2．在場驗證與召喚（SDD 第7.1／8.4節）。

    過期未完成任務的失敗判定與回應中的 quest 欄位還沒做（ticket #15），
    要等 quest_progress 表建立後才會回來補。
    """
    spirit = db.query(models.Spirit).filter_by(place_id=payload.spirit_id).first()

    # is_active=false 跟不存在一律回 404（對齊 SDD 第8.2節的 spirits 查詢）：
    # 下架的靈魂對玩家來說就是不存在，不需要區分成兩種錯誤讓人推敲。
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    distance_m = haversine_distance_m(
        payload.latitude, payload.longitude, spirit.latitude, spirit.longitude
    )

    # SDD 第7.1節：distance <= summon_radius_m 才在場成立（含邊界值）。
    # 半徑固定不動態放寬（第7節決策1）。
    if distance_m > spirit.summon_radius_m:
        raise HTTPException(status_code=403, detail="not within summon radius")

    # S3 觀察期（ticket #14）：只記 log，不阻擋，也不會讓例外往外拋。
    # 刻意放在在場驗證通過之後——CONTEXT.md 對「在場紀錄」的定義是「完成在場
    # 驗證所需的地標、時間與反作弊結果」，沒通過驗證的請求不構成在場紀錄。
    run_observation_checks(
        db,
        player_id=player_id,
        spirit=spirit,
        is_mock_location=payload.is_mock_location,
        gps_accuracy_m=payload.gps_accuracy_m,
    )

    return schemas.SummonResponse(
        encounter_token=issue_encounter_token(player_id, spirit.place_id),
        spirit_id=spirit.place_id,
    )


@router.get("/spirits/{place_id}", response_model=schemas.SpiritResponse)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """對應對外 API 清單：GET /api/v1/spirits/{placeId}（Sprint1 先只回基本資料）。"""
    spirit = db.query(models.Spirit).filter_by(place_id=place_id).first()
    if not spirit:
        raise HTTPException(status_code=404, detail="spirit not found")
    return spirit
