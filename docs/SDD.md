# 城市靈魂 AR 遊戲 — 系統設計文件（SDD）

> 本文件整合 `city_soul_AR_document_index.md` 索引下所有現行權威文件（#0, #2–#10）與 `docs/adr/0001-gcp-vertex-ai-for-mvp-dialogue.md`，
> 依索引第3節「推翻對照表」套用每份文件的最新修正版本，產出單一、內部一致的技術設計文件。
> 本文件不新增任何未在來源文件中出現的規格決策；標示為【提案待確認】的段落沿用原文件的標註，代表該處仍需負責人拍板，不是本文件擅自拍板。
> 歷史快照文件 `city_soul_AR_project_mainpoint.md` 除作為決策脈絡外不再引用；其內容凡與本文件衝突者，一律以本文件為準。

---

## 1. 產品概述

**核心迴圈**：玩家在指定地標完成「到場、召喚、當日情境短對話、可驗證微任務」的一次完整體驗，並有理由在隔天回來查看變化。

**首批玩家（TA）**：住在台北、約 18–35 歲，喜歡城市探索、角色互動與拍照分享的繁中使用者。MVP 語言統一為台灣繁體中文（人格卡、介面、對話、TTS、通知）。

**封閉測試垂直切片**：以單一「天文館靈魂」驗證核心迴圈、內容生產、成本與隱私邊界，通過驗證後才擴展到首發十個城市靈魂集合（七個高辨識度地標＋三個在地故事濃厚場域）。

**產品邊界（Avoid 清單，摘要）**：
- 不做真 3D / AR Foundation 空間錨定，視覺呈現為 2.5D Billboard（Rive/Lottie 分層動畫）
- 不做背景定位追蹤，僅在玩家主動開啟召喚/相機/感應模式時前景即時運算
- 世界記憶不含任何玩家輸入；玩家記憶僅屬單一玩家
- 可驗證微任務由後端確定性規則判定完成與否，不由 LLM 判定
- 「城市靈魂」是地標的擬人化集體意識，不扮演任何特定真人或歷史人物

**技術棧（ADR-0001 確認）**：Vertex AI 單一快速模型（不做 Flash/Pro 分流）、FastAPI 後端、PostgreSQL + pgvector、Redis、Google Cloud TTS、Flutter 前端。模型服務失敗時一律回退人工預寫台詞，確保召喚流程不中斷。

---

## 2. 模組邊界（腦袋／身體／臉）

系統依耦合最低原則切成三個模組，唯一跨模組關聯欄位是 `player_id`；身體與腦袋各自擁有自己的資料表，不互相唯讀存取對方的表。

### 2.1 腦袋（LLM／RAG／Memory）

- Vertex AI 單一快速模型串接與失敗回退策略（B1）
- Prompt 組裝引擎：人格卡＋當日情境＋記憶片段（B2）
- 人格卡載入與版本管理（B3）
- 角色安全邊界檢查層（輸入端過濾）（B4）
- 史實邊界規則注入（B5）
- 長期記憶（pgvector）語意檢索與寫入（B6）
- 短期記憶（Redis）存取層（B7）
- 記憶摘要 nightly batch job（B8）
- 當日情境內容生成邏輯（只做生成，不管排程/分發）（B9）
- TTS 串接＋viseme 時間軸產出（B10）
- 共鳴值解鎖敘事生成（B11）
- 快速問候比對層（B12，混合式聊天）
- 地標視覺辨識（B13，雲端 Gemini 多模態，即用即丟）

### 2.2 身體（互動／任務／遊戲介面／行銷）

- GPS/Fused Location 定位模組（S1）
- 在場驗證與召喚半徑判定（S2）
- 感應驗證（150m，`/sense`）
- 防作弊：mock location 偵測、速度合理性、二次驗證（S3）
- 任務進度資料表＋狀態機判定邏輯（S4，身體自讀寫）
- 共鳴值資料表＋判定邏輯（S5，身體自讀寫）
- 匿名玩家身分系統／帳號升級綁定（S6）
- 遊戲介面 UI：地圖/召喚點（S7）、任務列表/Profile（S8）
- 引導提問元件（S9）
- 當日情境排程觸發＋快取＋對外 API（S10）
- 訊號推播（S11）
- 紀念照片：本機保存/分享＋呼叫 B13 雲端辨識（S12）
- 行銷/分享功能（S13）

### 2.3 臉（動畫／2.5D／口型）

- 角色美術造型設計（Character Creator 產線）（F1）
- 精靈圖／序列幀烘焙管線（F2）
- Rive/Lottie 動畫實作：idle、揮手、轉身、撐傘、服裝與光影切層（F3）
- 相機疊圖渲染：Flutter 相機畫面＋Avatar 疊層（F4）
- 口型同步播放器：只播放腦袋給的 viseme 時間軸，不做音訊分析（F5）
- 素材版本管理與上傳（Cloud Storage + CDN）（F6）
- App 端素材下載與快取邏輯（F7）

### 2.4 模組邊界設計原則

| 決策 | 內容 |
|---|---|
| 當日情境 | 內容生成（腦袋）與排程/快取/對外 serve（身體）拆分 |
| viseme 時間軸 | 由腦袋在呼叫 TTS 時一併取得，跟音檔一起交給臉；臉只播放不分析 |
| 共鳴值解鎖 | 身體判定達標 → 呼叫腦袋生成敘事 |
| 記憶 schema owner | 結構化欄位（任務進度、共鳴值）屬身體；語意化欄位（對話摘要向量）屬腦袋 pgvector；僅靠 `player_id` 關聯 |

腦袋與身體的跨模組呼叫，只剩「需要生成內容」時才發生（見第5節內部介面），沒有任何純資料讀寫的跨模組呼叫，兩模組理論上可獨立測試。

**Schema 設計硬約束**：不得新增任何 `location_history` / `movement_trace` 類型的表。環境感知互動（走近轉身、經過古蹟講故事等）限定為前景即時運算判斷，不落地儲存，對應「原始 GPS 只在驗證期間使用後即丟棄，不形成軌跡」的隱私原則。

---

## 3. 資料庫 Schema

### 3.1 身體（Cloud SQL for PostgreSQL；開發期用本地 docker-compose）

```sql
-- 匿名玩家身分（S6）
CREATE TABLE players (
    player_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id      VARCHAR(128) NOT NULL UNIQUE,
    account_id      VARCHAR(128) NULL,
    usage_tier_id   VARCHAR(32) NOT NULL REFERENCES usage_tiers(tier_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 地標／召喚點基本資料
CREATE TABLE spirits (
    place_id        VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    latitude        DOUBLE PRECISION NOT NULL,
    longitude       DOUBLE PRECISION NOT NULL,
    summon_radius_m INTEGER NOT NULL DEFAULT 50,    -- 召喚（在場）半徑
    sense_radius_m  INTEGER NOT NULL DEFAULT 150,   -- 感應（聊天）半徑
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- 任務進度（S4：身體自己讀寫）
CREATE TABLE quest_progress (
    player_id               UUID NOT NULL REFERENCES players(player_id),
    quest_id                VARCHAR(64) NOT NULL,
    status                  VARCHAR(16) NOT NULL DEFAULT 'in_progress', -- in_progress / completed
    progress_value          INTEGER NOT NULL DEFAULT 0,
    attempts_today          INTEGER NOT NULL DEFAULT 0,
    attempts_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    current_token_issued_at TIMESTAMPTZ NULL,
    completed_at            TIMESTAMPTZ NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, quest_id)
);

-- 共鳴值（S5：身體自己讀寫）
CREATE TABLE resonance (
    player_id       UUID NOT NULL REFERENCES players(player_id),
    spirit_id       VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    resonance_value INTEGER NOT NULL DEFAULT 0,
    stage           INTEGER NOT NULL DEFAULT 0,   -- 共用門檻 10/40/100 的三階結果
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, spirit_id)
);

-- 共鳴事件帳本：確保單一收藏或任務不重複加值
CREATE TABLE resonance_events (
    resonance_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id          UUID NOT NULL REFERENCES players(player_id),
    spirit_id          VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    source_type        VARCHAR(32) NOT NULL,  -- 'encounter_collection' / 'quest'
    source_id          VARCHAR(128) NOT NULL,
    amount             INTEGER NOT NULL,       -- encounter_collection=10；quest=20
    awarded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (player_id, source_type, source_id)
);

-- 相遇收藏（S12：不存原始照片、GPS座標或影像雜湊）
CREATE TABLE encounter_collections (
    collection_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id           UUID NOT NULL REFERENCES players(player_id),
    place_id            VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    landmark_recognized BOOLEAN NOT NULL,
    resonance_awarded   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (player_id, place_id)
);

-- 當日情境快取（S10）
CREATE TABLE daily_event_cache (
    place_id        VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    event_date      DATE NOT NULL,
    content         JSONB NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (place_id, event_date)
);

-- 推播訂閱（S11）
CREATE TABLE push_subscriptions (
    player_id       UUID PRIMARY KEY REFERENCES players(player_id),
    push_token      VARCHAR(256) NOT NULL,
    is_subscribed   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 使用量分級定義
CREATE TABLE usage_tiers (
    tier_id      VARCHAR(32) PRIMARY KEY,   -- 'closed_beta', ...
    display_name VARCHAR(64) NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每個分級底下各資源類型的配額（正規化設計：新增資源類型只需多寫一筆資料，不需 ALTER TABLE）
CREATE TABLE usage_tier_limits (
    tier_id       VARCHAR(32) NOT NULL REFERENCES usage_tiers(tier_id),
    resource_type VARCHAR(32) NOT NULL,
    limit_value   INTEGER NOT NULL,
    PRIMARY KEY (tier_id, resource_type)
);
```

> **禁止事項**：不得新增 `location_history` / `movement_trace` 類型的表（見第2.4節）。

### 3.2 腦袋（pgvector；邏輯上獨立 schema，例如 `brain.*`）

```sql
-- 人格卡（B3：人工審核過的定義載入）
CREATE TABLE brain.persona_cards (
    spirit_id       VARCHAR(64) NOT NULL,
    version         INTEGER NOT NULL,
    content         JSONB NOT NULL,   -- 見第7節正式schema
    reviewed_by     VARCHAR(128) NOT NULL,
    reviewed_at     TIMESTAMPTZ NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (spirit_id, version)
);

-- 長期記憶語意向量（B6）
CREATE TABLE brain.memory_embeddings (
    memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id       UUID NOT NULL,     -- 僅語意關聯，不建跨schema實體FK
    spirit_id       VARCHAR(64) NOT NULL,
    summary_text    TEXT NOT NULL,
    embedding       VECTOR(768) NOT NULL,
    source          VARCHAR(32) NOT NULL,  -- 'dialogue_summary' / 'nightly_batch'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON brain.memory_embeddings USING ivfflat (embedding vector_cosine_ops);

-- 短期記憶不落地在PostgreSQL，走Redis（B7）
-- Redis key: session:{player_id}:{spirit_id} -> 近期對話輪次（TTL依session逾時設定）
```

`player_id` 是唯一跨模組關聯欄位，兩邊都不互相唯讀存取對方的表，只透過內部介面呼叫時把 `player_id` 當參數傳遞。

---

## 4. 使用量分級（Usage Tier）機制

**resource_type 列舉**：

| resource_type | 意義 | 計算週期 | 封測初始值 |
|---|---|---|---|
| `dialogue_calls_daily` | 每日 `/dialogue` 呼叫次數上限 | 每日（Asia/Taipei午夜重置） | 50 |
| `daily_tokens` | 每日 Gemini token 用量上限（input+output） | 每日 | 150,000 |
| `prompt_max_chars` | 單次 `user_input` 字數上限（單筆靜態檢查，非累計） | 每次請求 | 500 |
| `api_rate_per_minute` | 每分鐘 API 呼叫次數上限 | 滑動窗口 | 6 |
| `landmark_recognition_calls_daily` | 每日 `/landmark-photo` 呼叫次數上限 | 每日 | 10 |

> 上述數字皆為封測期保守起始值，非最終定案；`usage_tier_limits` 資料可直接調整，不需重新部署。

**執行層**：配額檢查用 Redis 計數器（`quota:{player_id}:{resource_type}:{YYYY-MM-DD,Asia/Taipei}`），每日型用 `INCRBY`+TTL 到當天午夜，`api_rate_per_minute` 用滑動窗口 key（分鐘級時間戳+TTL 60秒）。`prompt_max_chars` 是唯一不用計數器的類型，屬單次請求靜態檢查。`usage_tier_limits` 內容變動頻率低，應用啟動時載入記憶體快取即可。

150m 感應聊天與 50m 正式召喚後對話**共用同一組**每日配額，不分開計算。

---

## 5. 內部介面函式簽章

MVP 階段單一 FastAPI 服務，先實作為 Python 函式呼叫；簽章設計已考慮未來升級為 REST/gRPC 不需大改。

```python
from pydantic import BaseModel
from datetime import date, datetime


class DailyEventContent(BaseModel):
    place_id: str
    event_date: date
    narrative_text: str
    mood_tags: list[str]
    generated_at: datetime


class VisemeFrame(BaseModel):
    time_ms: int
    viseme: str

class TTSResult(BaseModel):
    audio_url: str
    viseme_timeline: list[VisemeFrame]


class DialogueResponse(BaseModel):
    reply_text: str
    tts: TTSResult


class UnlockStory(BaseModel):
    player_id: str
    spirit_id: str
    stage: int
    story_text: str
    tts: TTSResult | None = None


class QuestNarrative(BaseModel):
    player_id: str
    quest_id: str
    wrapper_text: str


# ── 腦袋 → 對外暴露（身體呼叫）──────────────────────────

def generate_daily_event_content(place_id: str, event_date: date) -> DailyEventContent:
    """B9．僅使用日期/節日、地標官方公開活動、人工審核素材作為輸入；
    無合格輸入或生成失敗時回退人工預寫台詞。純內容生成，不處理快取/過期。"""
    ...

def generate_dialogue_response(
    player_id: str, spirit_id: str, user_input: str, session_context: dict,
) -> DialogueResponse:
    """對應 POST /api/v1/spirits/{placeId}/dialogue。
    內部依序：B4安全邊界檢查 → B2 Prompt組裝 → B1 Gemini呼叫 → B10 TTS+viseme。"""
    ...

def generate_unlock_story(player_id: str, spirit_id: str, stage: int) -> UnlockStory:
    """B11．呼叫方：身體，須先寫完自己的resonance表再呼叫，順序不可顛倒。"""
    ...

def generate_quest_wrapper(player_id: str, quest_id: str) -> QuestNarrative:
    """B2延伸．呼叫方：身體，須先寫完quest_progress表再呼叫。"""
    ...

def recognize_landmark(image_bytes: bytes, spirit_id: str) -> bool:
    """B13．呼叫Vertex AI Gemini多模態辨識，image_bytes只在記憶體處理，
    辨識完成（成功或失敗）立即捨棄，不寫入任何持久化儲存、不留作訓練資料。
    Gemini呼叫失敗/逾時回傳False（fallback，不阻擋任務完成）。"""
    ...


# ── 臉 → 消費腦袋輸出（被動接收，不主動呼叫腦袋）──────────

def play_viseme_timeline(audio_url: str, viseme_timeline: list[VisemeFrame]) -> None:
    """F5．只播放，不分析音訊，資料完全來自 DialogueResponse.tts。"""
    ...


# ── Token 與配額 ──────────────────────────────────────

def issue_session_token(player_id: str) -> str:
    """S6．核發90天session token，建立或重複呼叫時都發新的一張。"""
    ...

def issue_encounter_token(player_id: str, spirit_id: str) -> str:
    """S2．在場驗證通過後核發，15分鐘效期。"""
    ...

def issue_sense_token(player_id: str, spirit_id: str) -> str:
    """S2-new．感應驗證通過後核發，30分鐘效期，不觸發任務判定。"""
    ...

def verify_session_token(token: str) -> str:
    """回傳player_id，無效/過期時raise TokenInvalidError。"""
    ...

def verify_encounter_or_sense_token(token: str, expected_spirit_id: str) -> tuple[str, str]:
    """回傳 (player_id, token_purpose)；spirit_id不符或過期時raise TokenInvalidError。"""
    ...

def check_and_apply_quest_failure(db, player_id: str, spirit_id: str) -> None:
    """S2呼叫。檢查是否有過期未完成的quest_progress，若有則attempts_today += 1。
    純狀態更新side effect，呼叫方(/summon)呼叫完後重新查一次拿最新狀態。"""
    ...

def check_and_consume_quota(player_id: str, resource_type: str, amount: int = 1) -> None:
    """在B4安全檢查之前呼叫（dialogue API第一道關卡）。
    超過配額raise QuotaExceededError，由API層轉成429回應。"""
    ...
```

**呼叫順序硬規則**：身體必須「先寫自己的表、再呼叫腦袋生成敘事」，不能反過來——否則腦袋生成內容時傳入的 `stage`/`quest_id` 狀態會跟資料庫不一致。

---

## 6. Token 設計

系統使用三種 JWT（HS256，簽章金鑰存於 GCP Secret Manager，Cloud Run 環境變數掛載；本機開發用獨立的假金鑰）：

| | Session Token | Sense Token | Encounter Token |
|---|---|---|---|
| 用途 | 證明「我是哪個玩家」 | 證明「在150m感應範圍內」 | 證明「在50m召喚範圍內」 |
| 效期 | 90天（可續） | 30分鐘 | 15分鐘 |
| 何時核發 | `POST /players` | `POST /sense` 感應驗證通過後 | `POST /summon` 在場驗證通過後 |
| 保護的API | quests/daily、resonance查詢、memory-summary等玩家層級查詢 | dialogue（僅對話能力） | dialogue、quests/complete、landmark-photo |
| 內含GPS座標 | 否 | 否 | 否 |
| 沒帶會怎樣 | 401，前端應重新呼叫`POST /players` | 401，需重新走`/sense` | 401，需重新走召喚流程 |

**Session Token claims**：`{sub, purpose: "session", iat, exp}`（90天）。App端存於裝置安全儲存區（`flutter_secure_storage`），每次啟動用舊token換新（延長效期，玩家無感）。

**Sense Token claims**：`{sub, spirit_id, purpose: "sense", iat, exp}`（30分鐘）。快過期（剩1-2分鐘）且仍在150m內時，前端**靜默**重新呼叫 `/sense` 換發，玩家UI無中斷。

**Encounter Token claims**：`{sub, spirit_id, purpose: "encounter", iat, exp}`（`exp-iat`固定900秒）。不含`jti`（不查資料庫、不做撤銷名單）。過期即該次任務嘗試失敗，不主動續期。

**Dialogue API 驗證邏輯**：`Session Token（必要）+ (Encounter Token 或 Sense Token，至少一張)`。持有Sense Token者不可呼叫`quests/complete`、不可觸發相機疊圖畫面。

三種token驗證邏輯必須用不同中介層區分，不可共用同一套驗證邏輯（避免相遇憑證被當session用、或session token被當相遇憑證用而使在場驗證形同虛設）。

---

## 7. 業務規則

| # | 規則 | 決策 |
|---|---|---|
| 1 | GPS容許誤差 | 固定半徑（`summon_radius_m`/`sense_radius_m`）直接判定，不動態放寬；`summon_radius_m`是已把GPS常見誤差考慮進去的「有效半徑」，非地標實際佔地範圍 |
| 2 | 召喚冷卻 | 召喚本身無冷卻，可重複觸發；可驗證微任務發放頻率是每玩家對每靈魂、每日一次；對話互動不受限 |
| 3 | Mock location防作弊 | 觀察期：偵測到只記錄log（含player_id、spirit_id、偵測時間、偵測依據），不擋下召喚 |
| 4 | 相遇憑證技術實作 | JWT自簽，不查資料庫，效能優先，接受「無法中途撤銷」的取捨 |
| 5 | 任務逾時 | 相遇憑證（15分鐘）過期＝該次任務嘗試失敗 |
| 6 | 失敗重試次數 | 當天最多失敗重試3次，超過需等隔天（Asia/Taipei午夜）重置 |
| 7 | 共鳴值入帳時機 | 任務完成當下即時入帳並檢查門檻，是`quest/complete`內部動作，非前端獨立呼叫 |
| 8 | 時區 | 所有「每日一次」「隔天重置」統一以 **Asia/Taipei（UTC+8）午夜** 為基準（已由驗收標準文件正式拍板，取代原提案假設） |

### 7.1 在場驗證與召喚（S2）

```
distance = haversine(玩家GPS, spirit.latitude, spirit.longitude)
在場成立 if distance <= spirit.summon_radius_m
```

Mock location偵測到即寫入應用程式日誌（Cloud Logging，不需新表），但不阻擋`/summon`請求。

### 7.2 感應驗證（`/sense`，150m）

```
distance <= spirit.sense_radius_m（預設150m）→ 核發sense_token（30分鐘）
不觸發任何任務判定、不核發任務
```

### 7.3 兩段式距離互動

| 範圍 | 名稱 | 可做什麼 |
|---|---|---|
| 150m內 | 感應範圍 | 跟靈魂聊天（混合式：簡單問候用固定語，深入對話才呼叫LLM） |
| 50m內 | 召喚範圍 | 觸發正式召喚 → 跳轉相機疊圖畫面 → 任務、共鳴值 |
| 150m外 | — | 地圖標記維持預設樣式，聊天輸入不可用 |

對話延續：短期記憶（Redis）key為`session:{player_id}:{spirit_id}`，與拿的是哪張token無關，玩家從150m聊天走進50m觸發正式召喚時對話記錄自動延續。

### 7.4 可驗證微任務生命週期

```
不存在 → in_progress → completed
              ↓（憑證過期且任務未完成）
           失敗嘗試（attempts_today += 1）
              ↓（attempts_today < 3）
           回到in_progress，可重新召喚再挑戰
              ↓（attempts_today >= 3）
           當天鎖定，等隔天（Asia/Taipei午夜）reset
```

**「失敗」判定方式**：不新增「回報失敗」API，改由下一次`/summon`時後端被動判定——查`quest_progress`是否有`status='in_progress'`且對應`encounter_token`已過期超過15分鐘；是則`attempts_today += 1`。這比前端主動回報更符合「以後端確定性規則驗證完成與否」的精神。

### 7.5 共鳴值入帳連動

```
POST /api/v1/quests/{questId}/complete
  → 身體：驗證任務規則型完成條件（後端確定性規則，非LLM判定）
  → 身體：寫入 quest_progress.status = 'completed'
  → 身體：resonance.resonance_value += 20（MVP固定值）
  → 身體：檢查resonance_value是否跨過10/40/100門檻
    → 若跨過：呼叫腦袋 narrative.generate_unlock_story(player_id, spirit_id, stage)
  → 身體：呼叫腦袋 narrative.generate_quest_wrapper(player_id, quest_id)
  → 回傳前端：{ quest_wrapper_text, resonance_value, unlock_story (可能為null) }
```

相遇收藏每個地標僅加一次共鳴值10點（`encounter_collections`的`UNIQUE(player_id, place_id)`約束）；`resonance_events`帳本以`UNIQUE(player_id, source_type, source_id)`確保不重複入帳。

### 7.6 混合式聊天（B12）

玩家輸入先比對「固定招呼表」（人格卡schema新增`canned_greetings`欄位，人工審核）→ 命中回預寫語，零成本、不呼叫Gemini；沒命中才進B4→B2→B1→B10完整流程。

### 7.7 地標視覺辨識（雲端Gemini版，推翻CONTEXT.md原「本機辨識」定義）

原CONTEXT.md「本機地標辨識」（裝置端判斷、不上傳原始影像）與「紀念照片不上傳」定義已被此方案正式修正：

```
玩家在實景疊圖任務中拍照
  → App用multipart/form-data直接上傳照片到後端
  → 身體（S12）收到照片，存於記憶體（不落地）
  → 身體呼叫腦袋B13 recognize_landmark(image_bytes, spirit_id)
  → 腦袋呼叫Vertex AI Gemini多模態辨識，回傳布林值
  → 身體取得布林值後立即捨棄image_bytes（不寫入任何儲存體，含Cloud Storage）
  → 身體回傳landmark_recognized給App
  → 提交任務完成時，此布林值作為completion_evidence一部分傳入/quests/{questId}/complete
```

「即用即丟」約束：照片只在記憶體中處理，全程不寫磁碟/Cloud Storage/資料庫欄位；辨識完成（成功或失敗）立即釋放；不做模型訓練或稽核用途保留。

任務完成判定不受影響：仍是GPS+拍照動作的確定性規則判定，不經LLM；只有「要不要給特別徽章」這個加分項目才跑Gemini辨識，失敗不阻擋任務完成。

`encounter_collections`定義維持不變：不存原始照片或GPS座標。

**風險提醒**：隱私風險等級應由原本的🟢低風險調升（照片會短暫觸及後端/雲端）；多模態呼叫成本疊加進既有🔴「AI對話成本」風險桶。

---

## 8. 完整外部 API 契約

> 沒特別寫「驗證」表示不需要任何token。

### 8.1 `POST /api/v1/players`
**驗證**：無
**Request**：`{ "device_id": "string" }`
**Response 200**：`{ player_id, device_id, account_id, created_at, session_token }`
**錯誤**：`422` device_id無效；同一device_id重複呼叫非錯誤，回傳既有player＋重新核發新session token

### 8.2 `GET /api/v1/spirits/{placeId}`
**驗證**：無（公開資訊）
**Response 200**：地標基本資料、召喚點座標
**錯誤**：`404` placeId不存在或is_active=false

### 8.3 `POST /api/v1/sense`
**驗證**：Session Token
**Request**：`{ spirit_id, latitude, longitude }`
**處理**：驗證session→拿player_id；distance<=sense_radius_m核發sense_token（30分鐘）；不觸發任務判定
**Response 200**：`{ sense_token, spirit_id }`
**錯誤**：`401` session無效；`403` 不在感應範圍；`404` spirit_id不存在

### 8.4 `POST /api/v1/summon`
**驗證**：Session Token
**Request**：`{ spirit_id, latitude, longitude, gps_accuracy_m, is_mock_location }`
**處理流程**：
1. 驗證session token→拿player_id
2. `is_mock_location=true`：記錄log，不阻擋
3. 算距離，`distance<=summon_radius_m`才在場成立，否則`403`
4. 查quest_progress是否有過期未完成任務，是則`attempts_today+=1`
5. 核發encounter_token（15分鐘）
6. 判斷今天任務發放狀態，回傳對應quest欄位

**Response 200**：`{ encounter_token, spirit_id, quest: { quest_id, status, attempts_today } | null }`
**錯誤**：`401`；`403` 不在召喚範圍；`404` spirit_id不存在

### 8.5 `POST /api/v1/spirits/{placeId}/dialogue`
**驗證**：Session Token +（Encounter Token 或 Sense Token）
**Request**：`{ user_input: string }`
**處理流程**：驗證兩張token（encounter/sense的spirit_id須與路徑一致）→ 配額檢查 → B12固定招呼比對 → 未命中則B4安全檢查→B2 Prompt組裝→B1 Gemini呼叫→B10 TTS+viseme → B7短期記憶寫入
**Response 200**：`DialogueResponse { reply_text, tts: { audio_url, viseme_timeline } }`
**錯誤**：`401`；`403` spirit_id不符或token過期；`422` user_input為空或超過`prompt_max_chars`（不消耗配額）；`429` 配額超過；`503`場景改為`200`＋人工預寫fallback台詞（Gemini失敗/逾時不視為錯誤）

### 8.6 `GET /api/v1/spirits/{placeId}/daily-event`
**驗證**：無（公開世界狀態）
**Response 200**：`DailyEventContent`
**錯誤**：排程尚未產生快取時回傳前一天內容保底，不回傳404空畫面

### 8.7 `GET /api/v1/quests/daily`
**驗證**：Session Token
**Response 200**：`{ quests: [{ quest_id, spirit_id, status, attempts_today }] }`

### 8.8 `POST /api/v1/quests/{questId}/complete`
**驗證**：Session Token + Encounter Token
**Request**：`{ completion_evidence: object }`（依任務類型而定，可含`landmark_recognized`布林值）
**處理**：見7.5節
**Response 200**：`{ quest_wrapper_text, resonance_value, unlock_story: { stage, story_text } | null }`
**錯誤**：`401`；`409` 今天已完成過；`422` completion_evidence格式不符或確定性規則判定未達成（不呼叫Gemini）

### 8.9 `GET /api/v1/resonance/{spiritId}`
**驗證**：Session Token
**（原`POST .../check`已修正為唯讀查詢，避免誤用觸發副作用）**
**Response 200**：`{ spirit_id, resonance_value, stage, next_threshold: integer | null }`

### 8.10 `GET /api/v1/players/me/memory-summary`
**驗證**：Session Token
**Response 200**：`{ quests: [...], resonance: [{ spirit_id, resonance_value, stage }] }`（純身體自己的表直查，不呼叫腦袋）

### 8.11 `GET /api/v1/assets/{avatarId}`
**驗證**：無（公開CDN資源）
**Response 200**：`{ asset_url, version }`

### 8.12 `POST /api/v1/quests/{questId}/landmark-photo`
**驗證**：Session Token + Encounter Token
**Request**：`multipart/form-data`，欄位`photo`
**處理流程**：驗證兩張token→檢查`landmark_recognition_calls_daily`配額→身體收到照片（記憶體）→呼叫腦袋B13→立即捨棄照片→回傳結果
**Response 200**：`{ landmark_recognized: boolean }`
**錯誤**：`401`/`403`；`422` 檔案格式不符或過大（不消耗配額）；`429` 配額用完；Gemini失敗/逾時不視為錯誤，回傳`200`＋`landmark_recognized:false`

> 此端點與`/quests/{questId}/complete`分開設計：前端先呼叫此端點拿辨識結果，再把布林值包進`completion_evidence`送進`/complete`。

---

## 9. 人格卡正式 Schema

`brain.persona_cards.content`（JSONB）：

```json
{
  "schema_version": 1,
  "core_personality": "string，核心性格一句話定調",
  "speaking_style": "string，說話風格、語氣、口頭禪等",
  "emotional_core": "string，情感核心/角色動機",
  "factual_boundary": {
    "known_facts": "string，明確已知史實範圍",
    "folklore": "string，民間傳說範圍，允許提及但要標明是傳說",
    "imagination": "string，角色可自由發揮想像的範圍，但不能表述為事實"
  },
  "taboo_topics": ["string", "..."],
  "quest_themes": ["string", "..."],
  "not_this_character": "string，明確排除的角色詮釋方向",
  "canned_greetings": [
    { "trigger_phrases": ["string", "..."], "response_text": "string，人工預寫，經審核" }
  ]
}
```

`schema_version`供B3載入邏輯判斷版本相容性。`canned_greetings`對應B12混合式聊天機制。

---

## 10. Prompt 組裝引擎（B2）設計骨架

> 【提案待確認】組裝順序為業界常見骨架，實際Top-K、記憶輪數、token預算數字須由腦袋模組負責人依實測成本/品質數據拍板，不由本文件單方面決定。

```
System Instruction 組成順序：
  1. 人格卡（B3）：core_personality + speaking_style + factual_boundary + taboo_topics
  2. 史實邊界規則（B5）：注入「不對敏感歷史武斷定論」規則文字
  3. 角色安全邊界（B4）：安全檢查在輸入端先過濾，不重複塞進system instruction

User Turn 組成順序：
  1. 當日情境摘要（B9產出的narrative_text，若今天有）
  2. 長期記憶（B6）：語意檢索Top-K（建議先抓K=3）
  3. 短期記憶（B7）：本session最近幾輪對話（建議上限6輪）
  4. 玩家這次的user_input
```

**待定案項目**（封測後依實測數據調整）：Top-K與短期記憶輪數的最終值；當日情境摘要是否設長度上限；Gemini Flash/Pro分流規則（目前ADR-0001已確認MVP不做分流，統一用單一快速模型）。

---

## 11. 前端（Flutter）架構

### 11.1 主導覽結構

底部三分頁：探索地圖（首頁，對應S7）、任務列表（S8）、共鳴值/Profile（S8）。相機疊圖畫面不是分頁，是從地圖畫面modal push的獨立畫面，召喚成立後跳轉。

### 11.2 畫面清單

| 畫面 | 說明 |
|---|---|
| 啟動畫面 | 背景呼叫`POST /players`（首次）或帶出既有session，無登入UI |
| 探索地圖 | OSM圖磚地圖、三段式靈魂標記、150m內嵌入對話、底部當日情境卡片 |
| 相機疊圖畫面 | 50m觸發後跳轉，漸顯尋找靈魂、可收合對話窗（預設展開）、引導提問元件 |
| 任務列表 | 任務狀態、嘗試次數 |
| 共鳴值/Profile | 共鳴值進度、相遇收藏、記憶摘要 |

共用元件：對話UI（訊息列表+輸入框+引導提問）獨立可重用，同時被探索地圖（sense_token模式）與相機疊圖畫面（encounter_token模式）嵌入。對話延續靠後端Redis session key天然成立，前端只要維持同一個Riverpod provider狀態不重置即可。

### 11.3 技術選型

| 項目 | 選型 | 理由 |
|---|---|---|
| 狀態管理 | Riverpod | 非同步友善、boilerplate少，方便token自動更新的依賴注入 |
| 路由 | GoRouter | 官方方案，處理分頁+modal疊加的返回鍵行為較乾淨 |
| 安全儲存 | flutter_secure_storage | 存install_id、session_token |
| 網路層 | dio + 攔截器 | 統一處理自動帶token、sense_token快過期靜默換發、429轉角色口吻fallback文案 |
| 定位 | geolocator | 前景區間輪詢，不使用geofencing套件（見11.5節省電修正） |
| 角色動畫 | Rive/Lottie | 沿用既有決策 |

### 11.4 分層架構

```
lib/
  core/        # 主題、常數、錯誤處理、共用UI元件
  services/
    token_manager.dart       # 管理三種token生命週期
    location_service.dart    # 前景區間輪詢封裝（地圖30秒／相機連續）
    api_client.dart          # dio實例+攔截器
  features/
    map/
    ar_camera/
    quest/
    profile/
    dialogue/    # 共用對話元件，被map與ar_camera嵌入
```

**TokenManager**：Session Token（90天，App啟動時換新）、Sense Token（30分鐘，快過期時靜默重呼`/sense`換發）、Encounter Token（15分鐘，過期即任務失敗，不主動續期）。

**LocationService**：地圖畫面前景開啟時每30秒查一次位置；相機疊圖畫面開啟時連續讀取供漸顯插值；App切到背景或關閉地圖畫面時停止輪詢，不做背景定位。

### 11.5 地圖與距離偵測

**地圖底圖**：Google Maps SDK（互動引擎）+ OSM資料經PyraCanny線稿萃取、Fooocus生成風格化圖磚，以Tile Overlay疊在Google Maps SDK上。

**地圖標記三段式視覺**：150m外預設樣式；150m內淡淡發光提示；50m內完全點亮、可點擊召喚按鈕。50m觸發流程為玩家主動點擊召喚按鈕才呼叫`/summon`並跳轉，不自動跳轉。「你還不夠靠近」提示不顯示精確距離數字，僅模糊提示。

**距離偵測技術方案**：

| 畫面 | 技術方案 | 原因 |
|---|---|---|
| 地圖畫面（150m/50m門檻） | 原生Geofencing API（Android/iOS） | 只回報進入/離開事件，最省電，天然不產出精確距離數字 |
| 相機疊圖畫面（50m內漸顯） | 前景持續讀GPS插值 | 玩家已主動開啟相機，本身高耗電情境，符合前景即時反應原則 |

> 待驗證風險：雙Geofence（150m+50m同心圓）在部分Android/iOS版本上的穩定性，建議Sprint 2做POC；若不穩定，備案退回較低頻率前景輪詢（每10-15秒），仍不顯示精確數字。

### 11.6 相機疊圖畫面

對話窗預設展開、可收合；靈魂依GPS距離深淡/縮放漸變，越接近召喚點越清晰完整；無遮擋判斷、無真實透視景深，符合2.5D Billboard既有決策。

### 11.7 開發期基礎設施

開發階段Postgres+Redis先用本地docker-compose，不急著佈建GCP服務；之後進staging/正式環境時只換連線字串/部署目標，不影響schema或業務邏輯程式碼。

---

## 12. 2.5D Billboard 視覺呈現

**技術棧確認**：2.5D Billboard（Rive/Lottie分層動畫）+ 自建串接Vertex AI Gemini，不使用真AR/3D引擎（已推翻原mainpoint的Unity+AR Foundation構想）。

| 效果 | 實作方式 | 複雜度 |
|---|---|---|
| 街角揮手 | Rive/Lottie idle動畫循環播放 | 低 |
| 走近轉身 | App前景持續讀GPS距離，門檻值觸發動畫切換（側身→面向鏡頭） | 中——距離門檻切換預先做好的動畫，非真實空間偵測 |
| 下雨撐傘 | 當日情境JSON含天氣旗標，前端依旗標換道具圖層 | 低 |
| 黃昏服裝/光影 | 依裝置本地時間切換服裝圖層+疊加半透明暖色濾鏡 | 低 |
| 經過古蹟講故事 | 同距離觸發機制，達門檻自動彈出對話，內容由Gemini依當日情境生成 | 中 |

**技術取捨**：無遮擋判斷、無真實透視景深（靠GPS距離模擬縮放）、鏡頭大角度傾斜時靈魂仍是平面貼紙。這是「實景疊圖任務不做空間錨定、平面偵測、真AR渲染」既有原則下的合理取捨。

**第三方工具評估結論**：視覺呈現層維持自建2.5D Billboard（不採用3D AI Avatar / Ready Player Me）；美術製作可用Character Creator做前期造型設計後烘焙成精靈圖序列幀；對話大腦維持自建Prompt+Vertex AI Gemini（Inworld/Convai因用量計費與嘴型同步整合成本，列為Roadmap選配，暫不採用）。

---

## 13. Roadmap（MVP 之後）

五項技術壁壘功能，明確排除在MVP範圍外：

1. **城市人格引擎**：城市靈魂可持續成長的人格、知識與語氣
2. **多Agent協作**：不同城市靈魂互相討論、分工合作
3. **世界記憶（持久化）**：玩家、城市靈魂與活動的可延續共同記憶——**需重新界定邊界**：不得與CONTEXT.md「世界記憶不包含任何玩家輸入」原則衝突，設計時須避免玩家互動意外進入世界記憶
4. **AI導演**：依玩家狀態/位置/時間/天氣動態生成劇情、任務、事件
5. **AR情境感知**：結合定位、時間、天氣、環境與視覺資訊的深度理解

> 「多Agent協作」「AI導演」都會大幅增加LLM呼叫次數，與🔴高風險「AI對話成本與延遲」直接相關，排入Roadmap時需一併重新估算成本模型。

---

## 14. 尚未解決的缺口

| 缺口 | 影響範圍 | 建議處理時機 |
|---|---|---|
| `canned_greetings`實際觸發語與預寫回覆內容 | 人格卡內容 | 需敘事負責人撰寫，Sprint3（B4/B2）前補齊 |
| 150m發光、相機疊圖漸顯效果的具體美術參數 | 臉模組美術細節 | Sprint3-4（F3美術動畫）前補齊 |
| 429配額用完時各情境角色口吻fallback文案 | 腦袋模組內容 | Sprint4（B9/B10）前補齊 |
| Prompt組裝Top-K/記憶輪數/Flash-Pro分流數字 | 腦袋Prompt調校 | 先用文件建議預設值開發（K=3、短期記憶6輪），封測後依實測調整 |
| `usage_tiers`分級策略本身 | 產品層級決策 | 上線前需要，開發期不影響 |
| 雙Geofence穩定性POC | 前端定位 | Sprint2 |
| 前端UI層視覺細節驗收標準（美術呈現、動畫參數） | 臉模組 | 架構骨架已定，視覺細節驗收標準待補 |

---

## 15. 驗收標準總覽

完整Given-When-Then驗收標準見 `city_soul_AR_acceptance_criteria.md`（AC1–AC14，共15節）。摘要涵蓋範圍：

1. 在場驗證與召喚（AC1）
2. 感應驗證（AC2）
3. Mock location防作弊觀察期（AC3）
4. 可驗證微任務生命週期（AC4）
5. 共鳴值（AC5）
6. Token驗證邊界（AC6）
7. 混合式聊天固定招呼層（AC7）
8. 配額（AC8）
9. Fallback/服務降級（AC9）
10. 地圖標記狀態機（AC10）
11. 距離偵測技術省電方案（AC11）
12. 相機疊圖畫面（AC12）
13. 開發環境（AC13）
14. 地標視覺辨識（AC14）

開發過程中每完成一個功能，應回頭對照對應AC編號逐條驗收。

---

## 16. Sprint 排程（提案）

依WBS依賴關係排出的7-Sprint提案（非任一原始文件的既有定案表，排序原則：無依賴的基礎工作包先做；身體/腦袋依決策4可平行開發；臉模組排中段；跨模組整合排在依賴項完成之後；Roadmap五項不排入這7個Sprint）：

| Sprint | 主題 | 腦袋 | 身體 | 臉 |
|---|---|---|---|---|
| 1 | 地基 | B3人格卡載入、B7短期記憶 | S1 GPS、S6匿名玩家身分 | F1角色美術造型 |
| 2 | 核心串接起步 | B1 Gemini串接、B6長期記憶schema+檢索 | S2在場驗證與召喚半徑 | F2精靈圖烘焙管線 |
| 3 | 判定邏輯與安全層 | B4安全邊界檢查、B2 Prompt組裝、B12快速問候比對 | S3防作弊、S4任務進度表、S5共鳴值表 | F3 Rive/Lottie動畫 |
| 4 | 對話輸出鏈 | B5史實邊界注入、B9當日情境生成、B10 TTS+viseme、B13地標辨識 | — | F4相機疊圖渲染、F5口型同步播放器 |
| 5 | UI與敘事生成收尾 | B11共鳴值解鎖敘事生成 | S7地圖UI、S8任務/Profile UI、S9引導提問、S10當日情境排程/快取/API | — |
| 6 | 對外整合＋素材交付 | （整合測試：dialogue、daily-event、resonance、quests/complete） | S11訊號推播、對外API全部串通 | F6素材版本管理、F7素材下載/快取 |
| 7 | 收尾與上線準備 | B8記憶摘要nightly batch | S12紀念照片（呼叫B13雲端辨識）、S13行銷/分享、端到端QA | 素材最終校驗、跨模組整合回歸測試 |

---

## 17. 附錄：ADR

- `docs/adr/0001-gcp-vertex-ai-for-mvp-dialogue.md`：MVP對話統一使用GCP Vertex AI單一快速模型，不導入多模型路由或高階模型分流；服務失敗時回退人工撰寫台詞。
