from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import append_session_turn
from app.modules.body import models, schemas
from app.modules.body.anticheat import run_observation_checks
from app.modules.body.auth import require_session_token
from app.modules.body.encounter_tokens import (
    ENCOUNTER_TOKEN_HEADER,
    issue_encounter_token,
    require_encounter_token,
)
from app.modules.body.geo import haversine_distance_m
from app.modules.body.quests import (
    MAX_DAILY_ATTEMPTS,
    STATUS_COMPLETED,
    STATUS_DAILY_LIMIT_REACHED,
    STATUS_IN_PROGRESS,
    evaluate_on_summon,
    taipei_today,
)
from app.modules.body.quota import consume_quota, default_tier_id
from app.modules.body.resonance import (
    AMOUNT_ENCOUNTER_COLLECTION,
    AMOUNT_QUEST,
    SOURCE_ENCOUNTER_COLLECTION,
    SOURCE_QUEST,
    apply_resonance,
    next_threshold,
    stage_for_value,
)
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token, require_sense_token
from app.modules.body.tokens import issue_session_token
import os

from app.modules.brain import models as brain_models
from app.modules.brain.daily_event import DEFAULT_DAILY_EVENT
from app.modules.brain.gemini import GeminiClient, VertexAIGeminiClient
from app.modules.brain.greetings import match_canned_greeting
from app.modules.brain.prompt_builder import assemble_prompt
from app.modules.brain.safety import FakeSafetyChecker, SafetyChecker
from app.modules.brain.tts import TTSClient, GoogleCloudTTSClient
from app.modules.brain.unlock_story import generate_quest_wrapper, generate_unlock_story
from app.modules.brain.vision import FakeLandmarkRecognizer, LandmarkRecognizer

# 未命中快速問候或模型失敗時的人工預寫台詞。
FALLBACK_REPLY = "（城市靈魂安靜地看著你）……這件事我還沒想清楚。要不要先跟我說說你眼前看到的？"

_safety_checker_instance: SafetyChecker = FakeSafetyChecker()
_landmark_recognizer_instance: LandmarkRecognizer = FakeLandmarkRecognizer()


def get_safety_checker() -> SafetyChecker:
    return _safety_checker_instance


def get_landmark_recognizer() -> LandmarkRecognizer:
    return _landmark_recognizer_instance

router = APIRouter(prefix="/api/v1", tags=["body"])


def get_gemini_client() -> GeminiClient:
    return VertexAIGeminiClient()


def get_tts_client() -> TTSClient:
    return GoogleCloudTTSClient()


@router.post("/players", response_model=schemas.PlayerResponse, responses={400: {"model": schemas.HTTPErrorResponse}})
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
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        403: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
    },
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
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        403: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
    },
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
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        403: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
        429: {"model": schemas.HTTPErrorResponse},
    },
)
def dialogue(
    place_id: str,
    payload: schemas.DialogueRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    sense_token: str | None = Header(default=None, alias=SENSE_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini_client: GeminiClient = Depends(get_gemini_client),
    tts_client: TTSClient = Depends(get_tts_client),
    safety_checker: SafetyChecker = Depends(get_safety_checker),
):
    """
    對話端點（完整版，v2.1 §13.5 Phase 3）。

    配額 (BE#32) -> B12 固定招呼 -> B4 安全檢查 (BE#11) -> B2 Prompt 組裝 (BE#12)
    -> B1 Gemini -> B10 TTS -> B7 短期記憶寫入
    """
    if not encounter_token and not sense_token:
        raise HTTPException(status_code=401, detail="missing token")

    token_player_id: uuid.UUID | None = None
    if encounter_token:
        token_player_id = require_encounter_token(place_id, encounter_token)
    elif sense_token:
        token_player_id = require_sense_token(place_id, sense_token)

    if token_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    # 1. 檢查並扣減每日對話配額 (BE#32)
    consume_quota(db, session_player_id, "dialogue_turns", 1)

    # 2. 快速問候語比對 (B12)
    canned = match_canned_greeting(db, place_id, payload.user_input)
    if canned is not None:
        tts_res = tts_client.synthesize(canned)
        try:
            append_session_turn(str(session_player_id), place_id, {"role": "user", "text": payload.user_input})
            append_session_turn(str(session_player_id), place_id, {"role": "assistant", "text": canned})
        except Exception:
            pass
        return schemas.DialogueResponse(reply_text=canned, source="canned", tts=tts_res)

    # 3. B4 角色安全邊界過濾 (BE#11)
    safety_res = safety_checker.check(payload.user_input)
    if not safety_res.is_safe:
        refusal = safety_res.refusal_reply or FALLBACK_REPLY
        tts_res = tts_client.synthesize(refusal)
        try:
            append_session_turn(str(session_player_id), place_id, {"role": "user", "text": payload.user_input})
            append_session_turn(str(session_player_id), place_id, {"role": "assistant", "text": refusal})
        except Exception:
            pass
        return schemas.DialogueResponse(reply_text=refusal, source="refusal", tts=tts_res)

    # 4. B2 Prompt 結構化組裝 (BE#12)
    sys_inst, user_turn = assemble_prompt(
        db,
        player_id=str(session_player_id),
        spirit_id=place_id,
        user_input=payload.user_input,
    )

    # 5. 呼叫 Gemini AI 模型生成 (B1)
    reply_text = gemini_client.generate(sys_inst, user_turn)
    source = "gemini" if reply_text != FALLBACK_REPLY else "fallback"

    # 6. 呼叫 TTS 語音合成 (B10)
    tts_res = tts_client.synthesize(reply_text)

    # 7. 寫入 Redis 短期對話紀錄 (B7)
    try:
        append_session_turn(str(session_player_id), place_id, {"role": "user", "text": payload.user_input})
        append_session_turn(str(session_player_id), place_id, {"role": "assistant", "text": reply_text})
    except Exception:
        pass

    return schemas.DialogueResponse(reply_text=reply_text, source=source, tts=tts_res)


@router.get(
    "/spirits/{place_id}",
    response_model=schemas.SpiritResponse,
    responses={404: {"model": schemas.HTTPErrorResponse}},
)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """對應對外 API 清單：GET /api/v1/spirits/{placeId}（Sprint1 先只回基本資料）。"""
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if not spirit:
        raise HTTPException(status_code=404, detail="spirit not found")
    return spirit


@router.get(
    "/quests/daily",
    response_model=schemas.DailyQuestsResponse,
    responses={401: {"model": schemas.HTTPErrorResponse}},
)
def get_daily_quests(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S4 / GET /api/v1/quests/daily 任務列表查詢（BE#33）。

    回傳該玩家目前的任務清單。
    - 依 Asia/Taipei 午夜日期自動處理次數重置。
    - 若當天嘗試已滿 3 次，status 標示為 daily_limit_reached。
    - 冷啟動（無任何列）回傳空陣列 []。
    """
    now = datetime.now(timezone.utc)
    today = taipei_today(now)

    progresses = db.query(models.QuestProgress).filter_by(player_id=player_id).all()
    results = []

    for p in progresses:
        if p.attempts_date != today:
            p.attempts_today = 0
            p.attempts_date = today
            db.commit()

        spirit_id = p.quest_id.split(":")[0] if ":" in p.quest_id else p.quest_id

        status = p.status
        if p.attempts_today >= MAX_DAILY_ATTEMPTS and p.status != STATUS_COMPLETED:
            status = STATUS_DAILY_LIMIT_REACHED

        results.append(
            schemas.QuestItemResponse(
                quest_id=p.quest_id,
                spirit_id=spirit_id,
                status=status,
                attempts_today=p.attempts_today,
            )
        )

    return schemas.DailyQuestsResponse(quests=results)


@router.post(
    "/quests/{quest_id}/complete",
    response_model=schemas.QuestCompleteResponse,
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        403: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
    },
)
def complete_quest(
    quest_id: str,
    payload: schemas.QuestCompleteRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini_client: GeminiClient = Depends(get_gemini_client),
):
    """
    S4 / S5 / POST /api/v1/quests/{quest_id}/complete 任務完成與共鸣入帳（BE#34/BE#43）。

    1. 需 Session Token ＋ Encounter Token (Sense Token 遭拒)。
    2. 驗證 Encounter Token 之持有人與地標符合。
    3. 標記 quest_progress.status = 'completed'。
    4. 呼叫 apply_resonance 入帳 +20 分。
    5. 硬規則 (v2.1 §6.4)：先完成 DB 寫入與點數結算，後呼叫腦袋生成敘事。
    6. 跨過門檻時生成並回傳 unlock_story，其餘時間為 null。
    """
    if not encounter_token:
        raise HTTPException(status_code=401, detail="missing encounter token")

    spirit_id = quest_id.split(":")[0] if ":" in quest_id else quest_id

    encounter_player_id = require_encounter_token(spirit_id, encounter_token)
    if encounter_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    progress = (
        db.query(models.QuestProgress)
        .filter_by(player_id=session_player_id, quest_id=quest_id)
        .first()
    )
    if progress is None:
        progress = models.QuestProgress(
            player_id=session_player_id,
            quest_id=quest_id,
            status=STATUS_COMPLETED,
            attempts_today=0,
            attempts_date=taipei_today(datetime.now(timezone.utc)),
            completed_at=datetime.now(timezone.utc),
        )
        db.add(progress)
    else:
        progress.status = STATUS_COMPLETED
        if progress.completed_at is None:
            progress.completed_at = datetime.now(timezone.utc)

    db.commit()

    res_result = apply_resonance(
        db,
        player_id=session_player_id,
        spirit_id=spirit_id,
        source_type=SOURCE_QUEST,
        source_id=quest_id,
        amount=AMOUNT_QUEST,
    )

    # 腦袋生成 (v2.1 §6.4 硬規則：必須在 DB 寫入後執行)
    wrapper_text = generate_quest_wrapper(str(session_player_id), quest_id, gemini_client)

    unlock_story_dict = None
    if res_result.newly_unlocked_stages:
        stories = [
            generate_unlock_story(str(session_player_id), spirit_id, stg, gemini_client)
            for stg in res_result.newly_unlocked_stages
        ]
        latest = stories[-1]
        unlock_story_dict = {"stage": latest.stage, "story_text": latest.story_text}

    return schemas.QuestCompleteResponse(
        quest_wrapper_text=wrapper_text,
        resonance_value=res_result.resonance_value,
        stage=res_result.stage,
        unlock_story=unlock_story_dict,
    )


@router.get(
    "/resonance/{spirit_id}",
    response_model=schemas.ResonanceProgressResponse,
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
    },
)
def get_resonance(
    spirit_id: str,
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S5 / GET /api/v1/resonance/{spirit_id} 共鳴進度查詢（BE#35）。

    1. 驗證 Session Token。
    2. 驗證 spirit 存在且 is_active=True (否則 404)。
    3. 動態依 resonance_value 計算 stage (0~3) 與 next_threshold (10/40/100)。
    4. 未曾互動的玩家回傳 0 點（HTTP 200）。
    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    row = (
        db.query(models.Resonance)
        .filter_by(player_id=session_player_id, spirit_id=spirit_id)
        .first()
    )
    val = row.resonance_value if row else 0

    return schemas.ResonanceProgressResponse(
        spirit_id=spirit_id,
        resonance_value=val,
        stage=stage_for_value(val),
        next_threshold=next_threshold(val),
    )


@router.get(
    "/profile",
    response_model=schemas.ProfileResponse,
    responses={401: {"model": schemas.HTTPErrorResponse}},
)
def get_profile(
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/profile 玩家個人頁彙總查詢（BE#36）。

    1. 需 Session Token (未帶或無效回傳 401)。
    2. 純身體資料表直查，不呼叫 Brain 腦袋模組。
    3. 一次回傳所有任務與所有靈魂的共鳴進度。
    4. 全新玩家回傳空陣列 {"quests":[], "resonance":[]} (HTTP 200)。
    """
    now = datetime.now(timezone.utc)
    today = taipei_today(now)

    progresses = db.query(models.QuestProgress).filter_by(player_id=session_player_id).all()
    quest_results = []
    for p in progresses:
        if p.attempts_date != today:
            p.attempts_today = 0
            p.attempts_date = today
            db.commit()

        spirit_id = p.quest_id.split(":")[0] if ":" in p.quest_id else p.quest_id

        status = p.status
        if p.attempts_today >= MAX_DAILY_ATTEMPTS and p.status != STATUS_COMPLETED:
            status = STATUS_DAILY_LIMIT_REACHED

        quest_results.append(
            schemas.QuestItemResponse(
                quest_id=p.quest_id,
                spirit_id=spirit_id,
                status=status,
                attempts_today=p.attempts_today,
            )
        )

    resonances = db.query(models.Resonance).filter_by(player_id=session_player_id).all()
    resonance_results = [
        schemas.ResonanceItemResponse(
            spirit_id=r.spirit_id,
            resonance_value=r.resonance_value,
            stage=stage_for_value(r.resonance_value),
        )
        for r in resonances
    ]

    return schemas.ProfileResponse(quests=quest_results, resonance=resonance_results)


@router.post(
    "/quests/{quest_id}/landmark-photo",
    response_model=schemas.LandmarkPhotoResponse,
    responses={
        401: {"model": schemas.HTTPErrorResponse},
        403: {"model": schemas.HTTPErrorResponse},
        404: {"model": schemas.HTTPErrorResponse},
        429: {"model": schemas.HTTPErrorResponse},
    },
)
def upload_landmark_photo(
    quest_id: str,
    file: UploadFile = File(...),
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
    landmark_recognizer: LandmarkRecognizer = Depends(get_landmark_recognizer),
):
    """
    S12 / POST /api/v1/quests/{quest_id}/landmark-photo 紀念照片地標辨識與相遇收藏（BE#44）。

    1. 需 Session Token ＋ Encounter Token (Sense Token 遭拒)。
    2. 先檢查並扣減地標辨識配額 (landmark_recognition_calls)；超額拋出 HTTP 429 且不呼叫辨識。
    3. 照片位元組僅於記憶體中讀取並過渡給辨識器，辨識完畢立即釋放，不落地儲存。
    4. 寫入 encounter_collections 表（含 UNIQUEConstraint 防重複加分），首度相遇收藏加值 +10 分。
    5. 辨識失敗時回傳 false，不拋出例外亦不阻擋任務完成。
    """
    if not encounter_token:
        raise HTTPException(status_code=401, detail="missing encounter token")

    spirit_id = quest_id.split(":")[0] if ":" in quest_id else quest_id

    encounter_player_id = require_encounter_token(spirit_id, encounter_token)
    if encounter_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    # 1. 檢查地標辨識配額 (BE#32/BE#44)
    consume_quota(db, session_player_id, "landmark_recognition_calls", 1)

    # 2. 記憶體中讀取照片 bytes 並交給辨識器
    image_bytes = file.file.read()
    recognized = False
    try:
        recognized = landmark_recognizer.recognize(image_bytes, spirit_id)
    finally:
        del image_bytes  # 立即拋棄位元組

    # 3. 相遇收藏與共鳴加值 (+10 分)
    collection = (
        db.query(models.EncounterCollection)
        .filter_by(player_id=session_player_id, place_id=spirit_id)
        .first()
    )
    if collection is None:
        collection = models.EncounterCollection(
            player_id=session_player_id,
            place_id=spirit_id,
            recognized_label=spirit_id if recognized else None,
        )
        db.add(collection)
        db.commit()

    res_result = apply_resonance(
        db,
        player_id=session_player_id,
        spirit_id=spirit_id,
        source_type=SOURCE_ENCOUNTER_COLLECTION,
        source_id=f"collection:{spirit_id}",
        amount=AMOUNT_ENCOUNTER_COLLECTION,
    )

    return schemas.LandmarkPhotoResponse(
        landmark_recognized=recognized,
        resonance_value=res_result.resonance_value,
        stage=res_result.stage,
    )


@router.get(
    "/spirits/{place_id}/daily-event",
    response_model=schemas.DailyEventResponse,
    responses={404: {"model": schemas.HTTPErrorResponse}},
)
def get_daily_event(place_id: str, db: Session = Depends(get_db)):
    """
    S10 / GET /api/v1/spirits/{placeId}/daily-event 當日情境查詢（BE#26）。

    1. 公開世界狀態端點，無需 Token 驗證。
    2. 驗證地標存在且 is_active=True (否則回傳 404)。
    3. 優先回傳當日快取；若無則回傳昨日快取；若皆無則回傳人工保底內容（均回傳 HTTP 200）。
    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    now = datetime.now(timezone.utc)
    today = taipei_today(now)
    yesterday = today - timedelta(days=1)

    cache_today = (
        db.query(models.DailyEventCache)
        .filter_by(place_id=place_id, event_date=today)
        .first()
    )
    if cache_today and cache_today.content:
        content = cache_today.content
        return schemas.DailyEventResponse(
            place_id=place_id,
            event_date=today,
            narrative_text=content.get("narrative_text", DEFAULT_DAILY_EVENT.narrative_text),
            theme_title=content.get("theme_title", DEFAULT_DAILY_EVENT.theme_title),
        )

    cache_yesterday = (
        db.query(models.DailyEventCache)
        .filter_by(place_id=place_id, event_date=yesterday)
        .first()
    )
    if cache_yesterday and cache_yesterday.content:
        content = cache_yesterday.content
        return schemas.DailyEventResponse(
            place_id=place_id,
            event_date=today,
            narrative_text=content.get("narrative_text", DEFAULT_DAILY_EVENT.narrative_text),
            theme_title=content.get("theme_title", DEFAULT_DAILY_EVENT.theme_title),
        )

    return schemas.DailyEventResponse(
        place_id=place_id,
        event_date=today,
        narrative_text=DEFAULT_DAILY_EVENT.narrative_text,
        theme_title=DEFAULT_DAILY_EVENT.theme_title,
    )


@router.get(
    "/players/me/memory-summary",
    response_model=schemas.MemorySummaryResponse,
    responses={401: {"model": schemas.HTTPErrorResponse}},
)
def get_player_memory_summary(
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    GET /api/v1/players/me/memory-summary 記憶摘要查詢（BE#37）。

    1. 需 Session Token 驗證 (401)。
    2. 嚴格過濾 player_id = session_player_id (隱私隔離)。
    3. 不含 768 維 Embedding 向量欄位，節省頻寬與防止數據外洩。
    4. 冷啟動回傳 {"memories": []} (HTTP 200)。
    """
    memories = (
        db.query(brain_models.MemoryEmbedding)
        .filter_by(player_id=session_player_id)
        .order_by(brain_models.MemoryEmbedding.created_at.desc())
        .all()
    )

    items = [
        schemas.MemoryItemResponse(
            memory_id=m.memory_id,
            spirit_id=m.spirit_id,
            summary_text=m.summary_text,
            created_at=m.created_at,
        )
        for m in memories
    ]
    return schemas.MemorySummaryResponse(memories=items)


@router.get(
    "/assets/{avatar_id}",
    response_model=schemas.AssetCatalogResponse,
    responses={404: {"model": schemas.HTTPErrorResponse}},
)
def get_asset_catalog(avatar_id: str, db: Session = Depends(get_db)):
    """
    GET /api/v1/assets/{avatarId} 素材 Catalog 與版本端點（BE#38）。

    1. 公開端點（免 Token 驗證）。
    2. 驗證地標/靈魂存在性，無效及非 is_active 靈魂回傳 404。
    3. 回傳 Unity Addressables 遠端 Catalog URL、Bundle URL 與穩定版本識別號。
    """
    spirit_id = avatar_id.replace("_spirit", "") if avatar_id.endswith("_spirit") else avatar_id
    spirit = (
        db.query(models.Spirit)
        .filter(models.Spirit.spirit_id.in_([avatar_id, spirit_id]))
        .first()
    )
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="avatar not found")

    base_cdn = os.getenv("CDN_BASE_URL", "https://cdn.citysoul.taipei/assets")
    updated_time = spirit.updated_at or datetime.now(timezone.utc)
    version = f"v1.0.{int(updated_time.timestamp())}"

    return schemas.AssetCatalogResponse(
        avatar_id=avatar_id,
        catalog_url=f"{base_cdn}/{avatar_id}/catalog.json",
        bundle_url=f"{base_cdn}/{avatar_id}/{avatar_id}.bundle",
        version=version,
        updated_at=updated_time,
    )


@router.post(
    "/players/me/push-subscription",
    response_model=schemas.PushSubscriptionResponse,
    responses={401: {"model": schemas.HTTPErrorResponse}},
)
def upsert_push_subscription(
    payload: schemas.PushSubscriptionRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    POST /api/v1/players/me/push-subscription 註冊/更新推播訂閱（BE#39）。

    1. 需 Session Token 驗證 (401)。
    2. 主鍵為 player_id，重複註冊更新 push_token 與 is_subscribed，避免重複列。
    """
    sub = (
        db.query(models.PushSubscription)
        .filter_by(player_id=session_player_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if sub is None:
        sub = models.PushSubscription(
            player_id=session_player_id,
            push_token=payload.push_token,
            is_subscribed=payload.is_subscribed,
            updated_at=now,
        )
        db.add(sub)
    else:
        sub.push_token = payload.push_token
        sub.is_subscribed = payload.is_subscribed
        sub.updated_at = now

    db.commit()
    db.refresh(sub)

    return schemas.PushSubscriptionResponse(
        player_id=sub.player_id,
        push_token=sub.push_token,
        is_subscribed=sub.is_subscribed,
        updated_at=sub.updated_at,
    )


@router.delete(
    "/players/me/push-subscription",
    response_model=schemas.PushSubscriptionResponse,
    responses={401: {"model": schemas.HTTPErrorResponse}},
)
def unsubscribe_push(
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    DELETE /api/v1/players/me/push-subscription 退訂推播（BE#39）。

    1. 需 Session Token 驗證 (401)。
    2. 將 is_subscribed 標記為 False，退訂後絕不安裝/送出推播。
    """
    sub = (
        db.query(models.PushSubscription)
        .filter_by(player_id=session_player_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if sub is None:
        sub = models.PushSubscription(
            player_id=session_player_id,
            push_token="",
            is_subscribed=False,
            updated_at=now,
        )
        db.add(sub)
    else:
        sub.is_subscribed = False
        sub.updated_at = now

    db.commit()
    db.refresh(sub)

    return schemas.PushSubscriptionResponse(
        player_id=sub.player_id,
        push_token=sub.push_token,
        is_subscribed=sub.is_subscribed,
        updated_at=sub.updated_at,
    )







