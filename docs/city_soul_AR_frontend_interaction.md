# 城市靈魂 AR 遊戲 — 前端互動流程與感應/召喚機制

> 銜接對象：《城市靈魂AR遊戲_專案重點整理》(mainpoint) ×《開發紀錄》(dev) ×《WBS 與 API 切分》(WBS-API) ×
> 《Schema/API/Sprint 整合文件》×《核心業務規則與API契約》
> 本文件是決策鏈的**第六層**：前四份文件定的是「後端能不能做」，這份文件補的是「玩家在畫面上實際怎麼互動」，
> 並反過來對前面幾份文件的 schema/API 做了必要的擴充。不重複已定案內容，只補這輪新增的部分。

---

## 0. 這輪新增的核心概念：兩段式距離互動

在既有設計裡，「在場」只有一種半徑（`summon_radius_m`，50m），對應到「能不能召喚+對話+做任務」全部綁在一起。
這輪討論新增了一個**更遠的感應層**，把「聊天」跟「正式召喚進AR」拆成兩段：

| 範圍 | 名稱 | 可以做什麼 |
|---|---|---|
| 150m 內 | **感應範圍**（新） | 跟靈魂聊天（混合式：簡單問候用固定語，深入對話才呼叫LLM） |
| 50m 內 | **召喚範圍**（既有 `summon_radius_m`） | 觸發正式召喚 → 跳轉相機疊圖畫面 → 任務、共鳴值 |

150m 之外：地圖標記維持預設樣式，聊天輸入不可用，UI 提示「你還不夠靠近」（不顯示精確距離，見第5節）。

---

## 1. 地圖底圖方案【已定案】

- 互動引擎：**Google Maps SDK**（手勢/縮放/定位點）
- 視覺圖磚：**OSM（OpenStreetMap，ODbL授權）資料 → PyraCanny 線稿萃取 → Fooocus 生成風格化圖磚 → 以 Tile Overlay 疊在 Google Maps SDK 上**
- 理由：OSM 授權明確允許衍生創作，只需標註來源，沒有「不得離線儲存/機器解讀」的限制；同時繼續用 Google Maps SDK 當底層互動引擎，不違反「疊自己的圖磚在Google底圖上」這件事本身的規範。

---

## 2. 感應憑證（Sense Token）— 與 Encounter Token 平行的新機制【已定案】

### 2.1 為什麼需要新 token

Dialogue API 原本要求 Session Token + Encounter Token，而 Encounter Token 只在 50m 內 `/summon` 通過後核發。
150m 感應範圍內玩家還沒有 Encounter Token，若沿用舊邏輯會直接 401——因此新增一張平行的憑證。

### 2.2 Claims 結構

```json
{
  "sub": "player_id (UUID)",
  "spirit_id": "taipei_planetarium",
  "purpose": "sense",
  "iat": 1735776000,
  "exp": 1735777800
}
```

- 演算法、金鑰管理沿用 Encounter Token 同一套（HS256 + Secret Manager），`purpose` 欄位區分兩種 token，不可互相冒用。
- **效期：30 分鐘**。設計精神：進入 150m 時驗證一次，之後持續聊天不再重驗距離（不強制玩家一直待在150m內），效期是唯一的風險兜底機制——過期後若仍在聊，前端需重新呼叫 `/sense` 換發新token。
- 不含GPS座標，與 Encounter Token 原則一致。

### 2.3 核發端點：`POST /api/v1/sense`

**驗證：** Session Token

**Request:**
```json
{
  "spirit_id": "string",
  "latitude": "number",
  "longitude": "number"
}
```

**處理流程：**
1. 驗證 session token → 拿到 `player_id`
2. 算距離，`distance <= spirit.sense_radius_m`（新欄位，預設150m）才成立，否則 `403`
3. 核發 sense_token（30分鐘）
4. **不觸發任何任務判定、不核發任務**——純粹是聊天資格

**Response 200:**
```json
{ "sense_token": "string (JWT)", "spirit_id": "string" }
```

**錯誤情境：**
- `401`：session token 無效
- `403`：不在感應範圍內
- `404`：`spirit_id` 不存在

### 2.4 Dialogue API 驗證邏輯更新

原本「Session Token + Encounter Token 兩張都要」，改為：

```
Session Token（必要）
  + (Encounter Token 或 Sense Token，至少一張)
```

兩張 token 解鎖能力不同：

| 能力 | Sense Token（150m） | Encounter Token（50m） |
|---|---|---|
| 對話（含LLM） | ✅ | ✅ |
| `quests/complete` | ❌ | ✅ |
| 觸發相機疊圖畫面 | ❌ | ✅ |

### 2.5 對話延續【已定案，設計上天然成立】

短期記憶（B7 Redis）key 是 `session:{player_id}:{spirit_id}`，跟拿的是哪張 token 無關。
玩家從150m聊天走進50m觸發正式召喚，只要 `player_id`／`spirit_id` 不變，Redis 對話記錄自動延續，**不需額外設計**。

---

## 3. 混合式聊天：固定招呼比對層【已定案，新增工作包 B12】

- 玩家輸入先比對「固定招呼表」（簡單關鍵字/短語比對，不呼叫Gemini）→ 命中回預寫語，零成本
- 沒命中才進 B4（安全檢查）→ B2（Prompt組裝）→ B1（Gemini呼叫）正常流程
- 固定招呼表放進**人格卡 schema 新增欄位**，維持「人工審核」的內容治理原則：

```json
"canned_greetings": [
  { "trigger_phrases": ["你好", "哈囉", "嗨"], "response_text": "……（人工預寫，經審核）" }
]
```

- 新增工作包 **B12：快速問候比對層**，排入腦袋模組（建議與 B4 同期，Sprint 3）

---

## 4. 配額規則【已定案】

150m 聊天（不論固定語或LLM）與 50m 正式召喚後的對話，**共用同一組**每日配額（`dialogue_calls_daily` / `daily_tokens`），不分開算。

> ⚠️ **提醒**：因為共用配額，玩家在150m閒聊過量，理論上可能吃掉當天50m正式召喚後的對話額度。
> 目前封測期 `dialogue_calls_daily=50` 是保守初始值，`usage_tier_limits` 本身是可調資料（不用重新部署），
> 若封測觀察到「150m聊到沒額度、走進50m反而不能聊」的體驗落差，屆時直接調高數字即可，這輪不預先處理。

---

## 5. 地圖標記三段式視覺【已定案】

| 距離 | 標記狀態 |
|---|---|
| 150m 外 | 預設樣式（原mockup的圓點+旗子樣式） |
| 150m 內 | 淡淡發光/圖示提示，代表「可以聊天了」 |
| 50m 內 | 完全點亮，變成可點擊的「召喚」按鈕 |

**50m 觸發流程【已定案】**：進入50m時**不是彈窗**，而是畫面上的召喚按鈕本身亮起／變為可點擊狀態，玩家主動點擊才觸發 `/summon` 並跳轉相機疊圖畫面（不是自動跳轉）。

**「你還不夠靠近」提示【已定案，修正版】**：
- **不顯示精確距離數字**（原本考慮顯示如「還差23m」，因持續輪詢GPS算距離耗電，此開發階段取消）
- 改用**模糊提示**（例如「再靠近一點」），搭配第6節的 Geofencing 技術方案，不需要持續算距離就能觸發

---

## 6. 距離偵測技術方案【已定案，取代原本「持續輪詢」的隱含假設】

為了同時滿足「不顯示精確距離」與「省電」，兩種畫面用不同技術：

| 畫面 | 技術方案 | 原因 |
|---|---|---|
| 地圖畫面（150m/50m 門檻偵測） | 作業系統原生 **Geofencing API**（Android Geofencing API / iOS CLCircularRegion） | 只回報「進入/離開範圍」事件，不需要持續前景輪詢算距離，最省電；且天然不產出精確距離數字，跟「不顯示距離」的決定一致 |
| 相機疊圖畫面（50m內漸顯尋找靈魂） | 前景**持續讀GPS**做距離插值 | 玩家已主動開啟相機（本身就是高耗電前景情境），跟 CONTEXT.md「前景即時情境反應」原則一致，不受本次省電修正影響 |

> ⚠️ **待驗證的技術風險**：雙 Geofence（150m + 50m 同一個地標兩個同心圓）在部分 Android/iOS 版本上是否能穩定觸發兩層獨立事件，
> 建議 Sprint 2（在場驗證相關工作包）階段先做技術驗證（POC），若平台限制導致雙geofence不穩定，備案是退回到「較低頻率的前景輪詢（例如每10-15秒一次）」，但仍不顯示精確數字，只用於門檻判斷。

---

## 7. 相機疊圖畫面【已定案】

- **對話窗預設展開**（對話是主要互動，不隱藏）
- **可收合**，玩家可以自行收起來專心找靈魂位置
- 靈魂依 GPS 距離**深淡/縮放漸變**，越接近召喚點（50m圓心）越清晰完整，類似 Pokémon GO 機制
- 技術取捨延續既有原則：無遮擋判斷、無真實透視景深、靠GPS距離模擬縮放（非相機實算3D），符合「2.5D Billboard、不做真AR空間錨定」的既有決策

---

## 8. 開發期基礎設施決策【已定案】

- **開發階段**：後端資料庫（Postgres + Redis）先用**本地資源**（例如 docker-compose 起本地服務），不急著一開始就佈建 Cloud SQL / Memorystore
- **之後**：核心流程穩定、要進 staging/正式環境時，再把連線目標換成 GCP 受管服務
- **理由**：減少「改一次 schema 就要重新佈署雲端」的摩擦，加快 MVP 開發期的迭代速度
- **影響範圍**：不改變任何 schema 設計，純粹是連線字串/部署目標的切換，Sprint 1 起手可以先用本地環境開發

---

## 9. Schema 異動彙總（更新既有《Schema/API/Sprint整合文件》）

```sql
-- spirits 表新增欄位
ALTER TABLE spirits ADD COLUMN sense_radius_m INTEGER NOT NULL DEFAULT 150;
```

`brain.persona_cards.content` JSON schema 新增欄位（見第8節《業務規則與API契約》原schema基礎上擴充）：

```json
{
  "canned_greetings": [
    { "trigger_phrases": ["string", "..."], "response_text": "string" }
  ]
}
```

---

## 10. API 契約異動彙總（更新既有《核心業務規則與API契約》）

新增：

| Method | Path | 驗證 | 說明 |
|---|---|---|---|
| POST | `/api/v1/sense` | Session Token | 150m感應範圍驗證，核發30分鐘 Sense Token，不觸發任務 |

修改：

| Method | Path | 異動內容 |
|---|---|---|
| POST | `/api/v1/spirits/{placeId}/dialogue` | 驗證邏輯改為 `Session Token +（Encounter Token 或 Sense Token）`；持有 Sense Token 者不可呼叫 `quests/complete`、不可觸發相機疊圖畫面 |

---

## 11. 尚待確認/驗證的缺口

1. 雙 Geofence 穩定性 POC（見第6節提醒）——建議排入 Sprint 2
2. `canned_greetings` 的實際觸發語內容——需要敘事負責人撰寫、人工審核，目前只有結構沒有內容
3. 地圖標記「150m淡淡發光」的具體美術呈現——屬於 F 模組（臉）的美術細節，尚未設計
4. ~~Sense Token 30分鐘過期後的前端體感~~ — **已定案**：前端**靜默重新換發**新的 Sense Token，玩家完全無感，不跳提示、不中斷對話流程。實作上代表 App 端要在背景自行追蹤 sense_token 的 `exp`，快到期前（例如剩1-2分鐘）主動重打 `/sense`（沿用當下最後一次已知在150m內的座標），不需要等 dialogue API 回 401 才被動處理。
