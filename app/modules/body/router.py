import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
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
from app.modules.body.quests import evaluate_on_summon
from app.modules.body.quota import QuotaExceededError, consume, default_tier_id
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token, require_sense_token
from app.modules.body.tokens import issue_session_token
from app.modules.brain.gemini import GeminiClient, VertexAIGeminiClient
from app.modules.brain.greetings import match_canned_greeting
from app.modules.brain.prompt_builder import assemble_dialogue_prompt
from app.modules.brain.safety import GeminiSafetyChecker, SafetyChecker, enforce_safety_boundary
from app.modules.brain.tts import GoogleCloudTTSClient, TTSClient

# usage_tier_limits 已種好的 resource_type（migration 0004）。只在這裡出現
# 一次——呼叫端不重複寫這個字串，改資源名稱只需要改這裡。
DIALOGUE_QUOTA_RESOURCE = "dialogue_calls_daily"

router = APIRouter(prefix="/api/v1", tags=["body"])


def get_gemini_client() -> GeminiClient:
    """
    FastAPI dependency，讓測試可以用 `app.dependency_overrides` 換成
    `FakeGeminiClient`，完全不需要 GCP 憑證——跟 `get_db` 是同一種用法。
    """
    return VertexAIGeminiClient()


def get_tts_client() -> TTSClient:
    """同上，測試換成 `FakeTTSClient`。"""
    return GoogleCloudTTSClient()


def get_safety_classifier_client() -> GeminiClient:
    """
    B4（安全邊界檢查，issue #11）分類專用的 Gemini client——**刻意跟**
    `get_gemini_client()`（用來生成實際回覆）**是不同的 dependency**，即使
    正式環境兩者背後可能是同一個 Vertex AI 專案。

    分開的理由是可測試性，不是效能：如果兩者共用同一個 fake，測試就沒辦法
    只針對「安全分類」或只針對「回覆生成」個別下 spy——issue #45 AC1 明訂
    「不安全時 B1 一次都沒被呼叫」，這裡的「B1」指的是**生成回覆**那次呼叫，
    不包含分類本身用掉的那一次（分類是 B4 自己的實作細節，不算在「下游」
    裡）。合成一個 dependency 會讓這兩種呼叫的次數混在同一個計數器上，
    測試就無法分開驗證。
    """
    return VertexAIGeminiClient()


def get_safety_checker(
    client: GeminiClient = Depends(get_safety_classifier_client),
) -> SafetyChecker:
    return GeminiSafetyChecker(client)


def _require_presence_token(
    spirit_id: str, encounter_token: str | None, sense_token: str | None
) -> uuid.UUID:
    """
    SDD 第6節：Session Token 必要，再加 Encounter 或 Sense 至少一張。

    兩張都沒帶才是「缺憑證」；帶了其中一張就用那一張的驗證邏輯（各自獨立，
    不共用，見 `encounter_tokens.py`／`sense_tokens.py` 的模組說明），驗證
    失敗時直接讓那張憑證自己的錯誤往外拋，不嘗試退而驗證另一張——玩家帶哪張
    憑證來，就該對那張憑證負責，兩張憑證的失敗原因混在一起只會讓除錯更難。
    """
    if encounter_token is not None:
        return require_encounter_token(spirit_id, encounter_token)
    if sense_token is not None:
        return require_sense_token(spirit_id, sense_token)
    raise HTTPException(status_code=401, detail="missing encounter or sense token")


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


@router.post("/sense", response_model=schemas.SenseResponse)
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


@router.post("/summon", response_model=schemas.SummonResponse)
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
    # `tts=None` 時整個欄位從 JSON 消失，不是序列化成 `"tts": null`——
    # 見 `schemas.DialogueResponse` 的說明。
    response_model_exclude_none=True,
)
def dialogue(
    place_id: str,
    payload: schemas.DialogueRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    sense_token: str | None = Header(default=None, alias=SENSE_TOKEN_HEADER),
    gemini_client: GeminiClient = Depends(get_gemini_client),
    tts_client: TTSClient = Depends(get_tts_client),
    safety_checker: SafetyChecker = Depends(get_safety_checker),
    db: Session = Depends(get_db),
):
    """
    對話端點（issue #45 完整版）。

    ```
    驗證 Session Token ＋（Encounter 或 Sense Token）
      → 配額檢查（#32）
      → B12 固定招呼比對 → 命中直接回預寫台詞，不呼叫 B4／B2／B1
      → 未命中 → B4 安全邊界（#11）→ 不安全就婉拒，不繼續往下
                              → 安全 → B2 Prompt 組裝（#12）→ B1 Gemini（#8）
      → B10 TTS（#21）
      → B7 短期記憶寫入
    ```

    `user_input` 空白或超長由 `DialogueRequest` 的 pydantic 驗證在進到這支
    函式之前就擋下（422），配額因此不會被消耗——格式錯誤不該扣玩家額度。
    """
    presence_player_id = _require_presence_token(place_id, encounter_token, sense_token)

    # 兩張憑證必須屬於同一個玩家。少了這道檢查，A 的 session 配上 B 的相遇／
    # 感應憑證就能通過——那等於讓沒到現場（或沒進感應範圍）的人借用別人的
    # 在場證明。
    if presence_player_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    # 配額是第一道關卡，排在 B12／B4／B2／B1 之前——被擋下的請求不該產生任何
    # 生成成本（issue #32）。
    try:
        consume(db, player_id=session_player_id, resource=DIALOGUE_QUOTA_RESOURCE)
    except QuotaExceededError as exc:
        # detail 只帶這個玩家自己的資訊（`QuotaExceededError` 的設計就是如此），
        # 不會不小心洩漏其他玩家的用量或內部設定值。
        raise HTTPException(
            status_code=429,
            detail={
                "resource": exc.resource,
                "limit": exc.limit,
                "reset_at": exc.reset_at.isoformat(),
            },
        )

    canned = match_canned_greeting(db, place_id, payload.user_input)
    if canned is not None:
        reply_text, source = canned, "canned"
    else:
        # `_generated` 用一個可變旗標記錄「有沒有真的走到生成那一步」，而不是
        # 另外再呼叫一次 `safety_checker.check()` 來判斷 source——安全分類本身
        # 呼叫 Gemini，多呼叫一次等於讓玩家的每一句話都被分類兩遍、多付一次
        # 那個成本，`enforce_safety_boundary`（issue #11）已經只呼叫一次
        # `check()`，這裡跟著遵守同一條規則。
        generated = {"value": False}

        def _generate_reply() -> str:
            generated["value"] = True
            assembled = assemble_dialogue_prompt(
                db,
                player_id=session_player_id,
                spirit_id=place_id,
                player_input=payload.user_input,
            )
            # 沒有生效人格卡時 B2 回傳 None（issue #12 AC5）：退回只餵原始
            # 輸入給 Gemini，跟 #42 可用版對「沒有人格卡」情境的處理一致，
            # 不是新行為。
            prompt = assembled.as_prompt() if assembled is not None else payload.user_input
            # `GeminiClient.generate()` 永遠回傳非空字串、永遠不拋例外（見
            # gemini.py 模組說明）——這裡不需要 try/except，模型失敗時它自己
            # 回退到人工預寫台詞，這一層看不出兩者的差別，也不需要看出。
            return gemini_client.generate(prompt)

        reply_text = enforce_safety_boundary(safety_checker, payload.user_input, _generate_reply)
        source = "generated" if generated["value"] else "refused"

    # 同理：TTS 失敗回 None，純文字照常顯示，不拋例外。婉拒台詞也要合成語音
    # ——玩家聽到的仍然是角色在說話，不是無聲的系統訊息。
    tts_result = tts_client.synthesize(reply_text)

    # 感應／召喚兩種模式共用同一把 session key（`player_id` + `spirit_id`），
    # 玩家從 150m 聊到 50m 召喚時對話自然延續（SDD 第7.3節）。
    session_key_player_id = str(session_player_id)
    append_session_turn(session_key_player_id, place_id, {"role": "user", "text": payload.user_input})
    append_session_turn(session_key_player_id, place_id, {"role": "assistant", "text": reply_text})

    return schemas.DialogueResponse(reply_text=reply_text, source=source, tts=tts_result)


@router.get("/spirits/{place_id}", response_model=schemas.SpiritResponse)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """對應對外 API 清單：GET /api/v1/spirits/{placeId}（Sprint1 先只回基本資料）。"""
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if not spirit:
        raise HTTPException(status_code=404, detail="spirit not found")
    return spirit
