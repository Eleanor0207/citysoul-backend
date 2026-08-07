import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.brain.tts import TTSResult
from app.modules.brain.unlock_story import UnlockStory


class ErrorResponse(BaseModel):
    """
    統一錯誤回應模型（issue #30）。

    形狀對齊 FastAPI `HTTPException` 既有的 `{"detail": ...}`——**不改變任何
    現有錯誤的實際 JSON 形狀**，只是讓契約描述它，讓 codegen 產出單一錯誤
    DTO。`detail` 用 `Any`：多數端點是字串，配額超限（429）是物件
    （`resource`／`limit`／`reset_at`），契約要能同時涵蓋兩種而不失真。
    """

    detail: Any


class PlayerCreateRequest(BaseModel):
    """
    App 首次啟動時呼叫。device_id 由 App 產生並存在裝置安全儲存區
    （對應 CONTEXT.md「匿名玩家」定義），這裡不接受任何硬體裝置 ID 當替代。
    """

    device_id: str = Field(min_length=1, max_length=128)

    @field_validator("device_id")
    @classmethod
    def device_id_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("device_id 不可為空白字元")
        return value


class PlayerResponse(BaseModel):
    """
    `POST /api/v1/players` 的回應。

    `account_id` 是對外契約，對應 DB 的 `auth_provider_id`（0003 改的名）。
    client 的 `PlayerDto` 用 `[JsonProperty("account_id")]` 寫死了這個 key。

    綁定流程做起來的時候，這裡應該一併把 `auth_provider` 也吐出去——
    只有「provider 那邊的 id」而不知道是哪一家 provider，資訊是不完整的。
    那一次改動要連 client 一起發版。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    player_id: uuid.UUID
    device_id: str
    account_id: str | None = Field(validation_alias="auth_provider_id")
    created_at: datetime
    session_token: str


class SummonRequest(BaseModel):
    """
    S2．在場驗證請求（SDD 第8.4節）。

    `gps_accuracy_m` 與 `is_mock_location` 供 S3 防作弊觀察期使用
    （見 `anticheat.py`）：偵測到只記 log，不影響這次召喚的結果。
    """

    spirit_id: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    gps_accuracy_m: float | None = None
    is_mock_location: bool = False


class SenseRequest(BaseModel):
    """
    S2-new．感應範圍驗證請求（SDD 第8.3節）。

    刻意**不**收 `gps_accuracy_m` 與 `is_mock_location`：那兩個欄位是給 S3 防作弊
    觀察期用的，而觀察期記錄的是「在場」——CONTEXT.md 對在場紀錄的定義是完成
    在場驗證所需的地標與時間，感應範圍不構成在場。150 公尺外的一次感應不該產生
    任何可用來推測玩家位置的紀錄。
    """

    spirit_id: str = Field(min_length=1, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class SenseResponse(BaseModel):
    sense_token: str
    spirit_id: str


QuestStatus = Literal["in_progress", "completed", "daily_limit_reached"]


class QuestStateResponse(BaseModel):
    """
    SDD 第8.4節的 `quest` 欄位。

    `status` 是 `Literal`（issue #30）而不是裸 `str`——OpenAPI 因此產出真正
    的 `enum`，Unity 端才有 enum 可解析（SDD v2.1 §11.2.1 硬規則5）。三個值
    的語意分界不變：`in_progress`／`completed` 是資料庫狀態，
    `daily_limit_reached` **只存在於 API 回應**，是查詢當下依 `attempts_date`
    算出來的結果（見 `quests.effective_state`），不落地。
    """

    quest_id: str
    status: QuestStatus
    attempts_today: int


class SummonResponse(BaseModel):
    encounter_token: str
    spirit_id: str
    quest: QuestStateResponse | None = None


class DialogueRequest(BaseModel):
    """
    對話請求（SDD 第8.5節）。

    `user_input` 的長度上限先用保守值。SDD 提到 `prompt_max_chars` 會依實測調整，
    但那要等真的接上 Gemini、看到成本數據之後才有依據。
    """

    user_input: str = Field(min_length=1, max_length=500)

    @field_validator("user_input")
    @classmethod
    def user_input_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("user_input 不可為空白字元")
        return value


class DialogueResponse(BaseModel):
    """
    對話回應（issue #42 可用版）。

    `tts` 為 `None` 時，路由層會用 `response_model_exclude_none=True` 把這個
    欄位整個從 JSON 拿掉，不會序列化成 `"tts": null`——TTS 合成失敗時是「這輪
    沒有語音」，不是「有一個空的語音物件」，兩者對客戶端的處理分支意義不同
    （同 B10 `tts.py` 模組說明）。安全邊界（B4）與完整 Prompt 組裝（B2）見
    issue #45。
    """

    reply_text: str
    # 'canned' = 命中預寫招呼；'generated' = 未命中，交給 Gemini 生成
    # （B1 呼叫失敗時的降級台詞也算在 'generated' 裡——那是 B1 自己的責任，
    # 見 gemini.py 模組說明；這一層看不出、也不需要看出兩者的差別）。
    source: str
    tts: TTSResult | None = None


class OrientationInfo(BaseModel):
    """
    SDD v2.1 §10.2：3DoF 定向用的靈魂方位設定（issue #30）。

    `bearing_deg`：相對召喚點的方位角，真北 0°、順時針。`height_offset_m`：
    垂直偏移。兩者包成巢狀物件（不是攤平兩個欄位），對齊 SDD 的回應範例，
    也讓客戶端能把「方位設定」整包傳給 `OrientationService`。
    """

    bearing_deg: float
    height_offset_m: float


class SpiritResponse(BaseModel):
    """
    `GET /api/v1/spirits/{placeId}` 的回應。

    **欄位名是對外契約，跟資料庫欄位名脫鉤。** 0002 把 DB 欄位改成
    `spirit_id` / `display_name` / `*_radius_meters` 之後，這裡靠
    `validation_alias` 從 ORM 物件讀新名字，但輸出仍是舊的 JSON key——
    Unity client 的 `SpiritDto` 用 `[JsonProperty("place_id")]` 寫死了那些
    名字，改動等於要發一版客戶端。

    改 DB 欄位名不需要動客戶端，這正是兩者脫鉤的用途；反過來說，**要改這裡的
    欄位名時，必須連同 client 一起改**。
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    place_id: str = Field(validation_alias="spirit_id")
    name: str = Field(validation_alias="display_name")
    latitude: float
    longitude: float
    summon_radius_m: int = Field(validation_alias="summon_radius_meters")
    # 客戶端要靠這個畫出「150m 內淡淡發光、50m 內完全點亮」的三段式標記（S7）。
    # 少了它，客戶端只能把 150 寫死在自己這邊——那條路一旦走了，之後調整半徑
    # 就得同時改後端與發版客戶端。
    sense_radius_m: int = Field(validation_alias="sense_radius_meters")
    is_active: bool
    orientation: OrientationInfo

    @model_validator(mode="before")
    @classmethod
    def _nest_orientation(cls, data: Any) -> Any:
        """
        `bearing_deg`／`height_offset_m` 在 `Spirit` ORM 是攤平欄位（issue
        #30：SDD 的回應範例要巢狀 `orientation`，但底層資料表沒有理由跟著
        巢狀化——那兩個值只在這支 API 才需要包成一包）。這裡在驗證前把
        ORM 物件轉成 dict、組出 `orientation`，其餘欄位名維持原樣讓既有的
        `validation_alias` 映射繼續生效。
        """
        if isinstance(data, dict):
            if "orientation" in data:
                return data
            return {
                **data,
                "orientation": {
                    "bearing_deg": data.get("bearing_deg", 0.0),
                    "height_offset_m": data.get("height_offset_m", 0.0),
                },
            }

        return {
            "spirit_id": data.spirit_id,
            "display_name": data.display_name,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "summon_radius_meters": data.summon_radius_meters,
            "sense_radius_meters": data.sense_radius_meters,
            "is_active": data.is_active,
            "orientation": {
                "bearing_deg": data.bearing_deg,
                "height_offset_m": data.height_offset_m,
            },
        }


class QuestListItem(BaseModel):
    """
    `GET /quests/daily` 的單筆任務（issue #33 SDD v1 §8.7）。

    `spirit_id` 不是資料庫欄位，是從 `quest_id` 反推的（見
    `quests.spirit_id_for_quest`）——`quest_progress` 表本身沒有這一欄。
    """

    quest_id: str
    spirit_id: str
    status: QuestStatus  # 同 QuestStateResponse.status（issue #30），同一個 enum
    attempts_today: int


class QuestListResponse(BaseModel):
    quests: list[QuestListItem]


class QuestCompleteRequest(BaseModel):
    """
    `POST /quests/{questId}/complete`（issue #34）。

    `completion_evidence` 刻意是不驗證內容的 dict：後端確定性規則判定完成
    與否是由呼叫端（客戶端的可驗證微任務邏輯）自己保證的前提，這裡不重新
    驗一次任務內容——CONTEXT.md「可驗證微任務」的定義是「以後端確定性規則
    驗證完成與否」，而完成條件本身屬於任務設計，不是這支端點的職責。
    """

    completion_evidence: dict = Field(default_factory=dict)


class QuestCompleteResponse(BaseModel):
    """
    issue #34（前半：確定性規則判定完成 ＋ 共鳴入帳）／#43（後半：接上敘事
    包裝與解鎖故事）。

    `unlock_stories` 是 **list**，不是單一 `unlock_story | null`——issue #34
    的原始回應範例只想到「跨一個門檻」的情況，但 #43 AC3 明訂一次入帳跨過
    多個門檻時每個 stage 都要各有一段故事（`resonance.ResonanceResult
    .newly_unlocked_stages` 本身也是 list，同一個理由：只取最後一個會讓
    中間那段靜默消失）。空 list 就是「沒跨門檻」，不需要另外一個
    `newly_unlocked_stages` 欄位重複同一件事——每個 `UnlockStory.stage`
    已經帶著這個資訊。
    """

    quest_wrapper_text: str | None = None
    resonance_value: int
    unlock_stories: list[UnlockStory] = Field(default_factory=list)


class ResonanceQueryResponse(BaseModel):
    """`GET /resonance/{spiritId}`（issue #35 SDD v1 §8.9）。"""

    spirit_id: str
    resonance_value: int
    stage: int
    next_threshold: int | None


class ProfileResonanceItem(BaseModel):
    spirit_id: str
    resonance_value: int
    stage: int


class ProfileResponse(BaseModel):
    """
    `GET /profile`（issue #36，v2.1 §10.3 漏列，補回；SDD v1 §8.10）。

    純身體自己的表直查，不呼叫腦袋——`quests`／`resonance` 兩個陣列長度不必
    相同（玩家可能對某靈魂有共鳴但沒有任務進度列，反之亦然）。
    """

    quests: list[QuestListItem]
    resonance: list[ProfileResonanceItem]


class MemorySummaryItem(BaseModel):
    """
    `GET /players/me/memory-summary` 的單筆記憶（issue #37）。

    刻意不含 `embedding`——向量是內部實作，對玩家無意義，回應會因此暴增
    數十 KB（AC 明訂檢查）。
    """

    summary_text: str
    created_at: datetime


class MemorySummaryResponse(BaseModel):
    """依 `spirit_id` 分組（issue #37 AC2）。"""

    memories_by_spirit: dict[str, list[MemorySummaryItem]]


class AvatarAssetResponse(BaseModel):
    """`GET /assets/{avatarId}`（issue #38，v2.1 §7.4）。"""

    avatar_id: str
    bundle_url: str
    version: str
