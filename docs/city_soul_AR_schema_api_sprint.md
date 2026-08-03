# 城市靈魂 AR 遊戲 — Schema／內部介面／Sprint 整合文件

> 串接對象：《城市靈魂AR遊戲_專案重點整理》(mainpoint) ×《開發紀錄》(dev) ×《WBS 與 API 切分》(WBS-API)
> 本文件目的：把 WBS-API「下一步」清單第 1、2 項（資料庫 schema、內部介面函式簽章）做出來，
> 並把整份 WBS 對回 Sprint 排程，同時說明三份文件彼此的定位關係。

---

## 0. 三份文件怎麼串起來（先講結論，再看細節）

三份文件不是三個獨立版本，是**同一個決策鏈的三個階段快照**：

| 文件 | 角色 | 內容性質 |
|---|---|---|
| **mainpoint**（專案重點整理） | 最早定案的 MVP SPEC 基準線 | 「這是我們原本說好的規格」——AR/3D、Convai NPC、環境感知互動的邊界都寫在這 |
| **dev**（開發紀錄） | 對 mainpoint 的**決策更新**，逐項對照落差並收斂 | 「這些是後來實測/評估後改變的決定」——2.5D Billboard 取代 Unity+AR、自建 NPC 取代 Convai，並把環境感知互動限縮成「主動召喚當下即時判斷」以符合隱私原則 |
| **WBS-API**（WBS 與 API 切分） | 把 dev 定案後的架構**落地成可執行的工作包與介面** | 「這是照上面決定拆出來的模組、資料表、API」——腦袋／身體／臉三模組邊界、決策1–4 |

**這份文件（Schema/API/Sprint）是第四層**：把 WBS-API 第 3 節「下一步」尚未做的兩塊補完，並把已經拆好的工作包排進 Sprint。它不改變前三份文件的任何決策，純粹是把 WBS-API 已經定案的邊界（決策1–4）轉成可以直接建表、直接刻函式簽章的形式。

**特別標註一個 mainpoint → dev 的關鍵收斂，因為它會影響下面的 schema 設計：**
mainpoint 原本認為「走近轉身」「經過古蹟講故事」這類環境感知互動，需要背景定位追蹤，與「原始 GPS 只在驗證期間使用即丟棄、不形成軌跡」的隱私原則衝突。dev 把它重新界定為「限定在玩家主動開啟召喚/相機模式當下即時運算」，不算違反原則。**這代表下面的資料庫 schema 不需要、也不應該設計任何儲存玩家歷史移動軌跡的表**——這是三份文件疊加後對 schema 設計的直接約束，寫在這裡提醒未來不要不小心加回去。

---

## 1. 資料庫 Schema

延續 WBS-API 決策4：**結構化欄位（任務進度、共鳴值）屬於身體自己的表；語意化欄位（對話摘要向量）屬於腦袋的 pgvector 表；兩者只靠 `player_id` 關聯，不合併、不互相唯讀存取對方的表。**

### 1.1 身體（Cloud SQL for PostgreSQL）

```sql
-- 匿名玩家身分（S6）
CREATE TABLE players (
    player_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    install_id      VARCHAR(128) NOT NULL UNIQUE,   -- App 產生的隨機本地身分，不使用硬體裝置 ID
    account_id      VARCHAR(128) NULL,               -- 升級綁定帳號後才有值
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 地標／召喚點基本資料（供 S2 在場驗證、S7 地圖用）
CREATE TABLE spirits (
    place_id        VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    latitude        DOUBLE PRECISION NOT NULL,
    longitude       DOUBLE PRECISION NOT NULL,
    summon_radius_m INTEGER NOT NULL DEFAULT 50,     -- 在場驗證半徑
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- 任務進度（S4：身體自己讀寫，不依賴腦袋）
CREATE TABLE quest_progress (
    player_id       UUID NOT NULL REFERENCES players(player_id),
    quest_id        VARCHAR(64) NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'in_progress', -- in_progress / completed
    progress_value  INTEGER NOT NULL DEFAULT 0,
    completed_at    TIMESTAMPTZ NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, quest_id)
);

-- 共鳴值（S5：身體自己讀寫）
CREATE TABLE resonance (
    player_id       UUID NOT NULL REFERENCES players(player_id),
    spirit_id       VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    resonance_value INTEGER NOT NULL DEFAULT 0,
    stage           INTEGER NOT NULL DEFAULT 0,       -- MVP 所有靈魂共用門檻 10／40／100 的三階結果；傳給腦袋 narrative.generate_unlock_story
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, spirit_id)
);

-- 共鳴事件帳本：以來源確保單一收藏或任務不會重複加值
CREATE TABLE resonance_events (
    resonance_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id          UUID NOT NULL REFERENCES players(player_id),
    spirit_id          VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    source_type        VARCHAR(32) NOT NULL,  -- 'encounter_collection' / 'quest'
    source_id          VARCHAR(128) NOT NULL, -- 收藏 ID 或任務 ID；可含日期以區分每日任務
    amount             INTEGER NOT NULL, -- MVP：encounter_collection 為 10；quest 為 20
    awarded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (player_id, source_type, source_id)
);

-- 相遇收藏（S12：影像留在裝置，雲端只保存可用於共鳴與回憶的中繼資料）
CREATE TABLE encounter_collections (
    collection_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id           UUID NOT NULL REFERENCES players(player_id),
    place_id            VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    landmark_recognized BOOLEAN NOT NULL,
    resonance_awarded   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (player_id, place_id)
    -- 不保存原始照片、GPS 座標或影像雜湊
);

-- 當日情境快取（S10：排程觸發+快取+對外 serve，內容來自腦袋 B9）
CREATE TABLE daily_event_cache (
    place_id        VARCHAR(64) NOT NULL REFERENCES spirits(place_id),
    event_date      DATE NOT NULL,
    content         JSONB NOT NULL,                   -- 腦袋 B9 回傳的內容原樣存放
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
```

> **注意**：這裡刻意**沒有**任何 `location_history` / `movement_trace` 類型的表——對應第 0 節提到的隱私原則約束（GPS 只在驗證當下用，不形成軌跡）。若未來要做「走近轉身」的距離觸發（dev 文件 1.1 節），觸發判斷是**前景即時運算**，不落地儲存，這件事完全不需要新增資料表，是 App 端邏輯（F 模組）+ S2 既有的即時距離判定，不影響這裡的 schema。

### 1.2 腦袋（pgvector，可與身體同一個 PostgreSQL 執行個體，但邏輯上是獨立 schema，例如 `brain.*`）

```sql
-- 人格卡（B3：人工審核過的定義載入）
CREATE TABLE brain.persona_cards (
    spirit_id       VARCHAR(64) NOT NULL,
    version         INTEGER NOT NULL,
    content         JSONB NOT NULL,          -- 人格設定、語氣、史實邊界規則
    reviewed_by     VARCHAR(128) NOT NULL,
    reviewed_at     TIMESTAMPTZ NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (spirit_id, version)
);

-- 長期記憶語意向量（B6，含 schema）
CREATE TABLE brain.memory_embeddings (
    memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id       UUID NOT NULL,           -- 外鍵僅語意關聯，不建跨 DB/schema 的實體 FK 約束
    spirit_id       VARCHAR(64) NOT NULL,
    summary_text    TEXT NOT NULL,
    embedding       VECTOR(768) NOT NULL,    -- 依實際 embedding 模型維度調整
    source           VARCHAR(32) NOT NULL,    -- 'dialogue_summary' / 'nightly_batch' 等
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON brain.memory_embeddings USING ivfflat (embedding vector_cosine_ops);

-- 短期記憶不落地在 PostgreSQL，走 Redis（B7），這裡不建表，只記錄 key 命名慣例：
-- Redis key: session:{player_id}:{spirit_id} -> 近期對話輪次（TTL 依 session 逾時設定）
```

> `player_id` 是唯一跨模組關聯欄位，如 WBS-API 第 0 節所述：**兩邊都不互相唯讀存取對方的表**，只有透過內部介面呼叫時把 `player_id` 當參數傳過去。

---

## 2. 內部介面函式簽章

對應 WBS-API 第 3 節「內部介面」清單。MVP 階段是單一 FastAPI 服務，先以 Python 函式呼叫實作；介面簽章設計時已經考慮未來直接升級成 REST/gRPC 不需要大改。

```python
# ============================================================
# 共用資料模型（pydantic）
# ============================================================

from pydantic import BaseModel
from datetime import date, datetime
from typing import Literal


class DailyEventContent(BaseModel):
    place_id: str
    event_date: date
    narrative_text: str
    mood_tags: list[str]          # 例如 ["雨天", "黃昏"]，供臉模組選圖層用
    generated_at: datetime


class VisemeFrame(BaseModel):
    time_ms: int
    viseme: str                    # 對應口型代碼

class TTSResult(BaseModel):
    audio_url: str
    viseme_timeline: list[VisemeFrame]


class DialogueResponse(BaseModel):
    reply_text: str
    tts: TTSResult                 # 決策2：viseme 跟音檔一起交付，臉不做音訊分析


class UnlockStory(BaseModel):
    player_id: str
    spirit_id: str
    stage: int
    story_text: str
    tts: TTSResult | None = None   # 若解鎖故事也要語音播放，沿用同一組 TTS 結構


class QuestNarrative(BaseModel):
    player_id: str
    quest_id: str
    wrapper_text: str


# ============================================================
# 腦袋 → 對外暴露（身體呼叫）
# ============================================================

def generate_daily_event_content(place_id: str, event_date: date) -> DailyEventContent:
    """
    B9．當日情境內容生成。
    呼叫方：身體 S10（排程觸發時）。
    僅使用日期／節日、地標官方公開活動與人工審核素材作為輸入；LLM 只負責短敘事改寫，
    無合格輸入或生成失敗時回退人工預寫台詞。純內容生成，不處理快取/過期邏輯——這是身體 daily_event_cache 的職責。
    """
    ...


def generate_dialogue_response(
    player_id: str,
    spirit_id: str,
    user_input: str,
    session_context: dict,   # 由腦袋自己組裝：人格卡(B3) + 當日情境 + 短期記憶(B7) + 長期記憶(B6)
) -> DialogueResponse:
    """
    對應對外 API POST /api/v1/spirits/{placeId}/dialogue。
    對外端點必須先驗證由 /summon 核發、綁定 player_id 與 spirit_id 且有效 15 分鐘的相遇憑證；
    憑證驗證通過後才呼叫本函式，不保存原始 GPS 座標。
    內部會依序跑：B4 安全邊界檢查 → B2 Prompt 組裝 → B1 Gemini 呼叫 → B10 TTS+viseme。
    """
    ...


def generate_unlock_story(player_id: str, spirit_id: str, stage: int) -> UnlockStory:
    """
    B11．共鳴值解鎖敘事生成。
    呼叫方：身體（判定達標、寫完自己的 resonance 表之後才呼叫，順序不可顛倒）。
    """
    ...


def generate_quest_wrapper(player_id: str, quest_id: str) -> QuestNarrative:
    """
    B2 延伸應用．任務完成敘事包裝。
    呼叫方：身體（判定完成、寫完自己的 quest_progress 表之後才呼叫）。
    """
    ...


# ============================================================
# 臉 → 消費腦袋輸出（不主動呼叫腦袋，被動接收 dialogue 回應內含的 TTSResult）
# ============================================================

def play_viseme_timeline(audio_url: str, viseme_timeline: list[VisemeFrame]) -> None:
    """
    F5．口型同步播放器。只播放，不分析音訊，資料完全來自 DialogueResponse.tts。
    """
    ...
```

**呼叫順序上的硬規則（沿用 WBS-API 決策3/4，寫進簽章註解避免未來實作時弄反）：**
身體必須「先寫自己的表、再呼叫腦袋生成敘事」，不能反過來——否則腦袋生成內容時傳入的 `stage`／`quest_id` 狀態會跟資料庫不一致。

---

## 3. WBS → 7 Sprint 對照（提案，非既有定案表）

> ⚠️ 如前所述，這份 Sprint 排程是我依 WBS-API 已寫明的依賴關係（`依賴` 欄）重新排出來的，三份原始文件裡都沒有現成的 7-Sprint 表。排序原則：
> 1. 沒有依賴的基礎工作包先做（Sprint 1）
> 2. 身體／腦袋依決策4可以平行開發，只用 `player_id` 關聯，所以刻意讓兩邊在同一個 Sprint 平行推進，不互卡
> 3. 「臉」模組因為在 dev 文件已定案為 2.5D Billboard（低複雜度），排在中段即可，不用搶跑
> 4. 對外整合 API、跨模組串接動作（S10、B11、S8 等）排在對應的腦袋/身體工作包都完成之後
> 5. Roadmap 五項技術壁壘（多 Agent、AI 導演等）**不排進這 7 個 Sprint**——三份文件都一致標示為 MVP 之後才做

| Sprint | 主題 | 腦袋 | 身體 | 臉 |
|---|---|---|---|---|
| **Sprint 1** | 地基：身分/定位/人格卡/美術起手 | B3 人格卡載入與版本管理、B7 短期記憶(Redis) | S1 GPS/Fused Location、S6 匿名玩家身分系統 | F1 角色美術造型設計 |
| **Sprint 2** | 核心串接起步 | B1 Vertex AI Gemini 串接、B6 長期記憶(pgvector schema+檢索) | S2 在場驗證與召喚半徑 | F2 精靈圖烘焙管線 |
| **Sprint 3** | 判定邏輯與安全層 | B4 角色安全邊界檢查層、B2 Prompt 組裝引擎 | S3 防作弊、S4 任務進度表+狀態機、S5 共鳴值表+判定邏輯 | F3 Rive/Lottie 動畫實作 |
| **Sprint 4** | 對話輸出鏈 | B5 史實邊界規則注入、B9 當日情境內容生成、B10 TTS+viseme 產出 | — | F4 相機疊圖渲染、F5 口型同步播放器 |
| **Sprint 5** | UI 與敘事生成收尾 | B11 共鳴值解鎖敘事生成 | S7 地圖/召喚點 UI、S8 任務列表/Profile UI、S9 引導提問元件、S10 當日情境排程/快取/API | — |
| **Sprint 6** | 對外 API 整合＋素材交付鏈 | （整合測試：dialogue、daily-event、resonance/check、quests/complete） | S11 訊號推播、對外 API 全部串通（summon／spirits／quests／resonance／memory-summary） | F6 素材版本管理、F7 App 端素材下載/快取 |
| **Sprint 7** | 收尾與上線準備 | B8 記憶摘要 nightly batch job | S12 紀念照片（本機地標辨識驗證與相遇收藏中繼資料）、S13 行銷/分享功能、端到端 QA | 素材最終校驗、跨模組整合回歸測試 |

**依賴檢查（確認沒有工作包被排到自己依賴項之前）：**
- B2（依賴 B1,B3,B6）在 Sprint3，B1/B3/B6 分別在 Sprint1/2 ✅
- S8（依賴 S4,S5）在 Sprint5，S4/S5 在 Sprint3 ✅
- S10（依賴 B9）在 Sprint5，B9 在 Sprint4 ✅
- F5（依賴 B10,F3）在 Sprint4 同批，B10 與 F5 同 Sprint——**這格要注意**：B10 要在 Sprint4 前段完成，F5 才能接手測試，實作時建議 Sprint4 內部再拆前後半週，不是嚴格問題但值得排 Sprint 內任務順序時註記
- F7（依賴 F6）在 Sprint6 同批，同樣建議 Sprint 內先後順序
- S12（依賴 F4）在 Sprint7，F4 在 Sprint4 ✅

---

## 4. 剩餘的「下一步」（WBS-API 原文第4節，還沒做完的部分）

1. ✅ S4/S5 與 B6 schema 欄位設計 → 已在第1節完成，且已用 `player_id` 單一外鍵關聯，兩邊互不相依
2. ✅ 依賴關係轉 Sprint 排程 → 已在第3節完成（附提案標註）
3. ⏳ **尚待你確認**：第2節三支生成類函式（`generate_unlock_story`／`generate_quest_wrapper`／`generate_daily_event_content`）的簽章要跟腦袋模組實際負責人對過——這份簽章是我依現有文件邏輯推出來的合理版本，欄位命名、`session_context` 內部結構這類細節建議下次跟負責人過一輪再鎖定。
