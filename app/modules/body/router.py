import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import models, schemas
from app.modules.body.anticheat import run_observation_checks
from app.modules.body.auth import require_session_token
from app.modules.body.daily_event import get_daily_event_for_spirit
from app.modules.body.encounter_tokens import (
    ENCOUNTER_TOKEN_HEADER,
    issue_encounter_token,
    require_encounter_token,
)
from app.modules.body.geo import haversine_distance_m
from app.modules.body.quests import complete_quest, evaluate_on_summon, spirit_id_for_quest
from app.modules.body.quota import default_tier_id
from app.modules.body.sense_tokens import issue_sense_token
from app.modules.body.tokens import issue_session_token
from app.modules.brain.gemini import GeminiClient, get_gemini_client
from app.modules.brain.greetings import match_canned_greeting
from app.modules.brain.quest_wrapper import generate_quest_wrapper
from app.modules.brain.unlock_story import generate_unlock_stories

# 未命中快速問候時的人工預寫台詞。
#
# CONTEXT.md：「無合格輸入或生成失敗時使用人工預寫台詞」。在 Gemini（#8）接上
# 之前，**所有**未命中都會走到這裡——這是刻意的，讓端到端流程在沒有 GCP 憑證的
# 情況下也能完整跑通。接上 B1 之後，這句話會退回它原本的角色：只在模型失敗時出現。
FALLBACK_REPLY = "（城市靈魂安靜地看著你）……這件事我還沒想清楚。要不要先跟我說說你眼前看到的？"

# #30：每支路由宣告它「實際會回傳」的錯誤碼，逐支對齊現況，不宣告理論上
# 可能但實作不會產生的碼。422（request 驗證失敗）不在這裡宣告——FastAPI
# 對每支有 body/path 參數的路由本來就會自動加上，形狀是它自己的
# `HTTPValidationError`（`detail` 是陣列），跟 `ErrorResponse`（`detail`
# 是字串）不是同一個模型，硬塞進來反而講錯契約。
_AUTH_401 = {401: {"model": schemas.ErrorResponse, "description": "Missing or invalid token"}}
_FORBIDDEN_403 = {403: {"model": schemas.ErrorResponse, "description": "Not within required radius or token mismatch"}}
_NOT_FOUND_404 = {404: {"model": schemas.ErrorResponse, "description": "Spirit not found or inactive"}}

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
        player = models.Player(
            device_id=payload.device_id,
            # 預設分級的真相是 usage_tiers.is_default，不是這裡寫死的字串。
            usage_tier_id=default_tier_id(db),
        )
        db.add(player)
        db.commit()
        db.refresh(player)

    session_token = issue_session_token(player.player_id)

    return schemas.PlayerResponse(
        player_id=player.player_id,
        device_id=player.device_id,
        account_id=player.auth_provider_id,
        created_at=player.created_at,
        session_token=session_token,
    )


@router.post(
    "/sense",
    response_model=schemas.SenseResponse,
    responses={**_AUTH_401, **_FORBIDDEN_403, **_NOT_FOUND_404},
)
def sense(
    payload: schemas.SenseRequest,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S2-new．感應範圍驗證（SDD 第8.3節）。

    跟 `/summon` 長得很像，但**行為上少了三件事**，而那三件事正是它存在的理由：

    1. **不跑 S3 防作弊觀察期。** 觀察期記的是「在場」，而 CONTEXT.md 對在場
       紀錄的定義是完成在場驗證所需的地標與時間——150 公尺外不構成在場。
    2. **不做任何任務判定、不核發任務**（SDD 第7.2節）。這是與 `/summon`
       最關鍵的行為差異：呼叫這支不會建立或修改任何 `quest_progress` 列。
    3. **不核發 encounter token。** 感應憑證換不到在場證明，玩家還是得走過去。

    共用的只有純函式（距離計算）與 session 驗證。token 的核發與驗證邏輯
    完全獨立，見 `sense_tokens.py` 的模組註解。
    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=payload.spirit_id).first()

    # 下架優先於距離判定：即使玩家就站在正中心，is_active=false 也是 404。
    # 對玩家來說，下架的靈魂跟不存在的靈魂沒有差別（對齊 /summon 的處理）。
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    distance_m = haversine_distance_m(
        payload.latitude, payload.longitude, spirit.latitude, spirit.longitude
    )

    # distance <= sense_radius_meters 才算進入感應範圍（含邊界值），比照第7.1節
    # 對召喚半徑的處理。
    if distance_m > spirit.sense_radius_meters:
        raise HTTPException(status_code=403, detail="not within sense radius")

    return schemas.SenseResponse(
        sense_token=issue_sense_token(player_id, spirit.spirit_id),
        spirit_id=spirit.spirit_id,
    )


@router.post(
    "/summon",
    response_model=schemas.SummonResponse,
    responses={**_AUTH_401, **_FORBIDDEN_403, **_NOT_FOUND_404},
)
def summon(
    payload: schemas.SummonRequest,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S2．在場驗證與召喚（SDD 第7.1／8.4節）。

    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=payload.spirit_id).first()

    # is_active=false 跟不存在一律回 404（對齊 SDD 第8.2節的 spirits 查詢）：
    # 下架的靈魂對玩家來說就是不存在，不需要區分成兩種錯誤讓人推敲。
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    distance_m = haversine_distance_m(
        payload.latitude, payload.longitude, spirit.latitude, spirit.longitude
    )

    # SDD 第7.1節：distance <= summon_radius_meters 才在場成立（含邊界值）。
    # 半徑固定不動態放寬（第7節決策1）。
    if distance_m > spirit.summon_radius_meters:
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

    # S4 任務狀態機（ticket #15）。即使今天的挑戰次數已用完，仍然照常核發
    # encounter_token——AC 明訂「在場驗證仍可通過（玩家可對話）」，被鎖住的
    # 只有任務挑戰，不是相遇本身。
    quest_state = evaluate_on_summon(db, player_id=player_id, spirit_id=spirit.spirit_id)

    return schemas.SummonResponse(
        encounter_token=issue_encounter_token(player_id, spirit.spirit_id),
        spirit_id=spirit.spirit_id,
        quest=schemas.QuestStateResponse(
            quest_id=quest_state.quest_id,
            status=quest_state.status,
            attempts_today=quest_state.attempts_today,
        ),
    )


@router.post(
    "/spirits/{place_id}/dialogue",
    response_model=schemas.DialogueResponse,
    responses={**_AUTH_401, **_FORBIDDEN_403, **_NOT_FOUND_404},
)
def dialogue(
    place_id: str,
    payload: schemas.DialogueRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
):
    """
    對話端點（**最小可用版**）。

    目前只走 B12 快速問候比對：命中回預寫台詞，未命中回人工預寫的 fallback。
    這讓「走到地標→召喚→對話→看到回應」這條路**在沒有 GCP 憑證的情況下就能
    完整跑通**——本機 Avast 的 TLS 攔截目前擋著 Vertex AI，不該連帶讓整條核心
    迴圈無法驗證。

    尚未接上、各有獨立 ticket 的部分：
    - 配額檢查（#32）
    - B4 安全邊界（#11）、B2 Prompt 組裝（#12）、B1 Gemini（#8）→ 見 #42／#45
    - B10 TTS 語音（#21）
    - B7 短期記憶寫入

    驗證沿用 SDD 第6節：Session Token（Authorization header）＋ Encounter Token
    （X-Encounter-Token header）。兩者用**不同的驗證邏輯**，不共用函式。
    """
    encounter_player_id = require_encounter_token(place_id, encounter_token)

    # 兩張憑證必須屬於同一個玩家。少了這道檢查，A 的 session 配上 B 的相遇憑證
    # 就能通過——那等於讓沒到現場的人借用別人的在場證明。
    if encounter_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    canned = match_canned_greeting(db, place_id, payload.user_input)
    if canned is not None:
        return schemas.DialogueResponse(reply_text=canned, source="canned")

    return schemas.DialogueResponse(reply_text=FALLBACK_REPLY, source="fallback")


@router.get(
    "/spirits/{place_id}",
    response_model=schemas.SpiritResponse,
    responses={**_NOT_FOUND_404},
)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """對應對外 API 清單：GET /api/v1/spirits/{placeId}。"""
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if not spirit:
        raise HTTPException(status_code=404, detail="spirit not found")

    # `orientation` 是巢狀物件，`from_attributes` 沒辦法從 ORM 的平面欄位
    # 自動長出來——改成明確建構，跟其他回應（PlayerResponse／SummonResponse）
    # 的寫法一致。
    return schemas.SpiritResponse(
        place_id=spirit.spirit_id,
        name=spirit.display_name,
        latitude=spirit.latitude,
        longitude=spirit.longitude,
        summon_radius_m=spirit.summon_radius_meters,
        sense_radius_m=spirit.sense_radius_meters,
        is_active=spirit.is_active,
        orientation=schemas.OrientationResponse(
            bearing_deg=spirit.bearing_deg,
            height_offset_m=spirit.height_offset_m,
        ),
    )


@router.post(
    "/quests/{quest_id}/complete",
    response_model=schemas.QuestCompleteResponse,
    responses={**_AUTH_401, **_FORBIDDEN_403, **_NOT_FOUND_404},
)
def complete_quest_endpoint(
    quest_id: str,
    payload: schemas.QuestCompleteRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini_client: GeminiClient = Depends(get_gemini_client),
):
    """
    S4／S5．任務完成、共鳴入帳與敘事包裝（SDD §7.5，#34 前半＋#43 後半）。

    需要 Session Token ＋ **Encounter Token**。持 Sense Token 者不可呼叫這支
    （SDD 第6節）——那張憑證只證明「在 150m 感應範圍內」，而挑戰任務要求
    真的到現場（50m）。這裡不接受 `X-Sense-Token`，沒有另外寫檢查：
    `require_encounter_token` 用的是 encounter 的金鑰與 `purpose` claim，
    sense token 本來就過不了。

    完成判定本身是**後端確定性規則，不由 LLM 判定**（CONTEXT.md）——那一段
    全在 `complete_quest` 裡，它碰不到腦袋模組。腦袋只在判定與寫入都結束
    之後才登場，負責把已經發生的事**包裝**成角色的話（#43，SDD §7.5 後半）。

    **呼叫順序硬規則（v2.1 §6.4）**：身體先寫完自己的表、再呼叫腦袋，不可
    顛倒——顛倒的話腦袋拿到的 stage 會跟資料庫不一致。`complete_quest` 回
    傳時該 commit 的都 commit 完了，底下的生成才開始。
    """
    spirit_id = spirit_id_for_quest(quest_id)
    if spirit_id is None:
        raise HTTPException(status_code=404, detail="quest not found")

    encounter_player_id = require_encounter_token(spirit_id, encounter_token)

    # 兩張憑證必須屬於同一個玩家（比照 dialogue 端點）。
    if encounter_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    # ── 身體：判定 → 寫表 → 入帳。到這行結束為止，腦袋一次都沒被碰到。
    result = complete_quest(
        db, player_id=session_player_id, spirit_id=spirit_id, quest_id=quest_id
    )

    # ── 腦袋：純包裝。任務已經完成、共鳴值已經入帳，這裡失敗都只會拿到
    #    回退台詞，不會讓玩家的進度消失（兩支生成函式都保證不拋例外）。
    #
    # 每個新解鎖的 stage 都各自生成一段（`generate_unlock_stories` 而不是
    # 只取最後一個）——#16 讓 `newly_unlocked_stages` 回 list 就是為了不讓
    # 中間那段靜默消失。§7.5 的回應只裝得下一個，所以帶回最高的那階，
    # 見 `schemas.QuestCompleteResponse` 的說明。
    stories = generate_unlock_stories(
        str(session_player_id),
        spirit_id,
        result.newly_unlocked_stages,
        gemini_client=gemini_client,
    )
    unlock_story = (
        schemas.UnlockStoryResponse(stage=stories[-1].stage, story_text=stories[-1].story_text)
        if stories
        else None
    )

    return schemas.QuestCompleteResponse(
        quest_wrapper_text=generate_quest_wrapper(
            str(session_player_id), quest_id, gemini_client=gemini_client
        ),
        resonance_value=result.resonance_value,
        unlock_story=unlock_story,
    )


@router.get(
    "/spirits/{place_id}/daily-event",
    response_model=schemas.DailyEventResponse,
    responses={**_NOT_FOUND_404},
)
def get_daily_event(place_id: str, db: Session = Depends(get_db)):
    """
    S10．當日情境查詢（#26）。**不需要任何 token**——公開世界狀態，跟
    `/summon`／`/sense` 不同，不是「這個玩家跟這個靈魂的關係」。

    地標不存在 → 404；地標存在但沒有今天的內容 → 仍是 200（保底鏈路見
    `daily_event.get_daily_event_for_spirit`），玩家不該因為排程延遲或
    失敗看到空畫面。
    """
    result = get_daily_event_for_spirit(db, place_id)
    if result is None:
        raise HTTPException(status_code=404, detail="spirit not found")

    return schemas.DailyEventResponse(
        place_id=result.place_id,
        event_date=result.event_date,
        narrative_text=result.narrative_text,
        source=result.source,
    )
