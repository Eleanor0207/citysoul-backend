# 城市靈魂 AR 遊戲 — 驗收標準清單

> 銜接對象：《核心業務規則與API契約》×《前端互動流程與感應/召喚機制》
> 本文件是決策鏈的**第七層**：把前兩份文件已經定案的精確規則，逐條轉成 Given-When-Then 格式的驗收標準，
> 供開發時逐項驗收「這個功能算不算做對」。不重新設計規則，只做格式轉換與逐條展開。
>
> **時區確認【已定案，取代原假設】**：所有「每日一次」「隔天重置」相關規則，統一以 **Asia/Taipei（UTC+8）午夜**
> 為重置基準點，取代《業務規則與API契約》原本標註的「提案待確認」假設，此文件視為正式拍板。
>
> **測試實作程度說明**：本文件只列驗收標準（要驗證「什麼」），不規定測試框架、覆蓋率數字、單元/整合/e2e的分配比例——
> 這些留給開發時依實際時間自行決定；但每一條標準都應該有對應的測試（形式不拘）能證明它成立。

---

## 1. 在場驗證與召喚（S2）

- **AC1.1** Given 玩家GPS與地標距離 = `summon_radius_m`（含邊界值） When 呼叫 `/summon` Then 在場驗證通過
- **AC1.2** Given 玩家GPS與地標距離 > `summon_radius_m`（例如 `summon_radius_m + 1`） When 呼叫 `/summon` Then 回傳 `403`，不核發 encounter_token
- **AC1.3** Given `spirit_id` 不存在或 `is_active = false` When 呼叫 `/summon` Then 回傳 `404`
- **AC1.4** Given Session Token 無效或過期 When 呼叫 `/summon` Then 回傳 `401`
- **AC1.5** Given 在場驗證通過 When `/summon` 處理完成 Then 回傳的 encounter_token 的 `exp - iat` 精確等於900秒（15分鐘）
- **AC1.6** Given 任意一次成功的 `/summon` 呼叫 When 檢查回傳的 encounter_token payload Then **不含**任何GPS座標欄位

## 2. 感應驗證（S-new，對應 `/sense`）

- **AC2.1** Given 玩家GPS與地標距離 <= `sense_radius_m`（150m） When 呼叫 `/sense` Then 核發 sense_token，成功回應
- **AC2.2** Given 玩家GPS與地標距離 > `sense_radius_m` When 呼叫 `/sense` Then 回傳 `403`
- **AC2.3** Given 任意一次成功的 `/sense` 呼叫 When 檢查回應內容 Then **不包含**任何任務(`quest`)相關欄位（`/sense` 不觸發任務發放）
- **AC2.4** Given 成功核發的 sense_token When 檢查其 `exp - iat` Then 精確等於1800秒（30分鐘）
- **AC2.5** Given sense_token 即將於1-2分鐘內過期、且玩家仍在150m內持續對話 When 前端偵測到即將過期 Then 自動靜默呼叫 `/sense` 換發新token，玩家UI無任何中斷或提示

## 3. Mock Location 防作弊（S3，觀察期）

- **AC3.1** Given `is_mock_location = true` 隨 `/summon` 送出 When 後端處理該請求 Then 寫入一筆log（含player_id、spirit_id、偵測時間、偵測依據），**且該次召喚正常通過**（不因mock location被擋）
- **AC3.2** Given 同上情境 When 檢查回應 Then HTTP狀態碼與正常在場驗證通過時相同（不因mock location標記而回傳錯誤碼）

## 4. 可驗證微任務生命週期（S4）

- **AC4.1** Given 玩家對某任務的 encounter_token 已過期超過15分鐘、且該任務狀態仍是 `in_progress` When 玩家對同一 `spirit_id` 再次呼叫 `/summon` Then 後端判定為一次失敗嘗試，`attempts_today += 1`
- **AC4.2** Given `attempts_today < 3` When 依AC4.1判定失敗後 Then 正常核發新的 encounter_token，任務狀態可重新挑戰（`quest.status = in_progress`）
- **AC4.3** Given `attempts_today >= 3` When 玩家再次呼叫 `/summon` Then 在場驗證仍可通過（玩家可對話），但回應中任務狀態標記為 `daily_limit_reached`，不核發新任務嘗試
- **AC4.4** Given 昨天 `attempts_today >= 3` 已鎖定 When 今天 Asia/Taipei 午夜後玩家再次挑戰 Then `attempts_today` 重置為0，可重新挑戰
- **AC4.5** Given 任務已於今日完成過 When 玩家對同一任務再次呼叫 `POST /quests/{questId}/complete` Then 回傳 `409`
- **AC4.6** Given `completion_evidence` 不符合該任務類型要求的格式、或後端確定性規則判定未達成 When 呼叫 `/quests/{questId}/complete` Then 回傳 `422`，**且不呼叫Gemini**（任務判定不經LLM）

## 5. 共鳴值（S5）

- **AC5.1** Given 任務完成判定通過 When `/quests/{questId}/complete` 處理完成 Then `resonance_value` 在同一個請求內即時 `+= 20`，不需要玩家另外呼叫其他API
- **AC5.2** Given 玩家對同一地標的「相遇收藏」尚未建立過 When 建立相遇收藏 Then `resonance_value += 10`，且該地標收藏只會加這一次（`encounter_collections` 有 `UNIQUE (player_id, place_id)` 約束）
- **AC5.3** Given `resonance_value` 累積跨過 10 / 40 / 100 任一門檻 When `/quests/{questId}/complete` 內部檢查 Then 呼叫 `narrative.generate_unlock_story`，回應中 `unlock_story` 不為 null
- **AC5.4** Given `resonance_value` 未跨過任何新門檻 When 任務完成 Then 回應中 `unlock_story` 為 null
- **AC5.5** Given 同一個 `source_type` + `source_id` 組合已經入過帳（`resonance_events` 的 `UNIQUE (player_id, source_type, source_id)`） When 嘗試重複入帳 Then 應被資料庫約束擋下，不會重複加值

## 6. 相遇憑證與Session Token驗證邊界

- **AC6.1** Given 玩家帶著 encounter_token 呼叫 dialogue API、但 token 內 `spirit_id` 與路徑參數 `placeId` 不一致 When 驗證 Then 回傳 `403`
- **AC6.2** Given 玩家只帶 session_token、沒帶 encounter_token 也沒帶 sense_token 呼叫 dialogue API When 驗證 Then 回傳 `401`
- **AC6.3** Given 玩家帶著有效的 sense_token（無 encounter_token）呼叫 `POST /quests/{questId}/complete` When 驗證 Then 應被拒絕（sense_token 不解鎖任務完成能力）
- **AC6.4** Given 玩家帶著有效的 sense_token（無 encounter_token） When 嘗試觸發相機疊圖畫面（前端邏輯判斷） Then 前端應阻擋跳轉，維持在地圖畫面

## 7. 混合式聊天（B12 快速問候比對層）

- **AC7.1** Given 玩家輸入完全命中 `canned_greetings.trigger_phrases` 中任一詞 When 呼叫dialogue API Then 直接回傳預寫 `response_text`，**不呼叫Gemini**（可用呼叫次數/延遲驗證）
- **AC7.2** Given 玩家輸入未命中任何 `canned_greetings` When 呼叫dialogue API Then 正常走 B4→B2→B1→B10 完整流程

## 8. 配額（Usage Tier）

- **AC8.1** Given 玩家今日 `dialogue_calls_daily` 已達上限 When 再次呼叫dialogue API（不論透過sense_token或encounter_token） Then 回傳 `429`，錯誤內容說明哪一種配額用完與重置時間
- **AC8.2** Given 玩家今日透過150m聊天已消耗部分 `dialogue_calls_daily` 配額 When 玩家50m正式召喚後繼續對話 Then 消耗的是**同一組**每日配額（驗證兩種情境共用同一計數器，不是分開的兩組數字）
- **AC8.3** Given `user_input` 字數超過 `prompt_max_chars` When 呼叫dialogue API Then 回傳 `422`，**且不消耗** `dialogue_calls_daily` 配額（靜態檢查應在配額扣除之前執行）
- **AC8.4** Given 同一player在60秒內的API呼叫次數超過 `api_rate_per_minute` When 呼叫任一受限API Then 回傳 `429`

## 9. Fallback / 服務降級

- **AC9.1** Given Gemini API呼叫失敗或逾時 When dialogue API處理中 Then 回傳 **HTTP 200**（非錯誤碼），內容為人工預寫的fallback台詞
- **AC9.2** Given 排程任務（S10）今天尚未成功產生 `daily_event_cache` 記錄 When 呼叫 `/daily-event` Then 回傳前一天的內容作為保底，**不回傳404空畫面**
- **AC9.3** Given B9（當日情境內容生成）沒有合格的受控輸入（無官方公開活動、無節日資料） When 排程觸發生成 Then 使用人工預寫台詞，不嘗試讓LLM自由發揮

## 10. 地圖標記狀態機（前端）

- **AC10.1** Given 玩家與地標距離 > 150m When 檢視地圖畫面 Then 靈魂標記維持預設樣式
- **AC10.2** Given 玩家與地標距離 <= 150m 且 > 50m When 檢視地圖畫面 Then 靈魂標記顯示「可聊天」的發光/圖示提示
- **AC10.3** Given 玩家與地標距離 <= 50m When 檢視地圖畫面 Then 靈魂標記變為完全點亮、可點擊的召喚按鈕
- **AC10.4** Given 玩家點擊已點亮的召喚按鈕 When 點擊事件觸發 Then 呼叫 `/summon` 並在成功後跳轉相機疊圖畫面；**不會**在僅僅進入50m範圍、尚未點擊時就自動跳轉
- **AC10.5** Given 玩家嘗試在150m外開啟聊天輸入 When 嘗試互動 Then 顯示「你還不夠靠近」提示（模糊提示，**不含精確距離數字**）

## 11. 距離偵測技術（省電方案，已修正）

- **AC11.1** Given App在地圖畫面且玩家未開啟相機疊圖畫面 When 檢查GPS存取模式 Then 應為**前景區間輪詢**，間隔約30秒一次，**不應要求背景定位權限**（可用App權限請求紀錄/log呼叫頻率驗證）
- **AC11.2** Given App已跳轉進相機疊圖畫面（已在50m內） When 檢查GPS存取模式 Then 允許前景持續讀取GPS計算漸顯效果
- **AC11.3** Given 前景輪詢判定50m門檻已跨過、UI召喚按鈕已亮起、但玩家點擊當下實際距離已超過50m（例如輪詢延遲造成的UI誤差） When 呼叫 `/summon` Then 後端仍應回傳 `403`，不因UI已亮起就略過後端距離驗證

## 12. 相機疊圖畫面

- **AC12.1** Given 玩家剛跳轉進相機疊圖畫面 When 檢視對話窗初始狀態 Then 對話窗預設為**展開**狀態
- **AC12.2** Given 玩家點擊收合按鈕 When 對話窗收合 Then 仍可看到AR相機畫面，且收合後可再次點擊展開
- **AC12.3** Given 玩家在50m範圍內距離召喚點越近 When 持續移動 Then 靈魂視覺呈現（透明度/縮放）應隨距離連續漸變，非跳躍式切換

## 13. 開發環境（基礎設施）

- **AC13.1** Given 開發環境啟動 When 檢查資料庫連線目標 Then 指向本地 Postgres/Redis（例如docker-compose服務），而非GCP Cloud SQL/Memorystore
- **AC13.2** Given 未來要切換到雲端環境 When 檢查程式碼異動範圍 Then 僅需更換連線字串/環境變數，不需修改schema或業務邏輯程式碼

---

## 14. 地標視覺辨識（B13，雲端Gemini版）

- **AC14.1** Given 玩家上傳有效圖片檔案 When 呼叫 `/quests/{questId}/landmark-photo` Then 身體呼叫腦袋B13、腦袋呼叫Gemini辨識，回傳 `200` 且 `landmark_recognized` 為布林值
- **AC14.2** Given 任意一次（成功或失敗的）`/landmark-photo` 呼叫已完成 When 檢查後端所有儲存體（資料庫、Cloud Storage、log） Then 找不到任何該張照片的持久化檔案（即用即丟）
- **AC14.3** Given Gemini辨識呼叫失敗或逾時 When 處理 `/landmark-photo` Then 回傳 `200` 且 `landmark_recognized: false`（fallback，非錯誤碼），不阻擋後續任務完成
- **AC14.4** Given 玩家今日 `landmark_recognition_calls_daily` 配額已達上限 When 呼叫 `/landmark-photo` Then 回傳 `429`
- **AC14.5** Given `landmark_recognized: false` 已作為 `completion_evidence` 一部分送進 `/quests/{questId}/complete` When 其餘完成條件（GPS在場、拍照動作）皆滿足 Then 任務仍判定為完成，只是不核發特別徽章（符合CONTEXT.md「失敗不阻擋完成」原則）
- **AC14.6** Given 上傳檔案非圖片格式或超過大小限制 When 呼叫 `/landmark-photo` Then 回傳 `422`，**不消耗** `landmark_recognition_calls_daily` 配額（靜態檢查應在配額扣除之前執行，比照AC8.3）

## 15. 尚未涵蓋、需要後續補充的驗收範圍

以下項目因為對應的設計本身還沒定案，此份清單暫時無法涵蓋，待對應設計補齊後再展開驗收標準：

1. 前端Flutter架構對應的UI層細節驗收標準（美術呈現、動畫參數等）——架構骨架已補齊（見《Flutter前端架構》），但視覺細節驗收標準仍待補
2. Prompt組裝的Top-K/記憶輪數等數字，目前只有預設值建議，沒有「這樣算對」的量化驗收標準（屬於品質調校，非功能對錯）
