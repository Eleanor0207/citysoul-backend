import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.brain.tts import TTSResult


class ErrorResponse(BaseModel):
    """
    所有錯誤回應的統一形狀（SDD v2.1 §11.2.1）。

    **這不是新的錯誤格式，是對既有格式的描述。** 形狀刻意對齊 FastAPI
    `HTTPException` 本來就會產出的 `{"detail": "..."}`，一個位元組都沒改。

    存在的理由是讓契約說得出錯誤長什麼樣。在這之前，錯誤回應完全不在 OpenAPI
    文件裡——SDD v2.1 §10.3 明訂「錯誤碼全數不變」代表錯誤碼是契約的一部分，
    但機器可讀的那份契約看不到它們，客戶端只能回頭讀規格散文，這正好違背
    「單一真相來源」的初衷。

    具名之後，客戶端 codegen 會產出**單一**錯誤 DTO，錯誤處理可以寫成一份共用
    程式碼，而不是每支端點各寫一次。
    """

    detail: str


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


# 任務狀態的合法值，會在 OpenAPI 產出真正的 `enum`。
#
# 以前這三個值只活在中文註解裡，`status` 產出的是裸 `type: string`——客戶端在
# 整個 API 最狀態密集的欄位上拿不到任何型別安全，SDD v2.1 §11.2.1 的 Unity
# 硬規則 5「Enum 採寬鬆解析」也因此無 enum 可解析。
#
# 三個值的語意分界不變：`in_progress` / `completed` 是資料庫狀態；
# `daily_limit_reached` **只存在於 API 回應**，是查詢當下依 `attempts_date`
# 算出來的結果，不落地（存進資料庫隔天就是錯的）。
QuestStatus = Literal["in_progress", "completed", "daily_limit_reached"]


class QuestStateResponse(BaseModel):
    """
    SDD 第8.4節的 `quest` 欄位。

    `status` 是 `QuestStatus` 而非裸 `str`，讓客戶端能生出真正的 C# enum，
    任務狀態比對由編譯器把關，不用比對魔術字串。
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
    對話回應（SDD v2.1 §10.1）。

    ⚠️ **這是 Phase 2 的「可用版」**（#42）：配額 → B12 招呼比對 → B1 生成 →
    B10 語音 → B7 短期記憶。B4 安全邊界與 B2 完整組裝屬 Phase 3（#45）。

    `tts` 可以是 null，那是**預期狀態而不是錯誤**：TTS 失敗時對話降級成純文字
    （SDD §8.5「模型失敗不視為錯誤」）。客戶端 F5 對此的處置是角色維持靜止
    口型、文字照常顯示——兩端的降級行為刻意銜接。

    🔒 `tts` 裡**只有 `audio_url`**。v2.1 §10.1 已移除 viseme 時間軸，對嘴由
    客戶端 uLipSync 即時分析負責（見 `brain/tts.py` 的模組註解）。
    """

    reply_text: str
    # 'canned' = 命中預寫招呼；'generated' = Gemini 生成；
    # 'fallback' = 生成失敗或無法組裝，回人工預寫台詞。
    # 客戶端不需要據此改變行為，但除錯與觀察命中率時很有用。
    source: str
    tts: TTSResult | None = None


class SpiritOrientation(BaseModel):
    """
    靈魂方位設定（SDD v2.1 §10.2），供客戶端 S14 的 3DoF 定向服務使用。

    做成巢狀物件而不是兩個平鋪欄位，是為了對齊 SDD v2.1 §10.2 的回應範例，
    也讓客戶端能把「方位設定」當成一個可整包傳給 `OrientationService` 的值，
    而不是每次都得手動把兩個散落的欄位湊起來。
    """

    # 相對召喚點的方位角：真北 0°、順時針。
    bearing_deg: float
    # 相對玩家視線高度的垂直偏移（公尺）。
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
    # 巢狀物件，資料庫是兩個平鋪欄位（`bearing_deg` / `height_offset_m`）。
    # 這層轉換由 `from_spirit()` 做，不靠 `from_attributes` 自動推——
    # ORM 物件上沒有 `orientation` 這個屬性可讀。
    orientation: SpiritOrientation

    @classmethod
    def from_spirit(cls, spirit) -> "SpiritResponse":
        """把 ORM 的 `Spirit` 轉成對外回應，含平鋪欄位 → `orientation` 的收攏。"""
        return cls(
            place_id=spirit.spirit_id,
            name=spirit.display_name,
            latitude=spirit.latitude,
            longitude=spirit.longitude,
            summon_radius_m=spirit.summon_radius_meters,
            sense_radius_m=spirit.sense_radius_meters,
            is_active=spirit.is_active,
            orientation=SpiritOrientation(
                bearing_deg=spirit.bearing_deg,
                height_offset_m=spirit.height_offset_m,
            ),
        )


class QuestCompleteRequest(BaseModel):
    """
    `POST /api/v1/quests/{questId}/complete` 的請求。

    `completion_evidence` 目前**不參與判定**——SDD 尚未定義它的結構。仍然收下來，
    是因為之後加規則時 API 形狀不該跟著變（那會是破壞性變更，見 §11.2.1）。
    """

    completion_evidence: dict = Field(default_factory=dict)


class UnlockStoryResponse(BaseModel):
    """一段解鎖敘事（SDD §7.5 的 `unlock_story` 物件）。"""

    stage: int
    story_text: str


class QuestCompleteResponse(BaseModel):
    """
    SDD §7.5 的完成回應。

    ## 為什麼有 `unlock_story` **和** `unlock_stories`

    §7.5 定義的是**單數** `unlock_story`，但 `newly_unlocked_stages` 是 list
    ——一次入帳理論上可能跨過多個門檻（#16 刻意的設計）。單數欄位表達不了那件事。

    所以兩個都有，各有明確職責：

    - `unlock_story`：**第一個**新解鎖的階段，維持 §7.5 的形狀與客戶端相容。
    - `unlock_stories`：**完整清單**，這是真相。

    MVP 的 +10／+20 跨不過兩個門檻，所以清單目前最多一個元素，兩者實質相同。
    但呼叫端不該假設這件事——每個新解鎖的 stage 都該有自己的一段敘事，漏掉中間
    那段是靜默的內容缺漏，不會有任何錯誤訊息提醒。

    `stage` 與 `newly_unlocked_stages` 不在 §7.5 的範例 body 裡，是 #34 的 AC
    要求「跨門檻時回應標示新達成 stage」才補上的。
    """

    quest_wrapper_text: str | None = None
    resonance_value: int
    unlock_story: UnlockStoryResponse | None = None
    unlock_stories: list[UnlockStoryResponse] = Field(default_factory=list)
    stage: int
    newly_unlocked_stages: list[int] = Field(default_factory=list)


class DailyEventResponse(BaseModel):
    """
    `GET /api/v1/spirits/{placeId}/daily-event` 的回應（S10／#26）。

    **公開世界狀態，不需要任何 token。**

    `is_fallback` 為 true 有兩種可能：內容是人工預寫保底，或是回退到了前一天的
    快取。客戶端**不需要**據此改變呈現——玩家看到的都該是一段正常的敘事。
    它存在是為了讓我們觀察排程的健康度。
    """

    narrative_text: str
    is_fallback: bool = False
    sources: list[str] = Field(default_factory=list)


class LandmarkPhotoResponse(BaseModel):
    """
    `POST /api/v1/quests/{questId}/landmark-photo` 的回應（S12／#44）。

    ⚠️ **回應裡沒有任何影像相關的東西**——沒有 URL、沒有雜湊、沒有尺寸。
    照片只在記憶體處理、辨識完立即捨棄（SDD §7.7），回應也不該留下它存在過的
    痕跡。

    `resonance_awarded` 為 false 有兩種可能：辨識失敗，或這個地標之前已經收藏過
    （`UNIQUE(player_id, place_id)`，每個地標只加一次 10 點）。兩者對玩家的意義
    不同，但都不是錯誤。
    """

    landmark_recognized: bool
    resonance_awarded: bool
    resonance_value: int
