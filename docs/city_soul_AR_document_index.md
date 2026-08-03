# 城市靈魂 AR 遊戲 — 文件索引與閱讀指南

> 本文件目的：給接手開發的人（包含自主開發的 Claude Code）一張閱讀順序地圖。
> 這個專案的文件是**決策鏈**，後面的文件會修正/取代前面文件的部分內容——
> 如果不知道閱讀順序跟推翻關係，直接照時間序讀，很容易照著已經被推翻的早期決策去實作。
> 本文件不新增任何規格內容，純粹是導覽索引。

---

## 1. 文件總覽表

| # | 文件 | 角色 | 狀態 |
|---|---|---|---|
| 0 | `CONTEXT.md` | 核心產品詞彙定義，所有文件名詞的地基 | ✅ 現行權威，**但3條定義已被#9局部修正**（見第3節） |
| 1 | `city_soul_AR_project_mainpoint.md` | 最早的MVP規格構想 | ⚠️ **歷史快照，多處已被#2推翻**，僅供理解決策演變脈絡 |
| 2 | `city_soul_AR_project_dev.md` | 對#1的技術決策更新與收斂 | ✅ 現行權威（技術棧、視覺呈現方案） |
| 3 | `city_soul_AR_WBS_API.md` | 模組邊界（腦袋/身體/臉）、工作包清單、初版API清單 | ✅ 現行權威，**S12描述已被#9局部更新** |
| 4 | `city_soul_AR_schema_api_sprint.md` | 資料庫schema、內部介面函式簽章、7-Sprint排程 | ✅ 現行權威 |
| 5 | `city_soul_AR_業務規則與API契約.md` | 精確業務規則、完整外部API契約、token技術規格、人格卡schema、Prompt骨架、配額機制 | ✅ 現行權威，**時區標註已被#7正式拍板** |
| 6 | `city_soul_pixel_map_mockup.html` | 像素風地圖介面視覺參考 | ✅ 視覺參考，非規格文件 |
| 7 | `city_soul_AR_frontend_interaction.md` | 前端互動邏輯（150m/50m雙層距離模型、Sense Token、地圖三段標記、相機疊圖流程） | ✅ 現行權威，**內部第6節有自我修正**（Geofencing→前景輪詢） |
| 8 | `city_soul_AR_acceptance_criteria.md` | 從#0-#7所有已定案規則推導的Given-When-Then驗收標準清單 | ✅ 現行權威 |
| 9 | `city_soul_AR_flutter_architecture.md` | Flutter工程架構（導覽、狀態管理、路由、分層、服務層） | ✅ 現行權威 |
| 10 | `city_soul_AR_landmark_recognition.md` | 地標視覺辨識技術方案（雲端Gemini版） | ✅ 現行權威，**修正了#0三條定義與#3的S12** |

---

## 2. 建議閱讀順序

1. **`CONTEXT.md`** — 先讀這個，建立詞彙共識。讀到「本機地標辨識」「紀念照片」「相遇收藏」時，記得這三條已被#10修正，實作時以#10為準。
2. **`city_soul_AR_project_dev.md`** — 技術棧與視覺呈現的權威版本（可跳過#1 mainpoint，除非想理解決策演變過程）。
3. **`city_soul_AR_WBS_API.md`** → **`city_soul_AR_schema_api_sprint.md`** — 模組邊界、資料表、內部介面、Sprint排程。
4. **`city_soul_AR_業務規則與API契約.md`** — 精確規則與完整API契約，這是後端實作最直接的依據。
5. **`city_soul_pixel_map_mockup.html`** — 搭配下一份文件對照畫面視覺。
6. **`city_soul_AR_frontend_interaction.md`** — 前端互動邏輯與感應/召喚機制。
7. **`city_soul_AR_flutter_architecture.md`** — Flutter工程架構怎麼組織程式碼。
8. **`city_soul_AR_landmark_recognition.md`** — 地標辨識最終方案，讀完回頭對照#0的修正。
9. **`city_soul_AR_acceptance_criteria.md`** — 開發過程中每完成一個功能，回來對照這份逐條驗收。

---

## 3. 文件間互相推翻對照表（最重要的一節）

| 被推翻的內容 | 出自 | 推翻/修正它的文件 | 修正後的內容摘要 |
|---|---|---|---|
| Unity + AR Foundation、Convai NPC | #1 mainpoint | #2 dev | 改為 2.5D Billboard（Rive/Lottie）+ 自建 Prompt + Vertex AI Gemini |
| 「經過咖啡店主動推薦」效果 | #1 mainpoint | #2 dev | 已拿掉，超出10個地標內容範圍 |
| 環境感知互動需要背景定位追蹤（與隱私原則衝突的認定） | #1 mainpoint | #2 dev | 重新界定為「主動召喚/相機模式當下即時運算」，不算背景追蹤 |
| 「本機地標辨識」「紀念照片：不上傳」「相遇收藏：不含原始照片」中的**上傳限制部分** | #0 CONTEXT.md | #10 landmark_recognition | 改為雲端Gemini辨識，照片「即用即丟」（記憶體處理、辨識完立即捨棄，不寫入任何儲存體） |
| S12工作包描述「本機地標辨識驗證」 | #3 WBS-API | #10 landmark_recognition | 改為「呼叫B13進行雲端辨識」，新增B13工作包 |
| 時區「Asia/Taipei」標註為【提案待確認】 | #5 業務規則契約 | #8 acceptance_criteria | 正式拍板確認為 Asia/Taipei（UTC+8） |
| 地圖畫面150m/50m門檻偵測用原生Geofencing API | #7 frontend_interaction 第6節（原始版本） | #7 frontend_interaction 第6節（同文件內自我修正） | 改用 `geolocator` 前景區間輪詢（30秒），理由：50m半徑低於geofence建議下限100m會有GPS漂移誤判、且原生geofencing需要背景定位權限與隱私原則有落差 |
| `/resonance/{spiritId}/check`（POST，會觸發副作用） | #3 WBS-API | #5 業務規則契約 第4節 | 改為 `GET /resonance/{spiritId}`，唯讀查詢 |

> ⚠️ **給自主開發agent的提醒**：如果之後開新對話讓Claude接手開發、只餵了部分文件，務必連同這份索引一起提供，避免Claude只看到#1或#3的舊版描述就照做。

---

## 4. 尚未解決的缺口彙總

這些不影響開發起手，屬於開發過程中會自然需要補上的細節：

| 缺口 | 影響範圍 | 建議處理時機 |
|---|---|---|
| `canned_greetings` 實際觸發語與預寫回覆內容 | 人格卡內容 | 需敘事負責人撰寫，Sprint3（B4/B2）前補齊即可 |
| 150m發光、相機疊圖漸顯效果的具體美術參數（透明度/縮放曲線） | 臉模組(F)美術細節 | Sprint3-4（F3美術動畫）前補齊 |
| 429配額用完時，各情境角色口吻fallback文案 | 腦袋模組內容 | Sprint4（B9/B10）前補齊 |
| Prompt組裝的Top-K/記憶輪數/Flash-Pro分流數字 | 腦袋Prompt調校 | 先用文件建議的預設值開發（K=3、短期記憶6輪），封測後依實測數據調整 |
| `usage_tiers` 分級策略本身（目前只有`closed_beta`一組） | 產品層級決策 | 上線前需要，開發期不影響 |

---

## 5. 給接手開發者/Claude Code的起手建議

1. 先照第8節《schema_api_sprint.md》第0節的說明，用本地Postgres+Redis（docker-compose）建開發環境，不急著佈建GCP服務（見#7文件第8節的開發期基礎設施決策）
2. 照Sprint1的工作包起手：B3人格卡載入、B7短期記憶、S1 GPS模組、S6匿名玩家身分、F1角色美術
3. 每完成一個功能，回頭對照#8驗收標準清單裡對應的AC編號逐條驗收
4. 遇到文件之間看起來矛盾時，先查第3節的推翻對照表，通常是後面的文件版本才對
