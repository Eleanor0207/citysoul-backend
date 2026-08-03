# 城市靈魂 AR 遊戲 — 地標視覺辨識技術方案（雲端Gemini版）

> 銜接對象：《核心業務規則與API契約》×《Schema/API/Sprint 整合文件》
> 本文件是決策鏈的**第九層**：補齊CONTEXT.md原本留白的「本機地標辨識」技術方案缺口。
> 這輪決策**改用雲端Gemini辨識**，取代原本的本機端模型構想，理由是未來新增地標無法保證每次都能自行採集訓練照片，
> 用Gemini換取不需要為每個新地標重新訓練模型的擴充彈性。
> 這是一次**正式的方向轉換**，會推翻CONTEXT.md三個既有定義，逐條列在第0節。

---

## 0. ⚠️ 對CONTEXT.md既有定義的正式修正

| CONTEXT.md 原定義 | 原文字 | 修正後 |
|---|---|---|
| 本機地標辨識 | 「App在玩家裝置上判斷...只將驗證通過或失敗的結果傳回後端，**不上傳原始影像**」<br>_Avoid_: 後端影像上傳 | 改為**地標視覺辨識**：照片上傳至後端，由後端呼叫Gemini辨識，**原本列為Avoid的「後端影像上傳」現在是必要流程**，但用「即用即丟」（見第2節）盡量保留原始隱私精神 |
| 紀念照片 | 「只存於玩家裝置...**不上傳或保存在MVP後端**」 | 改為：**短暫**上傳供辨識用，辨識完成（不論成功失敗）**立即捨棄，不寫入任何持久化儲存**，App端仍可正常保存/分享紀念照片本體，只是多了一段「上傳辨識」的暫時性動作 |
| 相遇收藏 | 「不含原始照片或GPS座標」 | **維持不變**，`encounter_collections` 表依然只存辨識結果的布林值，不存照片本體 |

**風險等級連帶提醒**：既有風險評估把「隱私邊界」列為🟢低風險，前提是照片完全不離開裝置。這個決定後，隱私風險等級應調升（照片會短暫觸及後端/雲端），且圖片辨識屬多模態呼叫，成本結構與純文字對話不同，會疊加進既有🔴「AI對話成本」風險桶，之後排Sprint時需一併估算。

---

## 1. 流程設計

```
玩家在實景疊圖任務中拍照
  → App 用 multipart/form-data 直接上傳照片到後端
  → 身體（S12）收到照片，存於記憶體（不落地）
  → 身體呼叫腦袋新工作包 B13：recognize_landmark(image_bytes, spirit_id)
  → 腦袋呼叫 Vertex AI Gemini 多模態辨識，回傳布林值
  → 身體取得布林值後，立即捨棄image_bytes（不寫入任何儲存體，含Cloud Storage）
  → 身體回傳 landmark_recognized 給App
  → 玩家提交任務完成時，此布林值作為 completion_evidence 的一部分傳入 /quests/{questId}/complete
```

**任務完成判定不受影響**：CONTEXT.md「觀星儀式」既有原則「本機地標辨識成功時給予特別徽章，失敗不阻擋完成」維持不變——任務完成與否仍是GPS+拍照動作的**確定性規則判定**，不經LLM；只有「要不要給特別徽章」這個加分項目才跑Gemini辨識。既有驗收標準AC4.6（任務判定不經LLM）不受影響。

---

## 2. 「即用即丟」處理方式【已定案】

- 照片（`image_bytes`）**只在記憶體中處理**，從收到請求到呼叫Gemini拿到結果，全程不寫入磁碟、不寫入Cloud Storage、不寫入任何資料庫欄位
- 辨識完成（不論成功或失敗）後**立即釋放**該筆image_bytes
- 不做為模型訓練資料保留、不做為稽核用途保留
- 這是目前能在「業務上必須把照片送到雲端辨識」跟「盡量貼近原始隱私設計精神」之間取得的最大公約數

---

## 3. 內部介面

```python
def recognize_landmark(image_bytes: bytes, spirit_id: str) -> bool:
    """
    B13．地標視覺辨識。
    呼叫方：身體（S12，玩家提交紀念照片時）。
    呼叫 Vertex AI Gemini 多模態辨識，只在記憶體中處理 image_bytes，
    辨識完成（不論成功失敗）立即捨棄，不寫入任何持久化儲存、不留作訓練資料。
    Gemini呼叫失敗/逾時時回傳 False（fallback，不阻擋任務完成，見CONTEXT.md既有原則）。
    """
    ...
```

---

## 4. 外部API契約新增

### `POST /api/v1/quests/{questId}/landmark-photo`

**驗證：** Session Token + Encounter Token

**Request:** `multipart/form-data`，欄位 `photo`（圖片二進位檔）

**處理流程：**
1. 驗證兩張token
2. 檢查 `landmark_recognition_calls_daily` 配額（見第5節）
3. 身體收到照片（記憶體）→ 呼叫腦袋 B13 `recognize_landmark`
4. 立即捨棄照片
5. 回傳辨識結果

**Response 200:**
```json
{ "landmark_recognized": true }
```

**錯誤情境：**
- `401` / `403`：token無效或不符
- `422`：檔案格式不符（非圖片）或檔案過大
- `429`：`landmark_recognition_calls_daily` 配額用完
- Gemini呼叫失敗/逾時 → **不是錯誤**，回傳 `200` 且 `landmark_recognized: false`（fallback，符合「失敗不阻擋完成」精神，任務本身仍可用其他completion_evidence正常完成）

> 此端點與 `/quests/{questId}/complete` 分開設計：前端先呼叫此端點拿到辨識結果，再把 `landmark_recognized` 布林值包進 `completion_evidence` 一併送進 `/complete`，任務完成判定邏輯本身不變。

---

## 5. 配額新增【已定案】

沿用《業務規則與API契約》第9.5節「一個tier對多筆limit」的正規化設計，**不需要改schema**，只需在 `usage_tier_limits` 新增一筆資料：

| resource_type | 意義 | 計算週期 | 建議初始值 |
|---|---|---|---|
| `landmark_recognition_calls_daily` | 每日 `/landmark-photo` 呼叫次數上限 | 每日（Asia/Taipei午夜重置） | **10** |

**初始值理由**：`encounter_collections` 有 `UNIQUE(player_id, place_id)` 約束，每個地標對玩家來說本質上只需要辨識成功一次；即使算上任務重試上限（同一任務當天最多3次失敗重試），單一玩家單日正常遊玩不太可能大量觸發這支API，10次已經涵蓋「當天挑戰2-3個不同地標、每個都重試幾次」的合理範圍，同時能防止異常灌爆。跟其他初始值一樣，屬於封測期保守值，之後可依`usage_tier_limits`資料直接調整,不需重新部署。

第6.4節（dialogue API）429錯誤情境的處理原則，同樣適用於這支新端點：配額用完時要回符合角色人設的fallback文案，不是冷冰冰的錯誤訊息。

---

## 6. WBS異動彙總

| 編號 | 工作包 | 產出 | 依賴 | 異動 |
|---|---|---|---|---|
| B13 | 地標視覺辨識 | Vertex AI Gemini多模態辨識服務函式，即用即丟 | B1 | **新增** |
| S12 | 紀念照片 | ~~本機地標辨識驗證~~ → **呼叫B13進行雲端辨識** + 本機保存/分享 + 不含照片與GPS的相遇收藏紀錄 | F4, B13 | **修改**（依賴新增B13） |

Sprint排程建議：B13 排入Sprint4（與B10 TTS+viseme同期，都是腦袋對外服務類工作包），S12維持在Sprint7不變（僅內部呼叫對象從「本機模組」換成「B13」，不影響排程位置）。
