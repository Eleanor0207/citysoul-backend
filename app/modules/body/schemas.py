import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class QuestStateResponse(BaseModel):
    """
    SDD 第8.4節的 `quest` 欄位。

    `status` 可能是 `in_progress` / `completed` / `daily_limit_reached`，
    最後一個只存在於回應中，不是資料庫狀態（見 models.QuestProgress）。

    型別是 `Literal` 而不是裸 `str`：契約凍結前（#30）這三個值只活在中文
    註解裡，OpenAPI 產出的是無型別的 `string`，client 端拿不到 enum，也
    無法用編譯器擋住打錯字的魔術字串。
    """

    quest_id: str
    status: Literal["in_progress", "completed", "daily_limit_reached"]
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
    對話回應。

    ⚠️ **這是走通端到端用的最小版本**，目前只做 B12 快速問候比對——命中回預寫
    台詞，未命中回人工預寫的 fallback。完整版（Gemini 生成、TTS 語音、安全邊界、
    Prompt 組裝）見 issue #42／#45。

    `tts` 欄位刻意還不存在：SDD v2.1 §10.1 定義它只含 `audio_url`，等 B10（#21）
    落地才會加上。現在放一個永遠是 null 的欄位只會讓客戶端寫出無用的處理分支。
    """

    reply_text: str
    # 'canned' = 命中預寫招呼；'fallback' = 未命中，回人工預寫台詞。
    # 客戶端不需要據此改變行為，但除錯與觀察命中率時很有用。
    source: str


class OrientationResponse(BaseModel):
    """
    SDD v2.1 §10.2：靈魂相對召喚點的方位設定，供客戶端 3DoF 定向服務使用。

    包成巢狀物件而不是兩個平鋪欄位，讓客戶端能把「方位設定」當成一個可
    整包傳給 `OrientationService` 的值，對齊 SDD 的回應範例。
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

    `orientation` 不能靠 `from_attributes` 自動從 ORM 物件的平面欄位長出來
    ——router 端改成明確用關鍵字建構這個 model（其他回應本來就是這樣寫）。
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
    orientation: OrientationResponse


class QuestCompleteRequest(BaseModel):
    """
    `POST /api/v1/quests/{questId}/complete` 的請求（SDD §7.5）。

    `completion_evidence` 目前不影響判定——完成條件是後端確定性規則
    （CONTEXT.md：不由 LLM 判定），而垂直切片階段的規則就是「帶著有效的
    相遇憑證送出」。欄位先收下來，等真的有需要驗證的證據型任務時再用；
    現在不收的話，客戶端之後要加回來又是一次破壞性變更。
    """

    completion_evidence: dict = Field(default_factory=dict)


class QuestCompleteResponse(BaseModel):
    """
    SDD §7.5 的完成回應。

    `quest_wrapper_text` 與 `unlock_story` **在這張票（#34）永遠是 null**
    ——它們需要腦袋生成（B11／B2），拆給 #43。§7.5 本來就允許
    `unlock_story` 為 null，所以這是合法的完整回應，不是半成品。
    """

    quest_wrapper_text: str | None = None
    resonance_value: int
    unlock_story: str | None = None


class DailyEventResponse(BaseModel):
    """
    `GET /api/v1/spirits/{placeId}/daily-event` 的回應（S10，#26）。

    `source` 區分內容從哪一層保底鏈路來的（今天的快取／前一天的快取／
    人工預寫），純粹方便觀察，客戶端不需要據此改變行為——三種情況對
    玩家來說都是「看到一段當日情境文字」，沒有分支要處理。
    """

    place_id: str
    event_date: date
    narrative_text: str
    source: Literal["cached_today", "cached_previous_day", "fallback"]


class ErrorResponse(BaseModel):
    """
    統一的錯誤回應契約模型（#30）。

    形狀對齊 FastAPI `HTTPException` 既有的 `{"detail": ...}`——這裡只是讓
    契約描述它，**不改變任何現有錯誤的實際 JSON 形狀**。自動觸發的 422
    驗證錯誤形狀不同（`detail` 是陣列），不用這個模型，FastAPI 已經自動
    宣告過。
    """

    detail: str
