# 城市靈魂 AR 遊戲 — 核心業務規則與 API 完整契約

> 補完對象：上一輪盤點出的 SDD 缺口——業務規則精確定義、API 完整契約、相遇憑證技術實作、
> 人格卡正式 schema、Prompt 組裝引擎設計。
> 本文件延續前三份文件（mainpoint／dev／WBS-API）與《Schema/API/Sprint 整合文件》的決策，
> 不重複已定案的內容，只補「精確到可以直接刻程式碼」的細節。
>
> 標示 **【已定案】** 的是你在這輪對答中拍板的規則；標示 **【提案待確認】** 的是我依現有原則
> 推出的技術設計，需要你過一輪才能真的鎖定；標示 **⚠️矛盾/需重新設計** 的是這輪決策跟舊文件
> 對不上、我已經改掉的地方。

---

## 0. 這輪確定的業務規則總表

| # | 規則 | 決策 |
|---|---|---|
| 1 | GPS 容許誤差 | 【已定案】先用固定半徑（`spirits.summon_radius_m`）直接判定，不額外加寬容錯；等有實測誤差數據再調 |
| 2 | 召喚冷卻 | 【已定案】召喚本身**無冷卻**，可重複觸發；但「可驗證微任務」的發放頻率是**每個玩家對每個靈魂、每日一次**；對話互動不受限，隨時可以再找靈魂聊天 |
| 3 | 防作弊（mock location） | 【已定案】**觀察期**：判定為 mock location 時只記錄（log），不擋下這次召喚；之後看數據再決定要不要真的擋 |
| 4 | 相遇憑證技術實作 | 【已定案】**JWT 自簽**，不查資料庫，效能優先，接受「無法中途撤銷」這個取捨 |
| 5 | 任務逾時 | 【已定案】相遇憑證（15分鐘）過期 = 這次任務嘗試**失敗** |
| 6 | 失敗重試次數 | 【已定案】**當天最多失敗重試 3 次**，超過要等隔天（任務每日重置）才能再挑戰 |
| 7 | 共鳴值入帳時機 | 【已定案】任務完成當下**即時入帳並檢查門檻**，`resonance` 的更新是 `quest/complete` 內部動作，不是前端另外呼叫的獨立步驟 |

> **時區補充【提案待確認】**：「每日一次」「隔天重置」的「一天」用什麼時區算，這輪沒問到，我先假設是 **Asia/Taipei（UTC+8）午夜重置**，理由是 CONTEXT.md「MVP 語言」明訂 TA 是台北 18–35 歲族群，用台北的「一天」對玩家最直覺。如果你們未來要做觀光客／海外玩家，這個假設要重新拿出來討論，但 MVP 階段這樣定沒問題。

---

## 1. 在場驗證與召喚（S2 的精確規則）

**判定邏輯：**
```
distance = haversine(玩家GPS, spirit.latitude, spirit.longitude)
在場成立 if distance <= spirit.summon_radius_m
```
不做動態誤差放寬（決策1）。`summon_radius_m` 這個欄位的值本身就是產品/現場勘查時，把 GPS 常見誤差一併考慮進去定出來的「有效半徑」，不是「地標的實際佔地範圍」——這件事在 seed data 或後台管理介面要註記清楚，避免未來有人誤解成「這是精確的地理邊界」。

**mock location 偵測（S3）：**
偵測到就寫一筆 log（不需要新表，用應用程式日誌 + Cloud Logging 就夠，MVP 階段不用為了「觀察期」這件事新增資料表），但**不阻擋**這次 `/summon` 請求。log 內容至少要有 `player_id`、`spirit_id`、偵測時間、偵測依據（例如 mock location flag、速度異常數值），方便之後回頭分析要不要轉真的擋。

---

## 2. 相遇憑證（Encounter Token）— JWT 技術規格【提案待確認】

### 2.1 為什麼是這個設計
決策4選了 JWT 自簽、不查資料庫。這代表**沒有辦法主動撤銷**一張已核發的憑證——如果你們之後發現需要「玩家帳號被封禁時立刻讓所有憑證失效」這種需求，屆時要嘛接受「最多等 15 分鐘憑證自然過期」，要嘛把設計換成 opaque token + Redis。MVP 階段先接受這個取捨。

### 2.2 Claims 結構

```json
{
  "sub": "player_id (UUID)",
  "spirit_id": "taipei_planetarium",
  "purpose": "encounter",
  "iat": 1735776000,
  "exp": 1735776900
}
```

- `exp - iat` 固定 900 秒（15 分鐘），對應 CONTEXT.md「相遇憑證...有效 15 分鐘的短效授權」。
- 沒有 `jti`：既然不查資料庫、不做撤銷名單，`jti` 加了也沒地方用，先不加，避免虛假的安全感。
- **刻意不含任何 GPS 座標**——對應 CONTEXT.md「它不包含或保存原始 GPS 座標」。

### 2.3 簽章金鑰管理

- 演算法：`HS256`（對稱金鑰）。理由：簽發方跟驗證方是同一個後端服務，不需要非對稱金鑰的「公鑰可以公開給第三方驗證」這個特性，HS256 實作簡單、效能好。
- 金鑰存放：GCP Secret Manager，Cloud Run 用環境變數方式掛載，**不要**寫死在程式碼或 `.env` 裡進版控。
- 本機開發：`.env` 裡放一組開發用的假金鑰即可，跟正式環境的金鑰分開管理。

### 2.4 使用流程

```
POST /api/v1/summon (GPS)
  → 身體 S2 在場驗證通過
  → 身體核發 encounter_token（JWT，15分鐘效期）
  → 回傳給前端

前端後續呼叫：
  POST /api/v1/spirits/{placeId}/dialogue
  Header: Authorization: Bearer {encounter_token}
  → 後端驗證 JWT 簽章 + exp 沒過期 + spirit_id 跟路徑參數一致
  → 通過才處理對話
```

---

## 3. 可驗證微任務生命週期【提案待確認，含決策6/7的落地設計】

這一節是這輪對答資訊量最大的部分，直接影響 `quest_progress` 的狀態機設計。

### 3.1 狀態機

```
不存在 → in_progress → completed
              ↓ (憑證過期且任務未完成)
           失敗嘗試 (attempts_today += 1)
              ↓ (attempts_today < 3)
           回到 in_progress，可重新召喚再挑戰
              ↓ (attempts_today >= 3)
           當天鎖定，要等隔天（Asia/Taipei 午夜）reset
```

### 3.2 「失敗」怎麼被系統知道——技術設計提案

問題：憑證過期是前端的 15 分鐘倒數，後端要怎麼確定「這次真的失敗了」，而不是依賴前端老實回報？

**提案：不新增「回報失敗」API，改成在下一次 `/summon` 時由後端被動判定。**

```
玩家呼叫 POST /api/v1/summon (同一個 spirit_id)
  → 後端查 quest_progress，是否有一筆 status = 'in_progress'
    且對應的 encounter_token 已經過期超過15分鐘？
    → 是：判定為一次失敗嘗試，attempts_today += 1
       → 若 attempts_today >= 3：拒絕本次召喚的任務發放（在場驗證仍然通過，
         玩家可以召喚聊天，但這支 API 回應裡標記「今日任務嘗試已用完」）
       → 若 attempts_today < 3：正常核發新的 encounter_token，任務可以重來
```

這個設計比「前端主動回報失敗」更符合 CONTEXT.md「以後端確定性規則驗證完成與否」的精神——不依賴前端誠實。

**需要在 `quest_progress` 表補的欄位（更新第1節整合文件的 schema）：**

```sql
ALTER TABLE quest_progress ADD COLUMN attempts_today INTEGER NOT NULL DEFAULT 0;
ALTER TABLE quest_progress ADD COLUMN attempts_date DATE NOT NULL DEFAULT CURRENT_DATE;
ALTER TABLE quest_progress ADD COLUMN current_token_issued_at TIMESTAMPTZ NULL;
```

- `attempts_date` 用來判斷「今天」是不是新的一天（Asia/Taipei 定義），跨天時 `attempts_today` 重置為 0，不需要排程 job 特地去清，查詢當下比對日期即可。
- `current_token_issued_at` 記錄最近一次核發憑證的時間，用來算「有沒有過期超過15分鐘」，這裡存的是時間戳，不是原始 GPS，不違反「相遇憑證不保存座標」的原則。

### 3.3 共鳴值入帳連動【已定案的落地設計】

```
POST /api/v1/quests/{questId}/complete
  → 身體：驗證任務規則型完成條件（後端確定性規則，不是LLM判定）
  → 身體：寫入 quest_progress.status = 'completed'
  → 身體：resonance.resonance_value += 20（MVP固定值，對應CONTEXT.md）
  → 身體：檢查 resonance_value 是否跨過 10/40/100 門檻
    → 若跨過：呼叫腦袋 narrative.generate_unlock_story(player_id, spirit_id, stage)
  → 身體：呼叫腦袋 narrative.generate_quest_wrapper(player_id, quest_id) 生成任務完成敘事
  → 回傳給前端：{ quest_wrapper_text, unlock_story (可能為null) }
```

---

## 4. ⚠️ 矛盾修正：`/resonance/{spiritId}/check` 這支 API 的定位

**舊版 WBS-API 寫的是：** 身體判定共鳴值達標 → 呼叫腦袋生成解鎖故事 → 回傳。這預設共鳴值檢查是一個**獨立於任務完成**的動作。

**這輪的決策（共鳴值入帳跟任務完成連動）讓這個獨立端點的必要性消失了**——因為第3.3節已經把「入帳+檢查門檻+生成故事」整個包進 `quest/complete` 裡面了。

**修正提案：**
把 `POST /api/v1/resonance/{spiritId}/check` 改成 **`GET /api/v1/resonance/{spiritId}`**，語意從「執行判定動作」改成「查詢目前狀態」——玩家在 Profile 頁面想看某個靈魂的共鳴值進度、下一個門檻還差多少，用這支查詢就好，不會意外觸發任何寫入或 LLM 呼叫。

這個修正同時解決一個潛在問題：舊版設計裡 `POST .../check` 這個名字本身就有點誤導，讀起來像是「檢查」這個動作本身會導致狀態改變，容易讓未來接手的工程師誤用（例如前端每次進 Profile 頁就呼叫一次，結果不小心觸發了不該觸發的副作用）。改成 `GET` 之後，語意上就是唯讀，不會有這個風險。

---

## 5. ⚠️ 發現的新缺口：玩家層級 API 的身分驗證機制

盤點 API 契約時發現：相遇憑證（JWT，15分鐘）只解決「這次召喚有沒有在場驗證通過」，但像 `GET /quests/daily`、`GET /players/me/memory-summary` 這類**不需要正在召喚中也能查的玩家層級 API**，要怎麼確認「這是哪個玩家在問」，三份原始文件完全沒提到。這是必須補的東西，不然這些 API 根本無法實作。

**提案：在 `POST /api/v1/players` 核發時，一併發一張長效的 session token。**

```json
{
  "sub": "player_id (UUID)",
  "purpose": "session",
  "iat": 1735776000,
  "exp": 1743552000   // 90天後
}
```

- 演算法、金鑰管理跟相遇憑證一致（HS256 + Secret Manager），可以共用同一把簽章金鑰，或分開一把——分開較安全（相遇憑證外洩的影響範圍不會波及 session），**建議分開**，多一個環境變數的成本很低。
- App 端把這個 token 存在裝置安全儲存區（跟 `device_id` 放一起），每次 API 呼叫帶在 `Authorization: Bearer {session_token}`。
- 90 天效期只是先抓一個數字，App 可以在每次啟動時偷偷用舊 token 換一張新的（有效期還沒過就直接發新的，順便延長），玩家不會感覺到「登入過期」這種體驗，畢竟是免註冊產品，不應該讓玩家感覺到任何登入機制存在。
- **這跟相遇憑證是兩張不同的 token，用途不同、有效期不同、可能簽章金鑰也不同，程式碼裡要用不同的驗證中介層區分，不要共用同一套驗證邏輯**，避免有人不小心拿 15 分鐘的相遇憑證當 session 用（會導致玩家每 15 分鐘就要重新「登入」，體驗很差），或反過來拿 90 天的 session token 當相遇憑證用（會讓在場驗證形同虛設，玩家可以拿舊 token 在家裡假裝在召喚點）。

**兩張 token 的權責邊界：**

| | Session Token | Encounter Token |
|---|---|---|
| 用途 | 證明「我是哪個玩家」 | 證明「我剛剛在場驗證通過，可以跟這個靈魂互動」 |
| 效期 | 90天（可續） | 15分鐘 |
| 何時核發 | `POST /players` | `POST /summon` 在場驗證通過後 |
| 保護的 API | quests/daily、resonance查詢、memory-summary 等玩家層級查詢 | dialogue、quests/complete 等需要「正在跟靈魂互動」語境的操作 |
| 沒帶會怎樣 | 401，前端應該重新呼叫 `POST /players`（同一個 device_id 會拿回同一個 player） | 401，前端應該引導玩家重新走一次召喚流程 |

---

## 6. 完整 API 契約

> 沒特別寫「驗證」的表示不需要任何 token（例如首次建立玩家身分）。

### 6.1 `POST /api/v1/players`

**驗證：** 無（這是建立身分的起點）

**Request:**
```json
{ "device_id": "string, 裝置安全儲存區產生的隨機值" }
```

**Response 200:**
```json
{
  "player_id": "uuid",
  "device_id": "string",
  "account_id": "string | null",
  "created_at": "datetime",
  "session_token": "string (JWT，90天效期，見第5節)"
}
```

**錯誤情境：**
- `422`：`device_id` 為空或格式不合法
- 同一個 `device_id` 重複呼叫 → 不是錯誤，回傳既有 player + **重新核發**一張新的 session token（順便延長效期，見第5節）

---

### 6.2 `GET /api/v1/spirits/{placeId}`

**驗證：** 無（地標基本資料本身不是敏感資訊，公開查詢即可，簡化 App 首次載入流程）

**Response 200:** 見整合文件 `SpiritResponse`

**錯誤情境：**
- `404`：`placeId` 不存在或 `is_active = false`

---

### 6.3 `POST /api/v1/summon`

**驗證：** Session Token

**Request:**
```json
{
  "spirit_id": "string",
  "latitude": "number",
  "longitude": "number",
  "gps_accuracy_m": "number | null",   // 先收著記錄，MVP不用來動態放寬判定（決策1）
  "is_mock_location": "boolean"        // 裝置端偵測結果，由 App 端 SDK 判斷後回傳
}
```

**處理流程（第1、3、3.2節邏輯的落地）：**
1. 驗證 session token → 拿到 `player_id`
2. 若 `is_mock_location = true`：記錄 log，**不阻擋**（決策3的觀察期）
3. 算距離，`distance <= spirit.summon_radius_m` 才算在場成立，否則 `403`
4. 查 `quest_progress`：是否有過期未完成的任務（第3.2節被動判定邏輯），是的話 `attempts_today += 1`
5. 核發 encounter_token（15分鐘）
6. 判斷今天是否已經發過任務（`quest_progress.attempts_date` 是今天 且 已有記錄）：
   - 沒發過 → 一併回傳新任務內容
   - 已發過且 `attempts_today >= 3` → 回傳「今日任務嘗試已用完」，但 encounter_token 仍然給，玩家還是能對話（決策2：對話不受限）
   - 已發過且 `attempts_today < 3` → 回傳同一個任務，允許重新挑戰

**Response 200:**
```json
{
  "encounter_token": "string (JWT)",
  "spirit_id": "string",
  "quest": {
    "quest_id": "string",
    "status": "in_progress | completed | daily_limit_reached | already_completed_today",
    "attempts_today": "integer"
  } | null   // 若今天沒有可用任務（例如已完成過），為 null
}
```

**錯誤情境：**
- `401`：session token 無效/過期
- `403`：不在召喚範圍內（`distance > summon_radius_m`）
- `404`：`spirit_id` 不存在

---

### 6.4 `POST /api/v1/spirits/{placeId}/dialogue`

**驗證：** Session Token + Encounter Token（兩張都要）

**Request:**
```json
{ "user_input": "string, 玩家的自由文字輸入" }
```

**處理流程：**
1. 驗證兩張 token；`encounter_token.spirit_id` 必須跟路徑參數 `placeId` 一致，否則 `403`
2. B4 角色安全邊界檢查（見 CONTEXT.md「角色安全邊界」定義）
3. B2 Prompt 組裝（見第8節）→ B1 Gemini 呼叫 → B10 TTS+viseme
4. B7 短期記憶寫入這輪對話

**Response 200:** 對應整合文件的 `DialogueResponse`

**錯誤情境：**
- `401`：任一張 token 無效
- `403`：`encounter_token` 的 `spirit_id` 跟路徑不符，或已過期
- `422`：`user_input` 為空
- `503`：Gemini API 呼叫失敗/逾時 → 這時要有 fallback：CONTEXT.md「無合格輸入或生成失敗時使用人工預寫台詞」，所以 503 情境下**不是**回錯誤給前端，是回一句人工預寫的 fallback 台詞，HTTP 狀態仍是 `200`（對前端來說是正常回應，只是內容是預寫台詞，不是即時生成的）

---

### 6.5 `GET /api/v1/spirits/{placeId}/daily-event`

**驗證：** 無（當日情境是公開的世界狀態，所有玩家看到一樣的內容）

**Response 200:** 對應整合文件的 `DailyEventContent`

**錯誤情境：**
- `404`：今天還沒有快取（排程 job 還沒跑，或跑失敗）→ 這裡也要有 fallback，不應該讓前端看到 404 空畫面；建議 `S10` 排程失敗時寫入前一天的內容當保底，寧可內容不是最新的，也不要讓玩家看到空的當日情境

---

### 6.6 `GET /api/v1/quests/daily`

**驗證：** Session Token

**Response 200:**
```json
{
  "quests": [
    {
      "quest_id": "string",
      "spirit_id": "string",
      "status": "not_started | in_progress | completed | daily_limit_reached",
      "attempts_today": "integer"
    }
  ]
}
```
> 這支是「列表」，`POST /summon` 回的是「單一 spirit 的任務狀態」，兩者資料來源一致（都是查 `quest_progress`），差別只是範圍。

---

### 6.7 `POST /api/v1/quests/{questId}/complete`

**驗證：** Session Token + Encounter Token

**Request:**
```json
{ "completion_evidence": "object，依任務類型而定，例如實景疊圖任務會帶本機辨識結果" }
```

**處理流程：** 見第3.3節

**Response 200:**
```json
{
  "quest_wrapper_text": "string",
  "resonance_value": "integer, 入帳後的最新值",
  "unlock_story": {
    "stage": "integer",
    "story_text": "string"
  } | null
}
```

**錯誤情境：**
- `401`：token 無效
- `409`：這個任務今天已經完成過，不能重複 complete
- `422`：`completion_evidence` 不符合這個任務類型要求的格式，或後端確定性規則判定未達成完成條件（CONTEXT.md：「以後端確定性規則驗證完成與否」，不是 LLM 判定，所以這裡的驗證邏輯在身體模組，不會呼叫 Gemini）

---

### 6.8 `GET /api/v1/resonance/{spiritId}`

**驗證：** Session Token

**（原本是 `POST .../check`，第4節已說明修正原因，改為唯讀查詢）**

**Response 200:**
```json
{
  "spirit_id": "string",
  "resonance_value": "integer",
  "stage": "integer",
  "next_threshold": "integer | null"   // 10/40/100，全部解鎖完是 null
}
```

---

### 6.9 `GET /api/v1/players/me/memory-summary`

**驗證：** Session Token

**Response 200:**
```json
{
  "quests": [ /* 同 6.6 結構 */ ],
  "resonance": [
    { "spirit_id": "string", "resonance_value": "integer", "stage": "integer" }
  ]
}
```
純粹是身體自己的表直查（整合文件決策4），不呼叫腦袋，延遲最低。

---

### 6.10 `GET /api/v1/assets/{avatarId}`

**驗證：** 無（素材是公開的 CDN 資源，不含任何玩家個資）

**Response 200:**
```json
{ "asset_url": "string, CDN URL", "version": "string" }
```

---

## 7. 更新後的內部介面函式簽章（補充）

對應整合文件第2節，這輪新增/修改的部分：

```python
def issue_session_token(player_id: str) -> str:
    """S6．核發90天 session token，player 建立或重複呼叫時都會發新的一張。"""
    ...

def issue_encounter_token(player_id: str, spirit_id: str) -> str:
    """S2．在場驗證通過後核發，15分鐘效期，見第2節 claims 結構。"""
    ...

def verify_session_token(token: str) -> str:
    """回傳 player_id，無效/過期時 raise TokenInvalidError。"""
    ...

def verify_encounter_token(token: str, expected_spirit_id: str) -> str:
    """回傳 player_id，spirit_id 不符或過期時 raise TokenInvalidError。"""
    ...


def check_and_apply_quest_failure(db: Session, player_id: str, spirit_id: str) -> None:
    """
    S2 呼叫，對應第3.2節被動判定邏輯。
    檢查是否有過期未完成的 quest_progress，若有則 attempts_today += 1。
    不回傳值，純粹是狀態更新的 side effect，呼叫方（/summon）呼叫完後
    再重新查一次 quest_progress 拿最新狀態組回應。
    """
    ...
```

---

## 8. 人格卡正式 Schema【提案待確認】

`brain.persona_cards.content`（JSONB 欄位）目前只有 Sprint1 seed script 裡的示意草稿，這裡把它定成正式結構，內容仍是佔位文字，實際文字要由敘事負責人撰寫、經人工審核：

```json
{
  "schema_version": 1,
  "core_personality": "string，核心性格一句話定調",
  "speaking_style": "string，說話風格、語氣、口頭禪等",
  "emotional_core": "string，情感核心/角色動機",
  "factual_boundary": {
    "known_facts": "string，明確已知史實範圍",
    "folklore": "string，民間傳說範圍，允許提及但要標明是傳說",
    "imagination": "string，角色可以自由發揮想像的範圍，但不能表述為事實"
  },
  "taboo_topics": ["string", "..."],
  "quest_themes": ["string", "..."],
  "not_this_character": "string，明確排除的角色詮釋方向（例如天文館人格卡的『不是館員、不是特定科學家』）"
}
```

這個結構直接對應 CONTEXT.md「人格卡」定義列的五個要素（核心性格、說話風格、史實邊界、禁忌、任務主題），`schema_version` 是為了未來欄位要改版時，載入邏輯（B3 `load_active_persona_card`）可以判斷版本相容性，不會因為改欄位就讓舊資料炸掉。

---

## 9. Prompt 組裝引擎（B2）設計提案【提案待確認，這塊建議你們找腦袋模組負責人一起過】

這是目前唯一一塊我不打算幫你「拍板」的——不是流程不能設計，是**組裝順序、記憶要抓幾筆、token預算怎麼抓**這些數字，會直接影響 Gemini 呼叫的成本跟品質，dev.md 明確標了這是「🔴高風險：AI對話成本與延遲」，不應該由我單方面決定,只給一個業界常見的骨架，數字都要你們自己測過調：

```
System Instruction 組成順序：
  1. 人格卡（B3）：core_personality + speaking_style + factual_boundary + taboo_topics
  2. 史實邊界規則（B5）：注入額外的「不對敏感歷史武斷定論」規則文字
  3. 角色安全邊界（B4）：安全檢查是「輸入端」先過濾，不是塞進 system instruction，這裡不重複放

User Turn 組成順序：
  1. 當日情境摘要（B9 產出的 narrative_text，如果今天有）
  2. 長期記憶（B6）：語意檢索 Top-K（建議先抓 K=3，避免一次塞太多筆推高 token 用量，
     之後看對話品質再調）
  3. 短期記憶（B7）：這個 session 內最近幾輪對話（建議上限抓最近 6 輪，超過的直接捨棄，
     不是每輪都無限累加）
  4. 玩家這次的 user_input
```

**沒有定案的東西，需要你們自己決定：**
- Top-K 抓幾筆長期記憶、短期記憶抓幾輪，這兩個數字直接決定每次呼叫的 token 用量，要拿實際的人格卡文字長度去估算成本
- 要不要對「當日情境摘要」做長度上限（避免情境文字本身就很長，擠壓掉記憶的空間）
- Gemini Flash / Pro 的分流規則（dev.md 提到「Gemini Flash／Pro 分層」，但沒寫「什麼情況用哪個」——目前唯一明確的線索是 WBS-API 說 `B11 共鳴值解鎖敘事生成`是「關鍵時刻」，這暗示解鎖故事可能該用 Pro，日常對話用 Flash，但這也需要你們定案）

**這幾個開放問題，透過下一節的分級配額機制解決一半**——不是把答案定出來，是把「答案存在資料庫、可以隨時調整」這件事做出來，數字本身還是要等封測數據。

---

## 9.5 使用量分級（Usage Tier）機制【提案待確認】

### 9.5.1 設計方向

參考你提供的方案表，但**不是照抄欄位**——那張圖是通用 SaaS API 方案（聊天次數、AI點數、圖片辨識、OCR、單次 Prompt 長度、單日 Token），有些項目跟這個專案的實際功能對不上，硬套反而會留一堆用不到的欄位。做法是：挑出真的適用的資源類型，並且**把 schema 設計成可以之後隨時加新的資源類型，不用改資料庫結構**。

**對應關係：**

| 範例圖項目 | 這個專案的對應 | 適用嗎 |
|---|---|---|
| 每日聊天 | 每日 `dialogue` API 呼叫次數 | ✅ 適用，直接對應 B1 Gemini 呼叫次數，是主要成本來源 |
| 單日 Token | 每日 Gemini token 用量上限 | ✅ 適用，dev.md 標的「🔴高風險：AI對話成本」直接對應這個 |
| 單次 Prompt 長度 | 玩家 `user_input` 字數上限 | ✅ 適用，防止有人塞超長文字進 Prompt 推高單次成本 |
| 每分鐘 API | 每分鐘 API 呼叫上限 | ✅ 適用，防止腳本濫用/灌爆，跟 AI 品質無關但仍是成本/穩定性風險 |
| 每日圖片辨識 | — | ❌ 不適用：CONTEXT.md「本機地標辨識」是**裝置端**判斷，不打後端 API，沒有後端資源消耗這件事 |
| 每日 OCR | — | ❌ 不適用：這個遊戲目前沒有 OCR 相關功能 |
| 每日 AI 點數 | — | ⏸️ 先不用：目前沒有「點數消耗」的遊戲機制設計，`dialogue` 次數已經是最直接的用量指標，不需要疊加一層點數制讓事情變複雜 |
| 試用 28 天 | — | ⏸️ 先不用：這是訂閱制產品的概念，這個遊戲目前沒有付費分級，如果未來要做「封測玩家 vs 正式玩家」的差異化，屬性可以再加，不影響現在的 schema |

### 9.5.2 Schema 設計

**關鍵決定：用「一個 tier 對多筆 limit」的正規化設計，不是每個資源類型開一個欄位。**
理由：欄位式設計（像範例圖那樣）以後每加一種新資源就要 `ALTER TABLE` 加欄位；正規化成兩張表之後，加新資源類型只是**多寫一筆資料**，不用動 schema，也不用重新部署。

```sql
-- 分級定義（例如：封測期、正式公開期，未來也可以是免費/付費）
CREATE TABLE usage_tiers (
    tier_id      VARCHAR(32) PRIMARY KEY,   -- 'closed_beta', 'public_default', ...
    display_name VARCHAR(64) NOT NULL,
    is_default   BOOLEAN NOT NULL DEFAULT FALSE,  -- 新玩家預設分配到哪個tier，只能有一筆是TRUE
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每個分級底下，各種資源類型的配額
CREATE TABLE usage_tier_limits (
    tier_id       VARCHAR(32) NOT NULL REFERENCES usage_tiers(tier_id),
    resource_type VARCHAR(32) NOT NULL,   -- 見下方列舉
    limit_value   INTEGER NOT NULL,
    PRIMARY KEY (tier_id, resource_type)
);

-- players 表加一個外鍵（更新第1節整合文件的schema）
ALTER TABLE players ADD COLUMN usage_tier_id VARCHAR(32) NOT NULL REFERENCES usage_tiers(tier_id);
```

**`resource_type` 目前列舉的四種（對應9.5.1的✅項目）：**

| resource_type | 意義 | 計算週期 |
|---|---|---|
| `dialogue_calls_daily` | 每日 `/dialogue` 呼叫次數上限 | 每日（Asia/Taipei 午夜重置） |
| `daily_tokens` | 每日 Gemini token 用量上限（input+output加總） | 每日 |
| `prompt_max_chars` | 單次 `user_input` 字數上限 | 每次請求（不是累計值，是單筆上限） |
| `api_rate_per_minute` | 每分鐘 API 呼叫次數上限 | 每分鐘滑動窗口 |

### 9.5.3 初始建議值（封測期）

跟你說的一樣，這幾個數字**先抓一組保守的起始值，開放測試後再調**，不是定案，寫在這裡只是讓 `closed_beta` 這個 tier 一開始不是空的：

| resource_type | 建議初始值 | 抓這個數字的理由 |
|---|---|---|
| `dialogue_calls_daily` | 50 | 一次遊玩體驗大概幾輪對話抓個寬鬆值，先觀察真實分佈 |
| `daily_tokens` | 150,000 | 比範例圖的 300,000 保守一半，因為這是「單一玩家」的用量，不是整個服務的用量 |
| `prompt_max_chars` | 500 | 遊戲對話輸入不會是長文，比範例圖的 3,000 字保守很多，先抓小一點 |
| `api_rate_per_minute` | 6 | 正常玩家打字說話不會這麼快，抓低一點主要是防腳本濫用，不是防正常玩家 |

這組數字**只是起點**，第0節時區假設一樣，等封測有實測數據，直接改 `usage_tier_limits` 的資料就好，不用重新部署。

### 9.5.4 執行層設計

配額檢查是高頻動作（每次 `/dialogue` 呼叫都要檢查），不適合每次都查 Postgres，用 Redis 做計數器：

```python
def check_and_consume_quota(
    player_id: str,
    resource_type: str,
    amount: int = 1,
) -> None:
    """
    在 B4 安全檢查之前呼叫（dialogue API 的第一道關卡）。
    超過配額時 raise QuotaExceededError，由 API 層轉成 429 回應。

    Redis key 命名：quota:{player_id}:{resource_type}:{YYYY-MM-DD, Asia/Taipei}
    每日型配額用 INCRBY + 設定 TTL 到當天Asia/Taipei午夜；
    每分鐘型配額（api_rate_per_minute）另外用滑動窗口，key 加上分鐘級時間戳、TTL=60秒。
    """
    ...
```

- Tier 的 limit 數值本身（`usage_tier_limits` 的內容）**不用每次都查 Postgres**，這種資料變動頻率低，應用啟動時載入記憶體快取即可，改了資料庫之後靠重啟或簡單的 TTL 快取失效機制更新，不需要為此做即時通知機制。
- `prompt_max_chars` 是唯一不用 Redis 計數器的一種——它是單次請求的靜態檢查（`len(user_input) <= limit`），不是累計值，判斷邏輯直接寫在 dialogue endpoint 裡查一次 tier limit 就好。

### 9.5.5 對 API 契約的影響

第6.4節 `POST /api/v1/spirits/{placeId}/dialogue` 補一條錯誤情境：

- `429`：配額超過（`dialogue_calls_daily` 或 `daily_tokens` 用完），回應內容要說明哪一種配額用完、下次重置時間，前端可以用這個資訊做 UI 提示（例如「今天已經聊得很盡興了，明天再回來找我吧」這種符合角色人設的訊息，而不是冷冰冰的錯誤訊息——這個文案要腦袋模組準備一句 fallback，不要直接把英文錯誤訊息丟給玩家看）
- `422`：`user_input` 超過 `prompt_max_chars`

---

## 10. 這輪之後，還剩下的缺口

補完到這裡，SDD 距離「可以放手讓 Claude 自主開發整個專案」還差這些，但已經不是這輪能問完的量了，建議留到真的要動工前一模組一模組補：

1. **Prompt 組裝的 Top-K / 記憶輪數 / Flash-Pro 分流規則**——分級配額機制解決了「怎麼調」的問題，但「調成多少」還是要等封測數據，不是這輪能定的
2. **前端（Flutter）完全沒有設計**：畫面流程、狀態管理、跟後端 API 怎麼串
3. **測試策略／驗收標準**：每個功能做完，用什麼判斷「這樣算對」
4. **本機地標辨識（CONTEXT.md 提到的「App 在裝置上判斷相機畫面是否包含指定地標視覺特徵」）的技術方案**：完全沒有提到要用什麼模型/方法，這塊我們都還沒碰過
5. **時區假設要正式過一輪**（第0節的 Asia/Taipei 假設）
6. **`usage_tiers` 的分級策略本身**：目前只有一個 `closed_beta`，「什麼樣的玩家會被分到哪個 tier」「未來要不要做付費分級」都還沒定案，這是產品層級的決定，不是技術問題


