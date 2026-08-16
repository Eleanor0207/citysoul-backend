import logging
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import models, schemas
from app.modules.body.anticheat import run_observation_checks
from app.modules.body.auth import require_session_token
from app.modules.body.encounter_tokens import (
    ENCOUNTER_TOKEN_HEADER,
    issue_encounter_token,
    require_encounter_token,
)
from app.core.redis_client import append_session_turn
from app.modules.body.geo import haversine_distance_m
from app.modules.body import daily_event_service, dialogue_log, push, queries, quests
from app.modules.body.quests import evaluate_on_summon
from app.modules.body.quota import (
    RESOURCE_DIALOGUE,
    RESOURCE_LANDMARK_RECOGNITION,
    consume,
    default_tier_id,
)
from app.modules.body.resonance import (
    AMOUNT_ENCOUNTER_COLLECTION,
    AMOUNT_QUEST,
    SOURCE_ENCOUNTER_COLLECTION,
    SOURCE_QUEST,
    apply_resonance,
)
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, require_sense_token
from app.modules.body.sense_tokens import issue_sense_token
from app.modules.body.tokens import issue_session_token
from app.modules.brain.safety import GeminiSafetyChecker, SafetyGate
from app.modules.brain.gemini import (
    GeminiClient,
    VertexAIGeminiClient,
    split_into_segments,
)
from app.modules.brain.landmark_recognition import (
    LandmarkRecognizer,
    VertexAILandmarkRecognizer,
    recognize_landmark,
)
from app.modules.brain.greetings import match_canned_greeting
from app.modules.brain.prompt_builder import build_prompt
from app.modules.brain.quest_narrative import generate_quest_wrapper
from app.modules.brain.unlock_story import generate_unlock_stories
from app.modules.brain.tts import GcsAudioStorage, GoogleCloudTTSClient, TTSClient

# 未命中快速問候時的人工預寫台詞。
#
# CONTEXT.md：「無合格輸入或生成失敗時使用人工預寫台詞」。在 Gemini（#8）接上
# 之前，**所有**未命中都會走到這裡——這是刻意的，讓端到端流程在沒有 GCP 憑證的
# 情況下也能完整跑通。接上 B1 之後，這句話會退回它原本的角色：只在模型失敗時出現。
FALLBACK_REPLY = "這件事我還沒想清楚。要不要先跟我說說你眼前看到的？"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["body"])

# 錯誤回應的契約宣告。
#
# 每支路由用 `responses=` 宣告它**實際會回傳**的錯誤碼，讓客戶端不必回頭翻
# SDD 散文就知道要處理哪些狀況。`tests/test_api_contract.py` 會強制宣告與實作
# 一致——**不宣告理論上可能但實作不會產生的錯誤碼**。
#
# 422 不在這裡：FastAPI 對任何有 request body 或 path 參數的端點都會自動加上
# 它，重複宣告只會覆蓋掉 FastAPI 自己那份更精確的 `HTTPValidationError`。
_UNAUTHORIZED = {401: {"model": schemas.ErrorResponse, "description": "Session token 無效或未提供"}}
_SPIRIT_NOT_FOUND = {
    404: {"model": schemas.ErrorResponse, "description": "靈魂不存在，或已下架（is_active=false）"}
}


def _forbidden(description: str) -> dict:
    return {403: {"model": schemas.ErrorResponse, "description": description}}


_QUOTA_EXCEEDED = {
    429: {"model": schemas.ErrorResponse, "description": "今日對話配額已用完"}
}


# 模型與語音的注入點。
#
# 做成 FastAPI 依賴而不是模組層級的單例，是為了讓測試用
# `app.dependency_overrides` 注入 fake——整條對話流程因此**不需要 GCP 憑證**
# 就能端到端測試。單例的話，測試只能去 monkeypatch 模組屬性，那種做法會在
# 匯入順序改變時安靜失效。
#
# 真實 client 用 lru_cache 保留，避免每個請求都重建（會重跑一次 ADC 解析）。
@lru_cache(maxsize=1)
def _default_gemini_client() -> GeminiClient:
    return VertexAIGeminiClient()


@lru_cache(maxsize=1)
def _default_tts_client() -> TTSClient:
    return GoogleCloudTTSClient(GcsAudioStorage())


def get_gemini_client() -> GeminiClient:
    return _default_gemini_client()


def get_tts_client() -> TTSClient:
    return _default_tts_client()


@lru_cache(maxsize=1)
def _default_landmark_recognizer() -> LandmarkRecognizer:
    return VertexAILandmarkRecognizer()


def get_landmark_recognizer() -> LandmarkRecognizer:
    return _default_landmark_recognizer()


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
    responses={**_UNAUTHORIZED, **_forbidden("不在感應範圍內"), **_SPIRIT_NOT_FOUND},
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
    responses={**_UNAUTHORIZED, **_forbidden("不在召喚半徑內"), **_SPIRIT_NOT_FOUND},
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
        # 401 有三個來源：session token、encounter token、sense token。
        **_UNAUTHORIZED,
        **_forbidden("憑證屬於別的靈魂，或兩張憑證不屬於同一個玩家"),
        **_SPIRIT_NOT_FOUND,
        **_QUOTA_EXCEEDED,
    },
)
def dialogue(
    place_id: str,
    payload: schemas.DialogueRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    sense_token: str | None = Header(default=None, alias=SENSE_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini: GeminiClient = Depends(get_gemini_client),
    tts: TTSClient = Depends(get_tts_client),
):
    """
    對話端點（**Phase 2 可用版**，#42）。

        Session ＋（Encounter 或 Sense）
          → 配額（#32）
          → B12 招呼比對（命中就直接回，不呼叫 Gemini）
          → B2 組裝（#12）→ B1 生成（#8）→ B10 語音（#21）
          → B7 短期記憶寫入

    **B4 安全邊界不在這條路徑上**——那是 Phase 3（#45）。目前 Prompt 用 B2 的
    最小可用組裝（沒有 embedding，所以沒有長期記憶；沒有當日情境）。

    ## 順序不是隨意的

    配額是**第一道關卡**（SDD v1 §3）。它排在最前面的理由跟 B4 排在 B2 之前
    一樣：被擋下的請求不該讓下游付出任何成本。B12 排在生成之前，是因為命中
    預寫台詞時完全不需要模型——那是 B12 的全部價值（零成本、零延遲）。

    ## 三種 token 的驗證邏輯不共用（SDD 第6節）

    Session ＋ Encounter 或 Session ＋ Sense 都可以。Encounter 代表「你真的
    到了現場（50m）」，Sense 代表「你在感應範圍內（150m），可以隔空聊天」。
    兩者換到的對話能力相同，差別在別的端點。
    """
    # ── 憑證 ──────────────────────────────────────────────────────────
    #
    # 兩張憑證擇一。優先看 encounter：它代表更強的在場證明，同時帶兩張時
    # 用強的那張比較不會讓人誤以為感應憑證換到了召喚能力。
    if encounter_token:
        holder_id = require_encounter_token(place_id, encounter_token)
    elif sense_token:
        holder_id = require_sense_token(place_id, sense_token)
    else:
        raise HTTPException(status_code=401, detail="missing encounter or sense token")

    # 兩張憑證必須屬於同一個玩家。少了這道檢查，A 的 session 配上 B 的相遇憑證
    # 就能通過——那等於讓沒到現場的人借用別人的在場證明。
    if holder_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    # ── 配額（第一道關卡）────────────────────────────────────────────
    #
    # 超額時 `QuotaExceededError` 由 main.py 的 handler 轉成 429。這裡刻意
    # 不 try/except——被擋下就該中斷，而錯誤格式是 API 層的事。
    #
    # ⚠️ 放在 B12 比對**之前**：AC 說配額是第一道關卡。代價是命中預寫招呼也
    # 會扣一格，而那次其實零成本。這是刻意的取捨——配額擋的是「玩家一天能講
    # 幾句話」，不是「我們付了多少錢」，讓招呼免費會給出一條無限對話的路徑。
    consume(db, session_player_id, RESOURCE_DIALOGUE)

    # ── B12 招呼比對 → B2 → B1 ───────────────────────────────────────
    #
    # 包成 closure 是為了讓 B4 能把整段當成「下游」傳給 `SafetyGate.run()`。
    # `source` 用 nonlocal 從裡面設定，所以**下游沒被呼叫時 source 仍然是
    # "refused"**——那正是 B4 要保證的事，而不是另外再寫一個 if 去推斷。
    source = "refused"

    def _reply(user_input: str) -> str:
        nonlocal source

        canned = match_canned_greeting(db, place_id, user_input)
        if canned is not None:
            source = "canned"
            return canned

        prompt = build_prompt(
            db,
            spirit_id=place_id,
            player_id=session_player_id,
            user_input=user_input,
        )

        if prompt is None:
            # 沒有生效人格卡。封閉測試期這是常態（草稿在審核通過前都是
            # active=False），所以走人工預寫台詞而不是 500。
            source = "fallback"
            return FALLBACK_REPLY

        text = gemini.generate(prompt.as_single_text())
        # B1 的契約是「永遠回非空字串，失敗時回 FALLBACK_REPLY」，所以
        # 這裡靠內容而不是例外來判斷是不是回退了。
        source = "fallback" if text == FALLBACK_REPLY else "generated"
        return text

    # ── B4 安全邊界 ──────────────────────────────────────────────────
    #
    # ⚠️ **只對開了旗標的靈魂跑。** 這一層要多花一次 Gemini 呼叫做輸入分類，
    # 也就是每輪對話的延遲與成本加倍，而 SDD 把「AI 對話成本與延遲」列為 🔴。
    # 風險不是均勻分布的——玩家問天文館「文物該不該還給對岸」的機率，跟問故宮
    # 差了一個量級。判斷依據與目前開了哪三個見 migration 0019。
    #
    # 排在 B12 招呼比對之前（包在同一個 closure 裡）：招呼比對是字串比對，
    # 「你好，我想自殺」這種夾帶的輸入若先命中招呼，玩家會拿到一句愉快的問候。
    if spirit.safety_gate_enabled:
        reply_text = SafetyGate(GeminiSafetyChecker(gemini)).run(payload.user_input, _reply)
    else:
        reply_text = _reply(payload.user_input)

    # ── B10 語音 ─────────────────────────────────────────────────────
    #
    # 失敗回 None，對話降級成純文字。SDD §8.5：模型失敗不視為錯誤。
    audio = tts.synthesize(reply_text)

    # ── B7 短期記憶 ──────────────────────────────────────────────────
    #
    # key 是 `session:{player_id}:{spirit_id}`，**不含 token 種類**——玩家從
    # 150m 隔空聊天走進 50m 召喚時，對話要自然延續（SDD §7.3）。兩種模式共用
    # 同一個 key 是這件事成立的原因。
    append_session_turn(str(session_player_id), place_id, {"role": "user", "text": payload.user_input})
    append_session_turn(str(session_player_id), place_id, {"role": "assistant", "text": reply_text})

    # ── 對話日誌（永久）────────────────────────────────────────────────
    #
    # 上面那兩行是 B7 短期記憶，寫進 Redis 且 **30 分鐘 TTL**——那是餵給模型的
    # 上下文，不是紀錄。玩家的「聊天紀錄」視窗要的是永久的那一份，所以另外寫進
    # `dialogue_turns`。
    #
    # ⚠️ role 的字串兩邊不一樣：Redis 是 user/assistant，資料表的 CHECK 約束只收
    # player/spirit。轉換在 `dialogue_log` 裡，這裡不要自己拼字串。
    #
    # 這一支**永遠不拋例外**。回覆已經生成、配額已經扣了，日誌寫不進去不該讓這次
    # 對話變成 500——那等於玩家付了配額卻什麼都沒拿到。
    dialogue_log.record_turns(
        db,
        player_id=session_player_id,
        spirit_id=place_id,
        user_input=payload.user_input,
        reply_text=reply_text,
    )

    return schemas.DialogueResponse(
        reply_text=reply_text,
        source=source,
        segments=split_into_segments(reply_text),
        tts=audio,
    )


@router.get("/spirits", response_model=list[schemas.SpiritResponse])
def list_spirits(db: Session = Depends(get_db)):
    """
    對應對外 API 清單：GET /api/v1/spirits。

    地圖上要畫出所有召喚點，而畫 pin 只需要經緯度——「所有層級所有地標都放，
    不然無法指路」（2026-08-12）。只畫腳下那一個，等於只告訴玩家他站在哪裡；
    地圖要能指路，就得同時看得見別的地標在哪個方向。

    ## 為什麼原本沒有這一支，現在有了

    `app/modules/dev/router.py` 的開頭寫著「正式 API 只有
    `GET /spirits/{placeId}`，因為客戶端是從地圖上點選的」。那句話成立的前提是
    **客戶端自己手上有那份清單**——召喚點的經緯度過去打包在
    `Assets/StreamingAssets/tiles/near/index.json` 裡。

    近景層改走即時 mesh 之後那批圖磚整組退場，那份索引跟著沒有了，前提不再成立。
    清單只剩後端有，所以它必須是正式 API 的一部分，不是主控台的方便功能。

    ## 跟 /dev/spirits 的差別

    這裡**只回 `is_active` 的靈魂**，跟 `/sense`、`/summon`、`/dialogue`、
    `GET /spirits/{placeId}` 對齊：下架的靈魂對玩家來說就是不存在，畫成 pin 等於
    邀請玩家走過去撞一個 404。主控台那支會把下架的一起回，因為測試的人需要看得到
    「為什麼這個地標打 404」——兩者的讀者不同，所以行為不同。
    """
    spirits = (
        db.query(models.Spirit)
        .filter(models.Spirit.is_active.is_(True))
        .order_by(models.Spirit.spirit_id)
        .all()
    )

    return [schemas.SpiritResponse.from_spirit(spirit) for spirit in spirits]


@router.get(
    "/spirits/{place_id}",
    response_model=schemas.SpiritResponse,
    responses={**_SPIRIT_NOT_FOUND},
)
def get_spirit(place_id: str, db: Session = Depends(get_db)):
    """
    對應對外 API 清單：GET /api/v1/spirits/{placeId}。

    回應含 `orientation`（SDD v2.1 §10.2）：客戶端 S14 的 3DoF 定向服務靠它
    決定把角色固定在哪個方位，少了它城市靈魂只能永遠黏在螢幕正中央。
    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()

    # `is_active` 檢查跟 /sense、/summon、/dialogue 對齊：下架的靈魂對玩家來說
    # 就是不存在。這支端點原本漏了這道檢查，是整個 repo 裡唯一一個會把下架靈魂
    # 回給玩家的地方——寫這次的契約測試時才發現。
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    return schemas.SpiritResponse.from_spirit(spirit)


@router.post(
    "/quests/{quest_id}/complete",
    response_model=schemas.QuestCompleteResponse,
    responses={
        **_UNAUTHORIZED,
        **_forbidden("Encounter token 屬於別的靈魂，或兩張憑證不屬於同一個玩家"),
        404: {"model": schemas.ErrorResponse, "description": "任務不存在，或玩家還沒有這筆進度"},
    },
)
def complete_quest_endpoint(
    quest_id: str,
    payload: schemas.QuestCompleteRequest,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini: GeminiClient = Depends(get_gemini_client),
):
    """
    S4＋S5．任務完成、共鳴入帳與解鎖敘事（SDD §7.5，#34 ＋ #43）。

        確定性規則判定 → quest_progress.status = 'completed'
          → resonance += 20 → 門檻判定
          → 回傳（unlock_story 與 quest_wrapper_text 皆為 null）

    ## 不呼叫腦袋，一次都不

    CONTEXT.md 明訂「可驗證微任務由**後端確定性規則**判定，不由 LLM 判定」。
    這支端點因此完全不 import 任何腦袋模組——判定只看資料庫裡的狀態。

    ## 呼叫順序硬規則（v2.1 §6.4）

    身體必須「先寫完自己的表、再呼叫腦袋」，不可顛倒。本票只做前半，正好
    符合此順序；#43 接上敘事生成時，那一段要加在所有寫入**之後**。

    ## 只收 Encounter Token

    SDD §6：持 Sense Token 者**不可**呼叫這支端點。感應憑證代表「你在 150m
    內」，而任務完成需要「你真的到了現場」。兩者用不同金鑰簽章，所以把
    sense token 塞進 `X-Encounter-Token` 會在驗章就失敗——這不是靠我們記得
    檢查 purpose，是兩套憑證本來就換不過來。
    """
    try:
        spirit_id = quests.spirit_id_for_quest(quest_id)
    except quests.QuestNotFoundError:
        # 亂打的 quest_id 是可預期的輸入，不是伺服器故障。
        raise HTTPException(status_code=404, detail="quest not found")

    holder_id = require_encounter_token(spirit_id, encounter_token)
    if holder_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    try:
        quests.complete_quest(db, player_id=session_player_id, quest_id=quest_id)
    except quests.QuestNotFoundError:
        raise HTTPException(status_code=404, detail="quest not found")

    # 共鳴入帳。去重靠 resonance_events 的 UNIQUE(player_id, source_type,
    # source_id)——**不是先查再寫**。重複提交（網路重試、連點兩下）是正常的
    # 使用者行為，S5 會回 awarded=False 而不是拋例外。
    result = apply_resonance(
        db,
        player_id=session_player_id,
        spirit_id=spirit_id,
        source_type=SOURCE_QUEST,
        source_id=quest_id,
        amount=AMOUNT_QUEST,
    )

    # ── 到這裡為止，身體的表全部寫完了 ─────────────────────────────
    #
    # 🔒 v2.1 §6.4 硬規則：**先寫完自己的表、再呼叫腦袋**，不可顛倒。
    #
    # 顛倒的話腦袋拿到的 stage 會跟資料庫不一致——玩家會看到一段講述他還沒
    # 達到的關係階段的故事。下面所有的生成呼叫都在這條線之後，而且它們的失敗
    # 一律不影響上面已經完成的寫入。

    # ⚠️ 這一整段包在 try 裡，是**刻意的重複防護**。
    #
    # B1 的契約是「永遠不拋例外」，B11 與任務包裝都建立在那之上，所以理論上
    # 這裡不需要 try。但這條路徑的失敗代價特別高：上面的寫入已經 commit 了，
    # 一個逸出的例外會讓玩家收到 500，而他的任務其實已經完成、共鳴值也已經
    # 入帳——他會重試，然後看到「重複提交」的結果，以為進度沒有存到。
    #
    # 換句話說：契約被違反時，付出代價的是玩家的信任，不是我們的 log。
    # 敘事只是包裝，包裝失敗不能讓進度看起來像消失了。
    try:
        # 沒跨門檻就不呼叫 B11。這是最常見的情況，每次白呼叫一次的成本很可觀。
        stories = generate_unlock_stories(
            gemini, spirit_id=spirit_id, stages=result.newly_unlocked_stages
        )
        unlock_stories = [
            schemas.UnlockStoryResponse(stage=s.stage, story_text=s.story_text)
            for s in stories
        ]
        wrapper_text = generate_quest_wrapper(gemini, spirit_id=spirit_id, quest_id=quest_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "任務敘事生成失敗，任務仍已完成並入帳（%s）：%s: %s",
            quest_id,
            type(exc).__name__,
            exc,
        )
        unlock_stories = []
        wrapper_text = None

    return schemas.QuestCompleteResponse(
        quest_wrapper_text=wrapper_text,
        # 單數欄位維持 §7.5 的形狀；完整清單才是真相（見 QuestCompleteResponse）。
        unlock_story=unlock_stories[0] if unlock_stories else None,
        unlock_stories=unlock_stories,
        resonance_value=result.resonance_value,
        stage=result.stage,
        newly_unlocked_stages=result.newly_unlocked_stages,
    )


@router.get(
    "/spirits/{place_id}/daily-event",
    response_model=schemas.DailyEventResponse,
    responses={**_SPIRIT_NOT_FOUND},
)
def get_daily_event_endpoint(place_id: str, db: Session = Depends(get_db)):
    """
    S10．當日情境查詢（#26）。

    **無需驗證**——這是公開的世界狀態，不含任何玩家資料。

    ## 永遠不回空畫面

        今天的快取 → 沒有就回最近一次的（通常是昨天）→ 再沒有就回人工預寫保底

    排程延遲、排程失敗、新地標剛上線都會讓今天的快取不存在，而它們全都是會發生
    的事。玩家不該因為我們的排程打嗝而看到空白。

    ⚠️ 注意跟 404 的分界：**地標不存在 → 404**；**地標存在但沒內容 → 200 ＋
    保底**。前者是玩家問錯了東西，後者是我們還沒準備好。
    """
    try:
        content = daily_event_service.get_daily_event(db, place_id=place_id)
    except daily_event_service.SpiritNotFoundError:
        raise HTTPException(status_code=404, detail="spirit not found")

    return schemas.DailyEventResponse(**content)


@router.post(
    "/quests/{quest_id}/landmark-photo",
    response_model=schemas.LandmarkPhotoResponse,
    responses={
        **_UNAUTHORIZED,
        **_forbidden("Encounter token 屬於別的靈魂，或兩張憑證不屬於同一個玩家"),
        404: {"model": schemas.ErrorResponse, "description": "任務或地標不存在"},
        429: {"model": schemas.ErrorResponse, "description": "今日地標辨識配額已用完"},
    },
)
async def landmark_photo(
    quest_id: str,
    photo: UploadFile = File(...),
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    db: Session = Depends(get_db),
    recognizer: LandmarkRecognizer = Depends(get_landmark_recognizer),
):
    """
    S12．紀念照片（後端側，#44）。

        Session ＋ Encounter → 辨識配額（#32）→ 讀進記憶體 → B13（#22）
          → 立即捨棄影像 → 相遇收藏入帳

    ## 隱私是核心約束（SDD §7.7）

    照片**只在記憶體處理，辨識完立即捨棄**：不寫檔、不上傳 Cloud Storage、
    不留作訓練資料，`encounter_collections` 也不存原始照片、GPS 座標或影像雜湊。

    `image_bytes` 是區域變數，函式結束就沒了。這裡刻意**不**把它存進任何
    地方——連「最近一次辨識失敗的照片」都不留（見 `brain/landmark_recognition.py`
    的模組註解）。

    ## 只收 Encounter Token

    SDD §6：持 Sense Token 者不可觸發相機疊圖相關動作。感應憑證代表「你在 150m
    內」，拍紀念照需要「你真的到了現場」。

    ## 辨識失敗不是錯誤

    回 `landmark_recognized: false`，任務仍可完成——玩家只是拿不到特別徽章
    （CONTEXT.md）。
    """
    try:
        spirit_id = quests.spirit_id_for_quest(quest_id)
    except quests.QuestNotFoundError:
        raise HTTPException(status_code=404, detail="quest not found")

    holder_id = require_encounter_token(spirit_id, encounter_token)
    if holder_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    spirit = db.query(models.Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    # 配額擋在辨識之前——被擋下的請求不該產生辨識成本。
    # 超額時 QuotaExceededError 由 main.py 的 handler 轉成 429。
    consume(db, session_player_id, RESOURCE_LANDMARK_RECOGNITION)

    # 讀進記憶體。這是影像唯一存在的地方，函式結束就沒了。
    image_bytes = await photo.read()

    recognized = recognize_landmark(recognizer, image_bytes, spirit_id)

    # 明確切斷參考。技術上區域變數本來就會被回收，但這一行是給讀程式碼的人看的
    # ——它標記出「從這裡開始不再持有影像」，讓之後有人想在下面加一段
    # 「順便存個檔」時，先撞到這個宣告。
    image_bytes = None

    return _record_landmark_collection(
        db, player_id=session_player_id, spirit_id=spirit_id, recognized=recognized
    )


def _record_landmark_collection(
    db: Session, *, player_id: uuid.UUID, spirit_id: str, recognized: bool
) -> schemas.LandmarkPhotoResponse:
    """
    相遇收藏入帳。

    去重靠 `UNIQUE(player_id, place_id)`——**先寫、撞到約束才知道重複**，
    不是先查再寫（同 #16 共鳴入帳的教訓）。重複收藏是正常的使用者行為。

    共鳴值只在**這次真的建立了新收藏**時才入帳。`apply_resonance` 自己也有
    去重（`resonance_events` 的 UNIQUE），所以這裡是兩層保護，但語意不同：
    這一層決定「要不要試著入帳」，那一層保證「試了也不會重複」。
    """
    row = models.EncounterCollection(
        player_id=player_id,
        place_id=spirit_id,
        landmark_recognized=recognized,
        resonance_awarded=False,
    )

    newly_collected = True
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        newly_collected = False
        db.rollback()

    awarded = False
    if newly_collected:
        result = apply_resonance(
            db,
            player_id=player_id,
            spirit_id=spirit_id,
            source_type=SOURCE_ENCOUNTER_COLLECTION,
            source_id=spirit_id,
            amount=AMOUNT_ENCOUNTER_COLLECTION,
        )
        awarded = result.awarded
        row.resonance_awarded = awarded
        db.commit()
        resonance_value = result.resonance_value
    else:
        db.commit()
        existing = (
            db.query(models.Resonance)
            .filter_by(player_id=player_id, spirit_id=spirit_id)
            .first()
        )
        resonance_value = existing.resonance_value if existing else 0

    return schemas.LandmarkPhotoResponse(
        landmark_recognized=recognized,
        resonance_awarded=awarded,
        resonance_value=resonance_value,
    )


@router.get(
    "/quests/daily",
    response_model=schemas.QuestsDailyResponse,
    responses={**_UNAUTHORIZED},
)
def get_daily_quests(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S8．任務列表查詢（#33）。

    ⚠️ **唯讀，不推進狀態機。** 跨日的 `attempts_today` 歸零是**算出來的**，
    不寫回資料庫——查詢端點如果順手做了狀態轉移，玩家只要打開任務列表就等於
    推進了一次任務，那是很難追查的副作用。真正的歸零由下一次 `/summon` 寫入。

    `daily_limit_reached` 同理：它取決於「今天」是哪一天，存進資料庫隔天就是
    錯的，所以只存在於回應。
    """
    return schemas.QuestsDailyResponse(
        quests=[schemas.QuestListItem(**q) for q in queries.daily_quests(db, player_id=player_id)]
    )


@router.get(
    "/resonance/{spirit_id}",
    response_model=schemas.ResonanceProgressResponse,
    responses={**_UNAUTHORIZED, **_SPIRIT_NOT_FOUND},
)
def get_resonance(
    spirit_id: str,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S8．共鳴進度查詢（#35）。

    「還沒開始」回 0 而不是 404——前端要能直接畫一條 0/10 的進度條。
    地標不存在或已下架才是 404。
    """
    try:
        progress = queries.resonance_progress(db, player_id=player_id, spirit_id=spirit_id)
    except queries.SpiritNotFoundError:
        raise HTTPException(status_code=404, detail="spirit not found")

    return schemas.ResonanceProgressResponse(**progress)


@router.get(
    "/profile",
    response_model=schemas.ProfileResponse,
    responses={**_UNAUTHORIZED},
)
def get_profile(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S8．玩家彙總查詢（#36）。

    Profile 畫面一次拿齊資料，避免前端發多次請求。**純身體自己的表直查，
    不呼叫腦袋。**

    🔒 `stage` 與 `GET /resonance/{spiritId}` 走**同一支** `stage_for_value()`
    （見 `queries.py` 的模組註解）。各自重算的話，哪天有人改了門檻規則卻只改到
    一邊，玩家會在兩個畫面看到不同的階段，而且不會有任何錯誤。
    """
    return schemas.ProfileResponse(
        quests=[schemas.QuestListItem(**q) for q in queries.daily_quests(db, player_id=player_id)],
        resonance=[
            schemas.ProfileResonanceItem(**r) for r in queries.all_resonance(db, player_id=player_id)
        ],
    )


@router.get(
    "/players/me/memory-summary",
    response_model=schemas.MemorySummaryResponse,
    responses={**_UNAUTHORIZED},
)
def get_memory_summary(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    記憶摘要查詢（#37）。

    🔒 **只回傳該玩家自己的記憶**（CONTEXT.md「玩家記憶僅屬單一玩家」）。
    過濾條件是 `player_id` 而不是 `spirit_id`——同一個靈魂底下有很多玩家的記憶。

    回應**不含 embedding 向量**：那是內部實作，對玩家沒有意義，而且 768 維
    浮點數會讓回應暴增數十 KB。

    B8 nightly batch（#40）未上線前，這裡回的是既有的 `dialogue_summary`
    來源記錄——**預期行為，不是缺陷**。
    """
    return schemas.MemorySummaryResponse(
        groups=[
            schemas.MemoryGroup(**g) for g in queries.memory_summaries(db, player_id=player_id)
        ]
    )


@router.get(
    "/assets/{avatar_id}",
    response_model=schemas.AvatarAssetResponse,
    responses={404: {"model": schemas.ErrorResponse, "description": "avatar 不存在"}},
)
def get_avatar_asset(avatar_id: str, db: Session = Depends(get_db)):
    """
    F7．Addressables catalog 與版本查詢（#38）。

    **無需驗證**——資產位置不是玩家資料。

    `version` 直接回傳資料表裡明確寫入的值，**不從 `updated_at` 或內容 hash
    算**：那樣的話一次無關的資料列更新就會讓所有客戶端重抓整包。

    開發期指向本機或測試 bucket 只需要改資料，不用改程式碼也不用等 CDN 佈建好
    ——那正是把它放進資料表的理由。
    """
    row = db.query(models.AvatarAsset).filter_by(avatar_id=avatar_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="avatar not found")

    return schemas.AvatarAssetResponse(
        avatar_id=row.avatar_id,
        catalog_url=row.catalog_url,
        bundle_url=row.bundle_url,
        version=row.version,
    )


@router.post(
    "/push/register",
    response_model=schemas.PushSubscriptionResponse,
    responses={**_UNAUTHORIZED},
)
def register_push(
    payload: schemas.PushRegisterRequest,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S11．註冊或更新推播 token（#39）。

    **重複註冊是更新，不是新增列**——主鍵是 `player_id`，換手機或 token 輪替時
    舊的直接被覆蓋。累積失效 token 的話，群發時得自己挑「最新的那個」，而那個
    判斷遲早會出錯然後把推播送到別人的舊裝置上。

    重新註冊視為重新訂閱：玩家會這樣做通常就是因為想再收到通知。
    """
    row = push.register_push_token(db, player_id=player_id, push_token=payload.push_token)
    return schemas.PushSubscriptionResponse(is_subscribed=row.is_subscribed)


@router.post(
    "/push/unsubscribe",
    response_model=schemas.PushSubscriptionResponse,
    responses={**_UNAUTHORIZED},
)
def unsubscribe_push(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    S11．退訂（#39）。

    用 `is_subscribed=false` 而不是刪列：token 還有用，重新訂閱時不需要重新
    註冊裝置。

    沒註冊過的玩家也回 200——「我不想收推播」對一個從沒註冊過的人來說已經成立，
    回 404 只會讓客戶端要為一個不是問題的狀況寫處理分支。
    """
    push.unsubscribe(db, player_id=player_id)
    return schemas.PushSubscriptionResponse(is_subscribed=False)
