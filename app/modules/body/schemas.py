import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.brain.tts import TTSResult


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
    """

    quest_id: str
    status: str
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
