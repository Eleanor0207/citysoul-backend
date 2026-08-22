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


class DistrictEntryRequest(BaseModel):
    """Coordinates used for one server-side district entry evaluation."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DistrictEntryResponse(BaseModel):
    """Result of a district check; no boundary or raw coordinates are returned."""

    district_id: str | None
    entry_granted: bool
    arc_id: str | None


# 任務狀態的合法值，會在 OpenAPI 產出真正的 `enum`。
#
# 以前這三個值只活在中文註解裡，`status` 產出的是裸 `type: string`——客戶端在
# 整個 API 最狀態密集的欄位上拿不到任何型別安全，SDD v2.1 §11.2.1 的 Unity
# 硬規則 5「Enum 採寬鬆解析」也因此無 enum 可解析。
#
# 任務狀態只反映資料庫狀態；attempts_today 是觀測欄位，不會產生額外狀態。
QuestStatus = Literal["in_progress", "completed"]


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
    # 'canned'    = 命中預寫招呼，沒有呼叫模型
    # 'generated' = Gemini 生成
    # 'fallback'  = 生成失敗或無法組裝，回人工預寫台詞
    # 'refused'   = B4 輸入端安全檢查擋下，**生成一次都沒有被呼叫**
    #               （只有 `spirits.safety_gate_enabled` 的靈魂會出現）
    # 客戶端不需要據此改變行為，但除錯與觀察命中率時很有用。
    source: str
    # 依空行切好的段落，供客戶端逐段推播（一次跳出整段文字像在讀說明書）。
    #
    # `reply_text` 保留完整內容且**不會消失**——分段是額外資訊，不是替代品。
    # 舊版客戶端忽略這個欄位仍然正確，這是刻意的相容性設計。
    #
    # ⚠️ 分段不代表內容完整。回應若被 token 上限截斷，這裡就是幾段加起來仍然
    # 少了結尾——完整性由 prompt 的長度指示與 `gemini_max_output_tokens` 負責。
    segments: list[str] = []
    tts: TTSResult | None = None
    suggested_questions: list[str] = Field(default_factory=list)


class InventoryItemResponse(BaseModel):
    """
    玩家持有的一件道具（待辦 P3 第 21 項）。

    `story_text` 為 null 是正常的：徽章、紀念品本來就可能只有 id 沒有長文，
    客戶端顯示名稱就好。有值時那段文字是**經人工審核的成品**（來自
    `brain.story_strings`），原樣顯示，不經模型。
    """

    item_id: str
    item_type: str
    acquired_at: datetime
    source_quest_id: str | None = None
    story_text: str | None = None


class InventoryResponse(BaseModel):
    """
    `GET /api/v1/inventory` 的回應。

    一件都沒有時回**空陣列**，不是 404——沒有道具是正常的起始狀態，不是
    「找不到」（同 `/spirits` 的規則）。
    """

    items: list[InventoryItemResponse] = Field(default_factory=list)


class CurrentWeatherResponse(BaseModel):
    """
    `GET /api/v1/weather/current` 的回應（backend#75／SDD §20.5）。

    ⚠️ **回應裡沒有任何位置資訊**——沒有回傳查詢座標、沒有地名、沒有格點編號。
    請求帶了座標進來，回應不該再把它送回去；那只會讓這個值出現在客戶端的 log
    與快取裡（同 `LandmarkPhotoResponse` 不回傳任何影像痕跡的理由）。

    這三個值**不經過任何模型**。天氣仍然不得進入 B9 的生成輸入（§20.5.1）。
    """

    # 攝氏。單位固定，不隨地區變——客戶端顯示成「26°」，不做換算。
    temperature_c: float

    # 在地化的天氣描述，直接顯示給玩家，例如「多雲時晴」。
    condition_text: str

    # 機器可讀的狀況列舉，例如 `PARTLY_CLOUDY`。客戶端日後要換圖示時用得到，
    # 現在可以忽略——先帶著，免得那天要改契約。
    condition_type: str


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
    # backend#48：配額用完／LLM 生成失敗的角色口吻文案，跟著靈魂資料一起帶下來，
    # 讓客戶端遇到這兩種狀況時不必再多打一支 API。人格卡沒有生效版本，或版本
    # 有但這兩個欄位還沒填時都是 None——客戶端要自己準備通用保底文案。
    quota_fallback_text: str | None = None
    llm_failure_fallback_text: str | None = None

    @classmethod
    def from_spirit(cls, spirit, persona=None) -> "SpiritResponse":
        """
        把 ORM 的 `Spirit` 轉成對外回應，含平鋪欄位 → `orientation` 的收攏。

        `persona` 是選填的：呼叫端沒有查（或查不到生效人格）就傳 None，
        兩個 fallback 欄位回 None，不是這支函式的責任去查資料庫。
        """
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
            quota_fallback_text=getattr(persona, "quota_fallback", None),
            llm_failure_fallback_text=getattr(persona, "llm_failure_fallback", None),
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


class CompletedBeatResponse(BaseModel):
    """一個已完成的 beat 與它的完成時間。"""

    beat_id: str
    completed_at: datetime


class StoryArcStateResponse(BaseModel):
    """
    `GET /api/v1/story-arcs/{arcId}/state` 的回應（backend#72）。

    唯讀查詢，不觸發任何寫入——跟 `GET /resonance/{spiritId}` 同一個原則。

    `eligible_beat_ids` 是**前置已全部滿足、但玩家還沒完成**的 beat，不是
    「這條 arc 全部的 beat」——玩家不需要看到還鎖著的節點長什麼樣，那會
    洩漏未解鎖的劇情內容。
    """

    arc_id: str
    completed_beat_ids: list[str] = Field(default_factory=list)
    # 每個已完成 beat 的完成時間（`players_story_progress.triggered_at`）。
    #
    # 同一個 beat 只能記一次（主鍵 `(player_id, beat_id)`），所以那個時間就是
    # 完成時間。終點 beat 的時間＝玩家走完這條主線的時間。
    completed_beats: list[CompletedBeatResponse] = Field(default_factory=list)
    # 玩家在這條 arc 上已經定下來的劇情變數（`story_focus` 等）。決定結局那一頁
    # 的措辭；一旦寫入就不會再變。
    variables: dict[str, str] = Field(default_factory=dict)
    eligible_beat_ids: list[str] = Field(default_factory=list)
    story_completed: bool = False


class BeatAdvanceRequest(BaseModel):
    """
    `POST /api/v1/story-arcs/{arcId}/beats/{beatId}/advance` 的請求（backend#72）。

    `chosen_option_ids` 是玩家在這個節點看／選了哪些選項。後端**只認這個 beat
    自己節點裡的 id**，客戶端送別的東西會被忽略——變數的值來自內容，不是來自
    請求，否則玩家可以自己指定結局。

    整個 body 可以省略：多數 beat 沒有選項。
    """

    chosen_option_ids: list[str] = Field(default_factory=list)


class BeatAdvanceResponse(BaseModel):
    """`POST /api/v1/story-arcs/{arcId}/beats/{beatId}/advance` 的回應（backend#72）。"""

    beat_id: str
    already_completed: bool
    story_completed: bool
    completed_beat_ids: list[str] = Field(default_factory=list)
    # 這次推進發出去的道具。**重複提交時是空的**——道具只發一次，所以客戶端
    # 不能靠這個欄位判斷「玩家現在有什麼」，那要問 `GET /inventory`。
    granted_item_ids: list[str] = Field(default_factory=list)
    # 這次寫進去的劇情變數。**set_once**——已經有值時是空的，那不是錯誤。
    set_variables: dict[str, str] = Field(default_factory=dict)


class ScriptOptionResponse(BaseModel):
    """一個觀察點／選項（backend#72）。"""

    option_id: str
    # 玩家**選之前**看到的字，例如「她的側影」。人工撰寫、原樣顯示，不經 LLM。
    text: str
    # 選之後年代簿說的話。null 代表這個選項沒有回應。
    #
    # ⚠️ 客戶端**不要**在選單上顯示 reply——那是選擇的結果，先攤開來就等於
    # 把三個答案都給了玩家，「看向哪裡」這個動作會失去意義。
    reply: str | None = None
    # 選這個會寫進哪個劇情變數，例如 `{"story_focus": "person"}`。
    #
    # 推進這個 beat 時，把選到的 `option_id` 放進 advance 的請求，後端就會
    # 依這裡的對應寫進 `players_story_variables`（0030）。**set_once**：一條
    # arc 上同一個變數只寫得進去一次，之後回看不覆蓋（文件 §2.3）。
    sets: dict[str, str] = Field(default_factory=dict)


class ScriptNodeResponse(BaseModel):
    """一個劇情節點。`type` 是 `line`（台詞）或 `choice`（一組選項）。"""

    type: Literal["line", "choice"]
    # line 專用。`speaker` 可能是「年代簿」「信」這種非角色的敘事者。
    speaker: str | None = None
    text: str | None = None
    # choice 專用。
    options: list[ScriptOptionResponse] = Field(default_factory=list)
    # 看過幾個選項才能繼續（文件 §2.3：不強制看完）。
    min_viewed_to_proceed: int = 1
    # False 代表可以多選、已看過的仍可回看。
    exclusive: bool = False


class InfoCardResponse(BaseModel):
    """一張劇情資訊卡（backend#72／0030）。

    ⚠️ **史實與虛構是兩個欄位，客戶端不該把它們接成一段。** 文件 §1 要求每張卡
    明確區分「史實可考」與「本作故事」——那是這個專案對地標的基本承諾，不是
    排版偏好。
    """

    card_id: str
    historical_text: str | None = None
    fiction_text: str | None = None


class BeatScriptResponse(BaseModel):
    """
    `GET /api/v1/story-arcs/{arcId}/beats/{beatId}/script` 的回應（backend#72）。

    ⚠️ **只回傳玩家已經走到的節點**（已完成，或前置全部滿足）。還鎖著的 beat
    回 403——腳本就是劇情內容本身，能查等於能先看完結局。這跟
    `StoryArcStateResponse.eligible_beat_ids` 不洩漏未解鎖節點是同一條規則。

    `nodes` 是空陣列時代表這個 beat 沒有台詞（例如只發道具的節點），**不是
    錯誤**。
    """

    beat_id: str
    # 腦袋那邊的角色身分；沒有角色的節點（序章、結局）是 null。
    character_id: str | None = None
    # 哪一個召喚點在說話。客戶端要的是這個，不是 `character_id`。
    spirit_id: str | None = None
    nodes: list[ScriptNodeResponse] = Field(default_factory=list)
    info_cards: list[InfoCardResponse] = Field(default_factory=list)


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


class SuggestedQuestionsResponse(BaseModel):
    """B14 questions available before the first dialogue turn."""

    questions: list[str] = Field(default_factory=list)
    is_fallback: bool = False


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


class QuestListItem(BaseModel):
    """
    `GET /api/v1/quests/daily` 的單一任務（SDD v1 §8.7）。

    `spirit_id` **不在 `quest_progress` 表裡**，是從 `quest_id` 的命名慣例
    反推的（見 `queries.quest_view`）。客戶端仍然需要它才知道這個任務屬於哪個
    地標，所以它是對外契約的一部分。
    """

    quest_id: str
    spirit_id: str
    status: QuestStatus
    attempts_today: int
    # 'daily' 是推導出來的任務，沒有目錄資料；'story' 是有人寫過內容的主線任務。
    quest_type: str = "daily"
    # 以下三個只有目錄裡的任務才有。daily 型任務是 None／空陣列，**不是缺資料**
    # ——它沒有可編輯的內容，標題由客戶端自己決定怎麼稱呼。
    title: str | None = None
    intro: str | None = None
    steps: list[dict] = Field(default_factory=list)


class QuestsDailyResponse(BaseModel):
    """沒有任何任務時 `quests` 是空陣列，不是 404——冷啟動是正常狀態。"""

    quests: list[QuestListItem] = Field(default_factory=list)


class ResonanceProgressResponse(BaseModel):
    """
    `GET /api/v1/resonance/{spiritId}` 的回應（SDD v1 §8.9）。

    `stage` 與 `next_threshold` 一律由 `resonance_value` 重算。
    已滿階時 `next_threshold` 為 null。
    """

    spirit_id: str
    resonance_value: int
    stage: int
    next_threshold: int | None = None


class ProfileResonanceItem(BaseModel):
    """
    Profile 裡的單一靈魂共鳴。

    刻意**沒有** `next_threshold`：Profile 是總覽，要看下一個門檻就去
    `GET /resonance/{spiritId}`。多回一個欄位不痛，但它會變成第二個必須跟
    單一查詢保持一致的地方。
    """

    spirit_id: str
    resonance_value: int
    stage: int


class ProfileResponse(BaseModel):
    """
    `GET /api/v1/profile`（SDD v1 §8.10）。

    ⚠️ v2.1 §10.3 的端點清單**漏列了這支**，已確認為漏列而非移除。

    兩個陣列的長度**不必相同**：一個靈魂可以有共鳴值但沒有任務進度（例如只
    收藏過紀念照片）。
    """

    quests: list[QuestListItem] = Field(default_factory=list)
    resonance: list[ProfileResonanceItem] = Field(default_factory=list)


class MemoryItem(BaseModel):
    """
    單筆記憶摘要。

    🔒 **沒有 embedding 欄位。** 向量是內部實作，對玩家沒有意義，而且 768 維
    浮點數會讓回應暴增數十 KB。
    """

    summary_text: str
    created_at: datetime


class MemoryGroup(BaseModel):
    """依靈魂分組的記憶。"""

    spirit_id: str
    memories: list[MemoryItem] = Field(default_factory=list)


class MemorySummaryResponse(BaseModel):
    """
    `GET /api/v1/players/me/memory-summary`（#37）。

    🔒 只含該玩家自己的記憶（CONTEXT.md「玩家記憶僅屬單一玩家」）。
    世界記憶不含任何玩家輸入，兩者不得混用。

    B8 nightly batch（#40）未上線前，這裡回的是既有的 `dialogue_summary`
    來源記錄——那是**預期行為，不是缺陷**。
    """

    groups: list[MemoryGroup] = Field(default_factory=list)


class AvatarAssetResponse(BaseModel):
    """
    `GET /api/v1/assets/{avatarId}`（v2.1 §7.4／#38）。

    `version` 讓客戶端判斷快取是否過期：**改了要變，沒改要穩定不變**。
    後者一樣重要——每次回傳新值的話，客戶端每次啟動都會重抓整包。

    **無需驗證**：資產位置不是玩家資料。
    """

    avatar_id: str
    catalog_url: str
    bundle_url: str
    version: str


class PushRegisterRequest(BaseModel):
    """
    `POST /api/v1/push/register`（S11／#39）。

    只收 token。**不收位置、不收裝置型號**——推播是通知不是內容，這條路徑不
    需要知道玩家在哪裡或用什麼手機。
    """

    push_token: str = Field(min_length=1, max_length=256)


class PushSubscriptionResponse(BaseModel):
    """
    訂閱狀態。

    刻意**不回傳 `push_token`**：客戶端本來就知道自己送了什麼，回傳它只是讓
    這個值多存在於一個地方（log、快取、錯誤回報）。
    """

    is_subscribed: bool
