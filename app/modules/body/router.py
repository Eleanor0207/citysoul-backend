import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import districts, models, schemas
from app.modules.body.anticheat import run_observation_checks
from app.modules.body.auth import require_session_token
from app.modules.body.encounter_tokens import (
    ENCOUNTER_TOKEN_HEADER,
    issue_encounter_token,
    require_encounter_token,
)
from app.core.config import settings
from app.core.redis_client import append_session_turn
from app.modules.body.geo import haversine_distance_m
from app.modules.body import (
    daily_event_service,
    guided_questions_service,
    push,
    queries,
    inventory,
    quests,
    story_progress,
    story_script,
    weather,
)
from app.modules.body.quests import evaluate_on_summon
from app.modules.body.quota import (
    RESOURCE_DIALOGUE,
    RESOURCE_LANDMARK_RECOGNITION,
    consume,
    default_tier_id,
    try_consume_global,
)
from app.modules.body.resonance import (
    SOURCE_DIALOGUE,
    SOURCE_ENCOUNTER_COLLECTION,
    SOURCE_QUEST,
    apply_resonance,
    dialogue_source_id,
    load_resonance_rules,
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
from app.modules.brain.models import StoryArc, StoryBeat
from app.modules.brain.greetings import match_canned_greeting
from app.modules.brain.loader import load_active_persona
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
_ARC_NOT_FOUND = {
    404: {"model": schemas.ErrorResponse, "description": "劇情主線或 beat 不存在（或已停用）"}
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
def _default_weather_provider() -> weather.WeatherProvider | None:
    return weather.build_provider()


def get_weather_provider() -> weather.WeatherProvider | None:
    """沒設金鑰時是 None＝這個環境不提供天氣，端點回 503（SDD §20.5.2）。"""
    return _default_weather_provider()


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


_DISTRICT_ENTRY_ITEMS = {
    "wanhua": "item_wanhua_letter",
}


def _grant_district_entry_item(
    db: Session, *, player_id: uuid.UUID, district_id: str
) -> bool:
    """Insert the district item once, using the inventory unique index."""
    item_id = _DISTRICT_ENTRY_ITEMS.get(district_id)
    if item_id is None:
        return False

    statement = pg_insert(models.PlayerInventory).values(
        player_id=player_id,
        item_type="story_document",
        item_id=item_id,
    )
    statement = statement.on_conflict_do_nothing(
        index_elements=["player_id", "item_id"]
    ).returning(models.PlayerInventory.inventory_id)
    inserted = db.execute(statement)
    inventory_id = inserted.scalar_one_or_none()
    db.commit()
    return inventory_id is not None


@router.post(
    "/districts/check-entry",
    response_model=schemas.DistrictEntryResponse,
    responses={**_UNAUTHORIZED},
)
def check_district_entry(
    payload: schemas.DistrictEntryRequest,
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """Evaluate one GPS fix and grant a district arc entry item if new."""
    district_id = districts.check_player_in_district(
        db, latitude=payload.latitude, longitude=payload.longitude
    )
    if district_id is None:
        return schemas.DistrictEntryResponse(
            district_id=None,
            entry_granted=False,
            arc_id=None,
        )

    arc = (
        db.query(StoryArc)
        .filter_by(district_id=district_id)
        .order_by(StoryArc.arc_id)
        .first()
    )
    arc_id = arc.arc_id if arc is not None else None
    entry_granted = _grant_district_entry_item(
        db, player_id=player_id, district_id=district_id
    )

    return schemas.DistrictEntryResponse(
        district_id=district_id,
        entry_granted=entry_granted,
        arc_id=arc_id,
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
    對話端點（**Phase 3 完整版**，#45）。

        Session ＋（Encounter 或 Sense）
          → 配額（#32）
          → B4 安全邊界（僅對逐地標開啟安全閘的靈魂）
          → B12 招呼比對（命中就直接回，不呼叫 Gemini）
          → B2 組裝（#12）→ B1 生成（#8）→ B10 語音（#21）
          → B7 短期記憶寫入

    **B4 已接在這條路徑上**，由 `spirit.safety_gate_enabled` 逐地標控制。
    安全閘會先分類輸入；只有判定安全才進入 B12／B2／B1 closure。Prompt 目前仍
    不傳 embedding 或當日情境，這兩項不是本端點本票的範圍。

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
    #
    # ── 全域上限（BE#65／SDD §18.6.4）─────────────────────────────
    #
    # 上面那道是 per player per day，攔不住「1000 個玩家同時觸頂」。這一道
    # 不看玩家，只看今天全站燒了幾輪。
    #
    # **順序：全域在前、玩家在後。** 反過來的話，全域觸頂時玩家仍然被扣了一格
    # 額度——那是拿玩家的額度去付我們的成本問題。代價是玩家自己的 429 會讓
    # 全域計數多算一格；那個方向的誤差是保守的（偏向省錢），也小得多。
    #
    # 觸頂**不拋例外、不回 429**：全域上限是我們的帳單問題，不是玩家做錯事。
    # 這裡只記下來，下面的生成路徑會安靜地切到保底句（§18.7.3）。
    global_budget_left = try_consume_global(
        RESOURCE_DIALOGUE, settings.global_dialogue_daily_limit
    )

    consume(db, session_player_id, RESOURCE_DIALOGUE)

    # ── B2 → B1 ──────────────────────────────────────────────────────
    #
    # 包成 closure 是為了讓 B4 能把整段當成「下游」傳給 `SafetyGate.run()`。
    # `source` 用 nonlocal 從裡面設定，所以**下游沒被呼叫時 source 仍然是
    # "refused"**——那正是 B4 要保證的事，而不是另外再寫一個 if 去推斷。
    source = "refused"

    def _reply(user_input: str) -> str:
        nonlocal source

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

    # ── B12 招呼比對（在 B4 之前短路）────────────────────────────────
    #
    # 命中就直接回，**不跑 B4、不呼叫 Gemini**。
    #
    # ## 2026-08-20 之前這一段在 B4 後面，為什麼搬到前面
    #
    # 原本的理由是「招呼比對是字串比對，『你好，我想自殺』這種夾帶的輸入若先
    # 命中招呼，玩家會拿到一句愉快的問候」。那個顧慮描述的是**包含比對**，而
    # `find_canned_response()` 做的是**完全相等**（見 greetings.py 的
    # 「為什麼是完全相等而不是包含」）。「你好，我想自殺」不等於任何一個觸發
    # 語，本來就命中不了——那段註解防的是一個實作上做不出來的攻擊。
    #
    # 代價卻是真的：對開了 B4 的靈魂，**每一句「你好」「謝謝」都要先燒一次
    # Gemini 分類呼叫**，而 B12 存在的唯一理由就是零成本零延遲。實測還踩到
    # 誤攔——龍山寺、故宮、新文化運動三隻對「拜拜」全部回 `refused`，因為
    # 分類器把它當祭拜。玩家只是要說再見，卻收到一段禁忌轉向。
    #
    # ## 安全性論證
    #
    # 觸發語與回覆都是人工審核過的固定字串，比對是完全相等。玩家能做的只有
    # 「一字不差打出清單上的某一個」，拿到一句審核者已經看過並批准的回覆——
    # **沒有夾帶的空間，夾帶就不是完全相等了。**
    #
    # ⚠️ **這個短路的安全性完全建立在「完全相等」上。** 哪天有人把 B12 改成
    # 模糊比對、前綴比對或去除標點以外的正規化，這裡就必須跟著搬回 B4 後面。
    # `tests/test_dialogue.py` 有一條測試把這個不變式釘住。
    canned = match_canned_greeting(db, place_id, payload.user_input)

    if canned is not None:
        source = "canned"
        reply_text = canned

    # ── B4 安全邊界 ──────────────────────────────────────────────────
    #
    # ⚠️ **只對開了旗標的靈魂跑。** 這一層要多花一次 Gemini 呼叫做輸入分類，
    # 也就是每輪對話的延遲與成本加倍，而 SDD 把「AI 對話成本與延遲」列為 🔴。
    # 風險不是均勻分布的——玩家問天文館「文物該不該還給對岸」的機率，跟問故宮
    # 差了一個量級。判斷依據與目前開了哪幾個見 `content/spirits.yaml`。
    # ── 全域上限觸頂：跳過所有會花錢的路徑 ───────────────────────────
    #
    # 排在 B12 **之後**是刻意的：招呼比對是完全相等的字串查詢，零成本零延遲，
    # 沒有理由因為預算而讓玩家連「你好」都得不到回應。B4 與 B1 則各是一次
    # Gemini 呼叫，那正是這道閘門要省下來的東西。
    #
    # 玩家看到的是這隻靈魂自己的保底句（`llm_failure_fallback`），跟生成失敗
    # 時一模一樣——玩家不需要、也不該分辨得出「我們今天沒預算了」。
    elif not global_budget_left:
        source = "fallback"
        persona = load_active_persona(db, place_id)
        reply_text = getattr(persona, "llm_failure_fallback", None) or FALLBACK_REPLY
        logger.warning(
            "global dialogue quota exhausted; serving fallback place_id=%s", place_id
        )

    elif spirit.safety_gate_enabled:
        reply_text = SafetyGate(GeminiSafetyChecker(gemini)).run(payload.user_input, _reply)
    else:
        reply_text = _reply(payload.user_input)

    # ── 共鳴值：每日對話 ─────────────────────────────────────────────
    #
    # backend#70（#52 拍板）：玩家與這個靈魂當天完成一輪對話 +10，同一天
    # 只入帳一次。**只有 "canned" 與 "generated" 算**——"refused" 是被 B4
    # 擋下來，玩家沒有真的跟靈魂對上話；"fallback" 是沒有生效人格卡或生成
    # 失敗，玩家拿到的是一句人工預寫的保底句，不是這個靈魂的回應。兩者都不
    # 是「完成一輪對話」。
    #
    # 去重靠 resonance_events 的 UNIQUE，source_id 帶靈魂 id 與 Asia/Taipei
    # 日期（見 dialogue_source_id() 的說明）。
    #
    # ⚠️ 這裡入帳後若剛好跨過門檻，**不會**觸發 B11 解鎖敘事生成——那段邏輯
    # 目前只接在 `/quests/{questId}/complete`，`DialogueResponse` 也還沒有
    # 對應欄位可以承載敘事文字。玩家的階段仍然正確入庫、GET /resonance 與
    # GET /profile 讀得到，只是不會在這一輪對話裡跳出解鎖故事——這是已知的
    # 範圍縮減，不是遺漏，需要的話另開票。
    if source in ("canned", "generated"):
        today = quests.taipei_today(datetime.now(timezone.utc))
        apply_resonance(
            db,
            player_id=session_player_id,
            spirit_id=place_id,
            source_type=SOURCE_DIALOGUE,
            source_id=dialogue_source_id(place_id, today),
            amount=load_resonance_rules(db).amount_dialogue,
        )

        # ── 當日任務：有對話就完成（SDD §20.3.4／backend#71）────────────
        #
        # 排在共鳴值之後、同一個 if 底下，因為兩者是**同一個觸發點**：這一輪
        # 對話真的成立了。分成兩個地方判斷的話，「什麼叫成立」會有兩份定義。
        #
        # 玩家還沒召喚過（沒有進度列）時這支安靜地什麼都不做——他可能正在
        # 150m 外隔空聊天，確實在對話，但還沒有任務可以完成。兩段式不變。
        quests.complete_on_dialogue(
            db, player_id=session_player_id, spirit_id=place_id
        )

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

    try:
        suggested = guided_questions_service.get_suggested_questions(
            db,
            gemini,
            place_id=place_id,
            player_id=session_player_id,
            # 憑證種類就是距離：encounter_token 代表 50m 在場成立，只有
            # sense_token 代表人還在 150m 感應圈，可能連建築物都還沒看到。
            proximity=guided_questions_service.proximity_for_token(
                has_encounter_token=bool(encounter_token)
            ),
        )
    except Exception:  # noqa: BLE001
        # B14 is an additive convenience field; it must never turn a successful
        # dialogue turn into an error response.
        logger.exception("B14 suggested question lookup failed for %s", place_id)
        suggested = {"questions": [], "is_fallback": True}

    return schemas.DialogueResponse(
        reply_text=reply_text,
        source=source,
        segments=split_into_segments(reply_text),
        tts=audio,
        suggested_questions=suggested["questions"],
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

    return [
        schemas.SpiritResponse.from_spirit(
            spirit, persona=load_active_persona(db, spirit.spirit_id)
        )
        for spirit in spirits
    ]


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

    return schemas.SpiritResponse.from_spirit(
        spirit, persona=load_active_persona(db, spirit.spirit_id)
    )


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
        # 目錄表優先：story 任務的 id（`q_...`）不合 `{spirit_id}:daily` 的命名
        # 慣例，只用慣例反推的話這支端點會把它們全部 404 掉。
        spirit_id = quests.resolve_spirit_id(db, quest_id)
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
        amount=load_resonance_rules(db).amount_quest,
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
        persona = load_active_persona(db, spirit_id)
        # 沒跨門檻就不呼叫 B11。這是最常見的情況，每次白呼叫一次的成本很可觀。
        stories = generate_unlock_stories(
            gemini,
            spirit_id=spirit_id,
            stages=result.newly_unlocked_stages,
            persona=persona,
        )
        unlock_stories = [
            schemas.UnlockStoryResponse(stage=s.stage, story_text=s.story_text)
            for s in stories
        ]
        wrapper_text = generate_quest_wrapper(
            gemini,
            spirit_id=spirit_id,
            quest_id=quest_id,
            persona=persona,
        )
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
    "/story-arcs/{arc_id}/state",
    response_model=schemas.StoryArcStateResponse,
    responses={**_UNAUTHORIZED, **_ARC_NOT_FOUND},
)
def get_story_arc_state(
    arc_id: str,
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    劇情主線目前進度（backend#72）。唯讀，不觸發任何寫入。

    ⚠️ **目前沒有真正的呼叫端**——三地標觀察任務（`quest_type='story'`）卡在
    實地勘查，還沒有任何內容能推進 beat（見 `story_progress.py` 模組說明）。
    這支端點先把讀取機制建好，等內容到位時客戶端直接接上。
    """
    try:
        state = story_progress.arc_state(db, player_id=session_player_id, arc_id=arc_id)
    except story_progress.ArcNotFoundError:
        raise HTTPException(status_code=404, detail="story arc not found")

    return schemas.StoryArcStateResponse(
        **state,
        variables=story_progress.story_variables(
            db, player_id=session_player_id, arc_id=arc_id
        ),
    )


@router.get(
    "/story-arcs/{arc_id}/beats/{beat_id}/script",
    response_model=schemas.BeatScriptResponse,
    responses={
        **_UNAUTHORIZED,
        **_ARC_NOT_FOUND,
        **_forbidden("這個 beat 還沒解鎖——腳本就是劇情內容，不能先看"),
    },
)
def get_beat_script(
    arc_id: str,
    beat_id: str,
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    一個 beat 的台詞與選項（backend#72）。

    在這支之前，`narrative_directive` 裡的節點與 `brain.story_strings` 裡的文字
    **沒有任何出口**——客戶端拿得到 `beat_prologue_letter` 這個 id，拿不到它的
    任何一個字。

    ## 只回傳玩家已經走到的節點

    已完成的 beat 可以重看（玩家本來就讀過了），前置全部滿足的可以讀（他正要
    走這一段）。其餘一律 403。**腳本就是劇情內容本身**，能任意查等於能先把
    結局看完——這跟 `arc_state()` 的 `eligible_beat_ids` 刻意不列出未解鎖節點
    是同一條規則。

    ## 內容不經 LLM

    `story_strings` 是人工撰寫、原樣顯示的成品，跟 `canned_greetings` 同一類。
    這支端點不呼叫任何腦袋模組。
    """
    beat = (
        db.query(StoryBeat).filter_by(beat_id=beat_id, active=True).first()
    )
    if beat is None or beat.arc_id != arc_id:
        raise HTTPException(status_code=404, detail="story beat not found")

    try:
        state = story_progress.arc_state(
            db, player_id=session_player_id, arc_id=arc_id
        )
    except story_progress.ArcNotFoundError:
        raise HTTPException(status_code=404, detail="story arc not found")

    if beat_id not in set(state["completed_beat_ids"]) | set(
        state["eligible_beat_ids"]
    ):
        raise HTTPException(status_code=403, detail="story beat is locked")

    script = story_script.build_script(db, beat)
    return schemas.BeatScriptResponse(
        beat_id=script.beat_id,
        character_id=script.character_id,
        spirit_id=script.spirit_id,
        nodes=[
            schemas.ScriptNodeResponse(
                type=node.type,
                speaker=node.speaker,
                text=node.text,
                options=[
                    schemas.ScriptOptionResponse(
                        option_id=option.option_id,
                        text=option.text,
                        sets=option.sets,
                    )
                    for option in node.options
                ],
                min_viewed_to_proceed=node.min_viewed_to_proceed,
                exclusive=node.exclusive,
            )
            for node in script.nodes
        ],
        info_cards=[
            schemas.InfoCardResponse(
                card_id=card.card_id,
                historical_text=card.historical_text,
                fiction_text=card.fiction_text,
            )
            for card in script.info_cards
        ],
    )


@router.post(
    "/story-arcs/{arc_id}/beats/{beat_id}/advance",
    response_model=schemas.BeatAdvanceResponse,
    responses={**_UNAUTHORIZED, **_ARC_NOT_FOUND, **_forbidden("這個 beat 的前置還沒全部完成")},
)
def advance_story_beat(
    arc_id: str,
    beat_id: str,
    payload: schemas.BeatAdvanceRequest | None = None,
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    把一個 beat 記成這個玩家已完成（backend#72）。

    確定性規則，不經 LLM——前置是否都在 `players_story_progress` 裡（見
    `story_progress.advance_beat()`）。推進到終點 beat 時同一次呼叫發放結局
    共鳴值（三個地標各 +30，#52 拍板）。

    ⚠️ **只驗證 session token，沒有比照 `/quests/{questId}/complete` 要求
    encounter token 做在場證明**——現在完全沒有真正的呼叫端，無從決定「哪
    一種在場證明」是對的。等三地標觀察任務落地、真正的呼叫端（quest
    turn-in）確定形狀時，這道 gating 需要一併補上，不該假設這個寬鬆版本
    就是終版（見 `story_progress.py` 模組說明）。
    """
    try:
        result = story_progress.advance_beat(
            db,
            player_id=session_player_id,
            arc_id=arc_id,
            beat_id=beat_id,
            chosen_option_ids=payload.chosen_option_ids if payload else [],
        )
    except story_progress.BeatNotFoundError:
        raise HTTPException(status_code=404, detail="story beat not found")
    except story_progress.BeatNotUnlockedError:
        raise HTTPException(status_code=403, detail="prerequisite beats not completed")
    except story_progress.RequiredQuestIncompleteError as unfinished:
        # 這一種是**玩家還有事情要做**，不是走錯順序、也不是資料壞掉。客戶端
        # 要能據此把玩家導回任務，所以 detail 帶出是哪個任務。
        raise HTTPException(
            status_code=403, detail=f"required quest not completed: {unfinished}"
        )
    except story_progress.RequiredItemMissingError as missing:
        # 跟前置未完成分開回報：玩家看到的補救方式不同，而且道具缺漏多半代表
        # 上一節的發放沒有生效，混在同一個 detail 裡就查不出是哪一種。
        raise HTTPException(
            status_code=403, detail=f"required item not held: {missing}"
        )

    return schemas.BeatAdvanceResponse(
        beat_id=result.beat_id,
        already_completed=result.already_completed,
        story_completed=result.story_completed,
        completed_beat_ids=result.completed_beat_ids,
        granted_item_ids=result.granted_item_ids,
        set_variables=result.set_variables,
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


@router.get(
    "/spirits/{place_id}/suggested-questions",
    response_model=schemas.SuggestedQuestionsResponse,
    responses={
        **_UNAUTHORIZED,
        **_forbidden("憑證屬於別的靈魂，或兩張憑證不屬於同一個玩家"),
        **_SPIRIT_NOT_FOUND,
    },
)
def get_suggested_questions_endpoint(
    place_id: str,
    session_player_id: uuid.UUID = Depends(require_session_token),
    encounter_token: str | None = Header(default=None, alias=ENCOUNTER_TOKEN_HEADER),
    sense_token: str | None = Header(default=None, alias=SENSE_TOKEN_HEADER),
    db: Session = Depends(get_db),
    gemini: GeminiClient = Depends(get_gemini_client),
):
    """Return today's B14 questions after Session plus Sense/Encounter auth."""

    if encounter_token:
        holder_id = require_encounter_token(place_id, encounter_token)
    elif sense_token:
        holder_id = require_sense_token(place_id, sense_token)
    else:
        raise HTTPException(status_code=401, detail="missing encounter or sense token")

    if holder_id != session_player_id:
        raise HTTPException(status_code=403, detail="token holder mismatch")

    try:
        content = guided_questions_service.get_suggested_questions(
            db,
            gemini,
            place_id=place_id,
            player_id=session_player_id,
            proximity=guided_questions_service.proximity_for_token(
                has_encounter_token=bool(encounter_token)
            ),
        )
    except guided_questions_service.SpiritNotFoundError:
        raise HTTPException(status_code=404, detail="spirit not found")

    return schemas.SuggestedQuestionsResponse(**content)


@router.get(
    "/inventory",
    response_model=schemas.InventoryResponse,
    responses={**_UNAUTHORIZED},
)
def get_inventory_endpoint(
    session_player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    玩家持有的道具（待辦 P3 第 21 項）。

    `player_inventory` 在這之前是**只寫不讀**的：走進萬華會發一封信、
    `story_beats.required_item_ids` 拿它當解鎖條件，但玩家看不到自己有什麼。

    玩家從 session token 解出來，**沒有任何參數可以指定別人**（同 `/profile`）。
    一件都沒有時回空陣列，不是 404——那是正常的起始狀態。

    有長文的道具會一併帶回 `story_text`，內容來自 `brain.story_strings`，是
    經人工審核的成品，原樣顯示、不經模型。
    """
    items = inventory.list_items(db, session_player_id)

    return schemas.InventoryResponse(
        items=[
            schemas.InventoryItemResponse(
                item_id=item.item_id,
                item_type=item.item_type,
                acquired_at=item.acquired_at,
                source_quest_id=item.source_quest_id,
                story_text=item.story_text,
            )
            for item in items
        ]
    )


@router.get(
    "/weather/current",
    response_model=schemas.CurrentWeatherResponse,
    responses={
        **_UNAUTHORIZED,
        503: {
            "model": schemas.ErrorResponse,
            "description": "這個環境沒有設定天氣金鑰，或上游查詢失敗",
        },
    },
)
def get_current_weather_endpoint(
    latitude: float = Query(..., ge=-90.0, le=90.0),
    longitude: float = Query(..., ge=-180.0, le=180.0),
    session_player_id: uuid.UUID = Depends(require_session_token),
    provider: weather.WeatherProvider | None = Depends(get_weather_provider),
):
    """
    「當地氛圍」：玩家目前位置的即時溫度與天氣狀況（backend#75／SDD §20.5）。

    ## 為什麼要 session token

    這支不含任何玩家資料，本來可以是公開的。要求憑證是為了**不讓它變成一個
    免費的天氣代理**——我們的金鑰在後端，開著等於誰都能拿我們的額度去查天氣。

    ## 座標會被降精度，這裡不做也會在下游做

    `weather.get_current_weather()` 的第一件事就是四捨五入到小數點後 2 位
    （約 1 km）。降精度是模組的不變式，不是呼叫端要記得的紀律。

    ## 失敗回 503，不是 200 加空值

    §18.7.3「回退不阻擋玩家進度」講的是**敘事**：那些是包裝，玩家的進度是真的。
    這條 bar 不是敘事也不是進度，它就是一個數值——沒有值的時候客戶端整條
    隱藏（§20.5.2），而「沒有值」用狀態碼講比用一個假的 0 度乾淨。

    ⚠️ **不寫任何東西進資料庫**：座標與結果都不落地（CONTEXT.md「不在背景追蹤、
    判斷或保存玩家位置」）。快取在 Redis，key 只含降精度後的格點。
    """
    if provider is None:
        raise HTTPException(status_code=503, detail="weather is not configured")

    current = weather.get_current_weather(provider, latitude, longitude)
    if current is None:
        raise HTTPException(status_code=503, detail="weather lookup failed")

    return schemas.CurrentWeatherResponse(
        temperature_c=current.temperature_c,
        condition_text=current.condition_text,
        condition_type=current.condition_type,
    )


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
            amount=load_resonance_rules(db).amount_encounter_collection,
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

    `attempts_today` 仍以查詢當下的 `attempts_date` 計算跨日呈現值，但不會限制
    任務挑戰，也不會產生額外的 `quest.status`。
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
