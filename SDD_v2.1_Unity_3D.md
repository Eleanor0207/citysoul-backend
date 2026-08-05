# 城市靈魂 — 系統設計文件（SDD v2.1）

> **版本**：v2.1 ／ 2026-08-05
> **取代對象**：`SDD.md`（v1，Flutter + 2.5D Billboard 版本）
> **本次變更性質**：客戶端技術棧與視覺呈現層全面改版；後端架構、業務規則、API 契約原則上不變。
>
> 本文件為單一權威來源。凡與 `SDD.md`(v1)、`city_soul_AR_flutter_architecture.md`、
> `city_soul_AR_project_dev.md`、`city_soul_AR_project_mainpoint.md` 衝突者，一律以本文件為準。
> 標示【待確認】者代表仍需負責人拍板，不是本文件擅自決定。

---

## 0. 本次架構變更摘要

一句話：**客戶端從 Flutter + 2.5D 平面立牌，改為 Unity + 3D 角色 + 3DoF 感測器定向的實境疊層。後端完全不動。**

| # | 決策 | 狀態 |
|---|---|---|
| 1 | 客戶端引擎改用 **Unity 6**，Flutter 全面退場，不採用 Godot | ✅ 定案 |
| 2 | **不做真 AR**（不裝 AR Foundation / ARCore / ARKit / Geospatial），改用 **3DoF 感測器定向實境疊層** | ✅ 定案 |
| 3 | 角色改為 **3D 模型**，美術方向為 **半寫實 × 混合靈魂感**（主體實體感 ＋ 輪廓發光 ＋ 腳部淡出） | ✅ 定案 |
| 4 | 對嘴改為 **uLipSync 客戶端即時音訊分析**，推翻 B10 viseme 時間軸設計 | ✅ 定案 |
| 5 | 開發節奏改為 **3 天一個 Sprint**，排程重構為 **7 階段垂直切片** | ✅ 定案 |
| 6 | 垂直切片首個地標改為 **龍山寺** | ⚠️ 定案但有風險，見 §12 |
| 7 | 資產產線**暫定 ComfyUI / Hunyuan3D**，CC4 為備援 | ⚠️ 暫定，Phase 0 後複審 |

---

## 1. 決策推翻對照表（最重要的一節）

接手開發的人請先讀這張表。專案歷史上已換過三次技術棧（Unity → Flutter 2.5D → Unity 3D），舊文件仍在專案內，容易誤讀。

| 項目 | 舊版 | v2.1 修正後 | 理由 |
|---|---|---|---|
| 客戶端引擎 | Flutter | **Unity 6** | 3D 角色＋動畫狀態機＋相機疊層，且為團隊現有能力 |
| 曾評估的替代 | Godot | **不採用** | Android 手機 AR 僅有研究等級社群外掛（`GodotVR/godot_arcore`：0 release、11 open issue），且缺乏跨平台 AR 抽象層 |
| 視覺呈現 | 2.5D Billboard（Rive/Lottie） | **3D mesh ＋ skeletal animation** | 目標體驗需要體積感與真透視 |
| 空間定位 | 無（純相機疊圖） | **3DoF 感測器定向**（陀螺儀＋電子羅盤） | 角色留在固定方位，玩家需轉動手機尋找 |
| 真 AR / 空間錨定 | 明確排除 | **仍明確排除**，移入 Roadmap | 見 §3 |
| 對嘴方案 | B10 產 viseme 時間軸 → F5 播放 | **uLipSync 客戶端即時分析** | Google Cloud TTS **不提供** viseme／phoneme 時間軸，原設計不可實作 |
| B10 職責 | TTS ＋ viseme 時間軸 | **只做 TTS，回傳音檔 URL** | 同上 |
| F5 職責 | 播放 viseme 時間軸，不分析音訊 | **即時 MFCC 分析驅動 shape key** | 同上 |
| 素材交付 | Cloud Storage ＋ CDN（格式未定） | **Unity Addressables remote bundle** | Unity runtime 無法載入 FBX |
| 地圖底圖 | Google Maps SDK ＋ Tile Overlay | **Unity 自建 raster tile renderer** ＋ 同一套風格化圖磚 | Unity 無 Google Maps SDK 綁定；圖磚方案不變 |
| 距離偵測 | `geolocator` 前景輪詢 | **Unity `Input.location` 前景輪詢** | 純平台替換，門檻邏輯不變 |
| 狀態管理／路由／網路層 | Riverpod / GoRouter / dio | **Unity 場景管理 ＋ UnityWebRequest ＋ 自建 TokenManager** | Flutter 專屬方案全數作廢 |
| 安全儲存 | `flutter_secure_storage` | **Android Keystore（Unity Plugin 封裝）** | |
| Sprint 長度 | 兩週（隱含） | **3 天** | 本次定案 |
| 排程結構 | 7 Sprint 橫向堆疊 | **7 階段垂直切片，共約 27 個 3 天 Sprint** | 見 §13 |
| 垂直切片地標 | 天文館 | **龍山寺** | 本次定案，風險見 §12 |
| 人員代號 | A / B / C / D | **模組角色名（Lead／身體／腦袋／臉）** | 避免與工作包代號 B/S/F 衝突 |
| CONTEXT.md「完整 3D 角色」列為 Avoid | Avoid | **解除** | 本次改版核心 |
| CONTEXT.md「真 AR 角色」列為 Avoid | Avoid | **維持 Avoid** | 決策 2 |
| 裝置支援 | 全 Android/iOS | **維持全 Android/iOS**（不用 ARCore，無認證白名單） | 決策 2 的紅利 |
| 封測平台優先序 | 未定 | **Android 優先** | 定案 |

**完全沿用 v1、不要重寫的部分**：

- 全部業務規則：150m 感應 / 50m 召喚、三種 Token、任務狀態機、共鳴值、配額、防作弊觀察期
- 全部 API 契約（除 §10 標註的兩處回應欄位異動）
- 全部資料庫 schema（除 §10.2 新增兩個欄位）
- 腦袋模組 B1–B9、B11、B12、B13
- 隱私原則與 Schema 硬約束（不得新增 `location_history` / `movement_trace` 類型的表）
- 時區 Asia/Taipei（UTC+8）

---

## 2. 產品概述

**核心迴圈**：玩家在指定地標完成「到場、召喚、當日情境短對話、可驗證微任務」的一次完整體驗，並有理由在隔天回來查看變化。

**首批玩家（TA）**：住在台北、約 18–35 歲，喜歡城市探索、角色互動與拍照分享的繁中使用者。MVP 語言統一為台灣繁體中文。

**封閉測試垂直切片**：以**單一龍山寺靈魂**驗證核心迴圈、內容生產、成本與隱私邊界，通過驗證後才擴展到首發十個城市靈魂。

> ⚠️ 此處與 CONTEXT.md 原定的「天文館靈魂」不同，屬本次改版的變更。內容治理風險見 §12.2。

---

## 3. 產品邊界（Avoid 清單，v2.1 更新版）

- ~~不做完整 3D 角色~~ → **改為採用 3D 角色**〔本次解除〕
- **不做真 AR**：不做 SLAM 空間錨定、平面偵測、遮擋判斷、深度估算〔維持〕
  - 技術含意：不引入 AR Foundation、ARCore、ARKit、Geospatial API
- **不做背景定位追蹤**，僅在玩家主動開啟召喚/相機/感應模式時前景即時運算〔維持〕
- 原始 GPS 只在驗證期間使用後即丟棄，不形成軌跡〔維持〕
- 世界記憶不含任何玩家輸入；玩家記憶僅屬單一玩家〔維持〕
- 可驗證微任務由後端確定性規則判定，不由 LLM 判定〔維持〕
- 「城市靈魂」是地標的擬人化集體意識，不扮演任何特定真人或歷史人物〔維持〕
- 玩家在 MVP 以文字輸入，不使用語音辨識〔維持〕
- 不做「經過咖啡店主動推薦」類商家內容〔維持〕
- **不對特定宗教信仰、神祇、儀式作出教義性陳述或裁決**〔本次新增，因應龍山寺切片〕

---

## 4. 技術棧

### 4.1 客戶端

| 項目 | 選型 | 備註 |
|---|---|---|
| 引擎 | **Unity 6**（URP） | 行動裝置最佳化 |
| 建置目標 | **Android（ARM64 only）** 優先 | 不出 ARMv7 |
| 3D 資產載入 | **Addressables**（remote bundle） | §7 |
| 對嘴 | **uLipSync** | §8 |
| 定位 | Unity `Input.location` 前景輪詢 | 門檻沿用 v1 |
| 方位 | `Input.gyro` ＋ `Input.compass` | 3DoF 核心 |
| 相機背景 | `WebCamTexture` → 全螢幕 RawImage | |
| 網路 | `UnityWebRequest` ＋ 自建攔截層 | |
| 安全儲存 | Android Keystore（Unity Plugin） | 不得用 `PlayerPrefs` |
| 建置 | **GitHub Actions ＋ GameCI** | §11.4 |

### 4.2 後端（不變）

Python / FastAPI ｜ GCP Cloud Run ｜ Vertex AI 單一快速模型 ｜ Cloud SQL for PostgreSQL ＋ pgvector ｜ Redis ｜ Google Cloud TTS ｜ Cloud Storage ＋ CDN。

開發期用本地 docker-compose（Postgres + Redis）。模型服務失敗時一律回退人工預寫台詞。

---

## 5. 客戶端架構（Unity）

### 5.1 3DoF 實境疊層方案〔本次新增〕

| 層級 | 手機轉動時 | 採用 |
|---|---|---|
| 0DoF 純疊層 | 角色永遠在螢幕正中 | ❌ |
| **3DoF 感測器定向** | 角色固定在某**方位角**，需轉動手機才看得到 | ✅ |
| 6DoF SLAM | 角色釘在世界座標，可繞行 | ❌ Roadmap |

**實作原則**：

1. 每個靈魂在 `spirits` 表設定方位角（§10.2）
2. 讀取 `Input.compass.trueHeading` 取得手機朝向
3. 角色螢幕水平位置 = f(靈魂方位角 − 手機朝向)；超出視野不繪製
4. 螢幕邊緣顯示方向指示器引導轉向
5. `Input.gyro.attitude` 補償俯仰/翻滾，維持角色水平站立
6. 角色縮放依 GPS 距離插值（沿用 v1 漸顯機制），非相機實算透視

**已知取捨**：無遮擋、無真實景深、羅盤受都市磁干擾、玩家平移時無視差。

**必做緩解手段**（非加分項）：接地 blob shadow（不透明度約 0.3）、相機畫面平均色溫匹配 ambient light、邊緣柔化、靈魂感 shader（§9）。

### 5.2 場景清單

| 場景 | WBS | 說明 |
|---|---|---|
| Bootstrap | S6 | 背景呼叫 `POST /players` 或帶出既有 session，無登入 UI |
| MapScene | S7 | 風格化圖磚地圖、三段式標記、150m 內嵌入對話、當日情境卡片 |
| EncounterScene | F4/F5/S9/S14 | 50m 召喚成立後 **additive load**；相機背景＋3D 角色＋可收合對話窗 |
| QuestScene | S8 | 任務狀態、嘗試次數 |
| ProfileScene | S8 | 共鳴值進度、相遇收藏、記憶摘要 |

對話 UI 做成共用 prefab，同時被 MapScene（sense_token 模式）與 EncounterScene（encounter_token 模式）使用。對話延續靠後端 Redis key（`player_id`+`spirit_id`）天然成立。

### 5.3 專案結構

```
Assets/_Project/
  Core/            # 常數、錯誤處理、事件匯流排
  Services/
    TokenManager.cs        # 三種 token 生命週期
    LocationService.cs     # 前景輪詢
    OrientationService.cs  # 陀螺儀＋羅盤（3DoF）      ★新增
    ApiClient.cs           # UnityWebRequest ＋ 攔截層
    AssetService.cs        # Addressables 載入與快取   ★新增
  Features/
    Map/  Encounter/  Quest/  Profile/  Dialogue/
  Characters/      # 🎭 臉的工作區（prefab / Animator / shader / 材質）
```

> **資料夾即分工邊界**：`Characters/` 由臉持有，`Features/` 與 `Services/` 由身體持有。兩人共用同一個 Unity 專案但不互相改對方資料夾，降低合併衝突。

### 5.4 TokenManager（邏輯沿用 v1）

| Token | 效期 | 換發時機 |
|---|---|---|
| Session | 90 天 | App 啟動時換新，玩家無感 |
| Sense | 30 分鐘 | 進入 150m 核發；剩 1–2 分鐘且仍在範圍內時**靜默**重呼 `/sense` |
| Encounter | 15 分鐘 | 50m 召喚成立核發；過期即任務嘗試失敗，不續期 |

儲存於 Android Keystore。三種 token 在後端須用不同中介層驗證，不可共用邏輯。

### 5.5 LocationService

- MapScene 前景：每 30 秒查一次位置，更新 150m / 50m 門檻狀態
- EncounterScene：連續讀取，供漸顯插值
- 切背景或離開 MapScene：停止輪詢，**不申請背景定位權限**

不使用原生 Geofencing API（v1 已論證）。v1 的「雙 Geofence 穩定性 POC」**取消**。

### 5.6 OrientationService〔新增〕

提供 `CurrentHeading`（真北方位角）與 `DeviceAttitude`；偵測 `headingAccuracy` 劣化並觸發 8 字校正提示；僅在 EncounterScene 啟用。

### 5.7 地圖

- 底圖：**Unity 自建 raster tile renderer**（quad 網格貼圖磚）
- 圖磚來源沿用 v1：OSM（ODbL）→ PyraCanny 線稿萃取 → Fooocus 風格化生成
- 三段式標記（不變）：>150m 預設樣式 ／ ≤150m 淡淡發光可聊天 ／ ≤50m 完全點亮可召喚
- 50m 觸發：玩家**主動點擊**召喚按鈕才呼叫 `/summon`，**不自動跳轉**
- 「你還不夠靠近」為模糊提示，不顯示精確距離數字

> ⚠️ Google Maps SDK 依賴已移除，手勢/縮放/定位點需自行實作。此為本次新增工作量，已提前至 Phase 2。

---

## 6. 模組邊界與工作包

三模組定義不變，唯一跨模組關聯欄位仍是 `player_id`。

### 6.1 🧠 腦袋 Brain（全部在後端）

| 代號 | 工作包 | v2.1 異動 |
|---|---|---|
| B1 | Vertex AI 串接＋失敗回退 | — |
| B2 | Prompt 組裝引擎 | — |
| B3 | 人格卡載入與版本管理 | — |
| B4 | 角色安全邊界檢查層 | ★**難度上升**（宗教場所，見 §12.2） |
| B5 | 史實邊界規則注入 | ★**難度上升**（同上） |
| B6 | 長期記憶（pgvector） | — |
| B7 | 短期記憶（Redis） | — |
| B8 | 記憶摘要 nightly batch | — |
| B9 | 當日情境內容生成 | — |
| B10 | TTS 串接 | ★**縮減**：移除 viseme 時間軸產出 |
| B11 | 共鳴值解鎖敘事生成 | — |
| B12 | 快速問候比對層 | — |
| B13 | 地標視覺辨識（雲端 Gemini，即用即丟） | — |

### 6.2 🦾 身體 Body

| 代號 | 工作包 | 環境 |
|---|---|---|
| S1 | GPS 定位模組 | 客戶端 |
| S2 | 在場驗證與召喚半徑判定 | 後端 |
| S3 | 防作弊（mock location、速度合理性） | 後端 |
| S4 | 任務進度表＋狀態機 | 後端 |
| S5 | 共鳴值表＋判定邏輯 | 後端 |
| S6 | 匿名玩家身分／帳號升級綁定 | 後端＋客戶端 |
| S7 | UI：地圖／召喚點（★含自建 tile renderer） | 客戶端 |
| S8 | UI：任務列表／Profile | 客戶端 |
| S9 | 引導提問元件 | 客戶端 |
| S10 | 當日情境排程＋快取＋對外 API | 後端 |
| S11 | 訊號推播 | 後端＋客戶端 |
| S12 | 紀念照片：本機保存/分享＋呼叫 B13 | 後端＋客戶端 |
| S13 | 行銷／分享功能 | 客戶端 |
| **S14** | **3DoF 方位定向服務** ★新增 | 客戶端 |

### 6.3 🎭 臉 Face

| 代號 | v1 定義 | v2.1 定義 | 需 Unity |
|---|---|---|---|
| F1 | 角色美術造型設計（CC 產線） | ★概念設計 ＋ 生成規範制定 | ❌ |
| F2 | 精靈圖／序列幀烘焙管線 | ★Blender 清理／減面／UV／貼圖烘焙 | ❌ |
| F3 | Rive/Lottie 動畫實作 | ★Mixamo 綁骨 ＋ 動作庫 ＋ Blender shape key | ❌ |
| F4 | 相機疊圖渲染 | ★角色 prefab ＋ 靈魂感 shader ＋ 材質設定 | ✅ |
| F5 | 口型同步播放器（吃 viseme） | ★uLipSync 整合 | ✅ |
| F6 | 素材版本管理 | ★Addressables 打包 ＋ remote catalog | ✅ |
| F7 | App 端素材下載與快取 | ★下載進度／快取／版本比對 | ✅ |
| **F8** | — | ★Animator 狀態機（idle ↔ talking ↔ 揮手 ↔ 轉身） | ✅ |

> ⚠️ **人力缺口【待確認】**：F4–F8 需 Unity 能力（Animator、Shader Graph、Prefab、Addressables），屬**技術美術（Technical Artist）**範疇。
> 若臉不具備此能力，F4/F5/F7/F8 需由身體代持，**但身體已是關鍵路徑瓶頸**。
> 建議處理：① 臉補足 Unity 技能（Phase 0–1 期間）；② 或依「來不及會加人力」原則，補一名技術美術。
> 本文件排程**假設臉持有全部 F 模組**；若改由身體代持，Phase 2–5 需重新估時。

### 6.4 模組邊界設計原則

| 決策 | 內容 |
|---|---|
| 當日情境 | 內容生成（腦袋）與排程/快取/serve（身體）拆分 |
| ~~viseme 時間軸~~ | ★**廢除**：改為臉自行分析音訊 |
| 共鳴值解鎖 | 身體判定達標 → 呼叫腦袋生成敘事（順序不可顛倒） |
| 記憶 schema owner | 結構化欄位屬身體；語意化欄位屬腦袋 pgvector；僅靠 `player_id` 關聯 |
| **資產交付邊界** | ★**新增**：見 §7.3 |
| **Unity 資料夾邊界** | ★**新增**：臉持有 `Characters/`，身體持有 `Features/` `Services/` |

**Schema 硬約束（不變）**：不得新增任何 `location_history` / `movement_trace` 類型的表。

---

## 7. 資產產線與交付規格

### 7.1 產線（暫定路線）

```
① ComfyUI / Hunyuan3D    A-Pose 正面全身圖 → .glb 草模
② Blender 清理           分離四肢/頭髮/衣物、減面、UV、貼圖烘焙
③ Mixamo 綁骨            下載 Idle / Talking / Wave / Turn
                         ⚠️ Mixamo 會清除 blend shape，shape key 必須排在此步驟之後
④ Blender Shape Keys     JawOpen、Blink_L、Blink_R → 匯出 .fbx
⑤ Unity                  Animator(F8) → uLipSync(F5) → 靈魂感 shader(F4) → Addressables(F6)
```

### 7.2 ⚠️ 產線最大風險與備援

**風險**：AI 生成 mesh 常見「頭髮與肩膀熔接」「裙襬將雙腿黏連」「四肢邊界不清」，導致 **Mixamo 自動綁骨失敗**，且清理工時不可預測。

**備援（Character Creator 4）**：出廠即含骨架、臉部 rig 與標準 viseme，10 隻風格一致性易控管；為付費軟體、風格自由度較低。

**複審時點**：Phase 0 的 POC-資產 結束時，通過門檻見 §13.2。

### 7.3 資產交付規格書

臉交付給身體的每一隻角色必須符合以下規格，**交件前自檢**：

| 項目 | 規格 |
|---|---|
| 格式 | `.fbx`（含骨架、動畫、shape key） |
| 面數 | **15,000 – 20,000 tris** |
| 貼圖 | Albedo / Normal / ORM 各一，**2048×2048**，ASTC 壓縮 |
| 材質數 | ≤ 3（身體、頭髮、眼睛） |
| 骨架 | Mixamo 標準命名，Unity Humanoid rig 相容 |
| shape key 命名 | `JawOpen`、`Blink_L`、`Blink_R`（**大小寫敏感，不得增減或改名**） |
| 眼睛 | 獨立材質（供貼圖切換眨眼備援） |
| 動作 | `Idle`、`Talking`、`Wave`、`Turn` 四組完整 loop |
| 座標原點 | 腳底中心置於 (0,0,0)，面向 +Z |
| **資產包上限** | **< 10 MB**（Addressables 打包後） |

### 7.4 Addressables 交付鏈

打包 remote bundle → 上傳 Cloud Storage → 沿用既有 `GET /api/v1/assets/{avatarId}` 回傳 catalog／bundle URL 與 version。

**下載策略**：不預載全部角色；玩家進入某地標 150m 感應範圍時才觸發該角色下載。Profile 頁提供「清除快取」入口。

### 7.5 APK 體積預算

| 項目 | 目標 |
|---|---|
| APK 下載大小（不含遠端角色資產） | **< 100 MB** |
| 單一角色資產包 | < 10 MB |
| 繁中字型（子集化） | < 3 MB |
| 建置架構 | **ARM64 only** |

---

## 8. 對嘴方案（uLipSync）

### 8.1 推翻理由

v1 設計「B10 呼叫 TTS 時一併取得 viseme 時間軸 → F5 播放」。**此設計不可實作**：Google Cloud TTS 不提供 viseme 或 phoneme 時間軸輸出。此為架構缺陷，本次一併修正。

### 8.2 新設計

| 模組 | v1 | v2.1 |
|---|---|---|
| B10 | TTS ＋ viseme 時間軸 | **只做 TTS，回傳音檔 URL** |
| F5 | 播放 viseme 時間軸 | **uLipSync 即時 MFCC 分析驅動 shape key** |
| 跨模組介面 | 音檔 ＋ viseme JSON | **只剩音檔 URL** |

### 8.3 MVP 精細度：下巴開合模式

考量 AI 生成 mesh 的臉部拓樸限制，MVP **不做完整母音 viseme**：

- uLipSync 以**音量包絡**驅動單一 `JawOpen` shape key（0–1）
- 眨眼透過 `Blink_L/R`，或眼睛材質切換（備援）
- 在實境疊層觀看距離下，與完整 viseme 的體感差異有限

**完整 viseme（A/I/U/E/O/N）** 列為 Roadmap。**N（閉嘴型）為必要項**——中文雙唇音（ㄅㄆㄇ）需嘴唇閉合，未來升級不得省略。

### 8.4 效能

uLipSync 的每幀 MFCC 運算與 3D 渲染、相機串流、GPS 輪詢疊加。**中階 Android 連續 5 分鐘的幀率與發熱須於 Phase 0 實測**，優先度高於 APK 體積。

---

## 9. 美術方向與 shader 規格

### 9.1 定案方向：半寫實 × 混合靈魂感

| 部位 | 處理 |
|---|---|
| 身體與臉 | Alpha **0.9–0.95**，保留半寫實 PBR 質感 |
| 輪廓 | 微弱 Fresnel rim light，僅邊緣一圈，**不整體發光** |
| 小腿以下 | 垂直漸層 dissolve，淡出成光點 |
| 配色 | 每地標一主題色，僅出現於 rim 與 dissolve |
| 呼吸感 | rim 強度以 sin 波起伏（週期 3–4 秒） |
| 接地 | 橢圓漸層 blob shadow，不透明度約 0.3 |

### 9.2 Unity URP Shader Graph 規格

```
基底：Lit → Surface Type = Transparent，Alpha 0.9–0.95（ScriptableObject 可調）
邊緣：Fresnel Effect → Power 2–4 → × 主題色 → Emission
腳部：Object Space Position.y → Smoothstep → × Alpha
呼吸：Time → Sine → Remap(0.8, 1.0) → × Fresnel 強度
```

**設計優勢**：靈魂感完全在 shader 層，**與資產解耦**。日後導入真 AR 空間錨定時，只需 Alpha 調至 1.0、關閉 rim 與 dissolve 即得完整實體角色，**不需重做資產**。

### 9.3 【待補】

10 個地標主題色定義 ｜ rim 強度與 dissolve 曲線具體參數 ｜ 150m 標記發光美術參數 ｜ 角色概念設計稿與風格一致性規範

---

## 10. API 契約異動

**原則：全部沿用 v1，僅以下兩處異動。**

### 10.1 `POST /api/v1/spirits/{placeId}/dialogue`

Response 200 的 `tts` 物件**移除** `viseme_timeline`：

```jsonc
// v1
{ "reply_text": "...", "tts": { "audio_url": "...", "viseme_timeline": [...] } }
// v2.1
{ "reply_text": "...", "tts": { "audio_url": "..." } }
```

同步影響內部介面 `TTSResult` pydantic model 與 `UnlockStory.tts`。

### 10.2 `GET /api/v1/spirits/{placeId}`

新增靈魂方位設定，供 3DoF 定向使用：

```jsonc
{
  // ...既有欄位
  "orientation": {
    "bearing_deg": 135.0,      // 相對召喚點的方位角（真北 0°，順時針）
    "height_offset_m": 0.0
  }
}
```

```sql
ALTER TABLE spirits ADD COLUMN bearing_deg     DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE spirits ADD COLUMN height_offset_m DOUBLE PRECISION NOT NULL DEFAULT 0;
```

### 10.3 未異動

`POST /players`、`POST /sense`、`POST /summon`、`GET /daily-event`、`GET /quests/daily`、
`POST /quests/{questId}/complete`、`GET /resonance/{spiritId}`、`GET /players/me/memory-summary`、
`GET /assets/{avatarId}`、`POST /quests/{questId}/landmark-photo`。

Token 設計、配額規則、錯誤碼、fallback 策略全數不變。

---

## 11. 團隊、專案結構與硬體

### 11.1 分工（改用模組角色名，避免與工作包代號衝突）

| 角色 | 工作包 | 主要環境 |
|---|---|---|
| 🧭 **Lead**（你） | SDD、API 契約、整合、驗收測試、地標勘查主辦 | Unity（唯讀/審查）＋ Docker |
| 🦾 **身體 Body** | **S1–S14** | Unity（`Features/` `Services/`）＋ 後端 S 部分 |
| 🧠 **腦袋 Brain** | **B1–B13** | 純後端，不碰 Unity |
| 🎭 **臉 Face** | **F1–F8** | Blender / Mixamo ＋ Unity（`Characters/`） |

> ⚠️ **負載提醒**：腦袋承擔 13 個工作包為單人最重；身體橫跨客戶端與後端且為關鍵路徑。
> Lead 應預留下海支援餘裕，優先支援腦袋的後端 S 工作包。
> 依「來不及會加人力」原則，優先補位順序建議：① 技術美術（分擔 F4–F8）② 後端（分擔 S2–S5）。

### 11.2 Repo 結構〔本次新增〕

底層已全面更換，**建議新開專案**，不沿用任何 Flutter 時期 repo。

| Repo | 內容 | 關鍵設定 |
|---|---|---|
| `citysoul-backend` | FastAPI、schema、alembic、docker-compose | 一般 Python `.gitignore` |
| `citysoul-client` | Unity 專案 | **Git LFS**（`*.fbx` `*.png` `*.wav` `*.bundle`）＋ Unity `.gitignore` ＋ **Force Text** serialization ＋ **Visible Meta Files** |
| （資產原始檔） | `.blend`、ComfyUI 輸出、4K 貼圖來源 | **不進 Git**，走雲端硬碟；僅交付用 `.fbx` 進 client repo |

Unity 專案設定務必在第一天就開好（Force Text / Visible Meta Files），事後改會產生大量無意義 diff。

#### 11.2.1 API 契約單一真相來源〔本次新增〕

拆兩個 repo 的代價是「後端改了契約、客戶端沒跟上」，以 3 天 Sprint 的節奏這會頻繁發生。處理方式為 **code-first ＋ CI 契約閘門**，不採用純 schema-first（FastAPI 不擅長從 OpenAPI 反向生成 server code，摩擦成本過高）。

**流程**：

```
① 後端     FastAPI 自動產生 /openapi.json
② 後端 CI  與已 commit 的 contracts/openapi.json 比對（oasdiff）
             破壞性變更（刪欄位／改型別／改必填）→ ❌ build 失敗
             僅新增欄位 → ✅ 通過並更新快照
③ 後端     merge 至 main 後，CI 將 openapi.json 推為 Release artifact（帶版本 tag）
④ 客戶端   script 拉取最新 openapi.json
             以 NSwag / openapi-generator 生成 C# DTO
             → Assets/_Project/Services/Generated/  ★不得手改
```

**Unity 端硬規則**：

| # | 規則 | 理由 |
|---|---|---|
| 1 | **只生成 DTO model，不生成 HTTP client** | generator 預設產出的 `HttpClient` 版本不適用 Unity；`ApiClient.cs` 維持手寫（`UnityWebRequest`） |
| 2 | **使用 Newtonsoft.Json**（`com.unity.nuget.newtonsoft-json`），**不得使用 `JsonUtility`** | `JsonUtility` 不支援 Dictionary 與 nullable，無法正確處理 `unlock_story: {...} \| null` 等結構 |
| 3 | `POST /quests/{questId}/landmark-photo` **手寫** | `multipart/form-data`，codegen 產物不可直接使用 |
| 4 | `Generated/` 檔頭標註 `DO NOT EDIT`，CI 檢查是否與最新 schema 一致 | 防止手改後被下次生成覆蓋 |
| 5 | Enum 採寬鬆解析，未知值 fallback 而非拋錯 | 後端新增 enum 值對舊版 App 屬破壞性變更 |

**版本相容原則**：

- **只加不減**：後端可新增欄位，**不得刪除或改名**既有欄位；客戶端忽略未讀欄位
- 破壞性變更必須開 `/api/v2`，舊版保留一段過渡期
- CI 閘門可由特定 PR label（`breaking-change`）跳過，但**必須同時提交 v2 路由**
- 契約凍結時點：Phase 1（見 §13.3）

### 11.3 硬體需求

| | Lead | 身體 | 腦袋 | 臉 |
|---|---|---|---|---|
| Unity ＋ Android Build Support | ✅ | ✅ | ❌ | ✅ |
| Unity Library 快取 | ✅ | ✅ | ❌ | ✅ |
| Android SDK / NDK | CI 代勞 | ✅ | ❌ | CI 代勞 |
| Docker（Postgres/Redis） | ✅ | ✅ | ✅ | ❌ |
| Blender | ❌ | ❌ | ❌ | ✅ |
| ComfyUI ＋ 3D 權重 | ❌ | ❌ | ❌ | ⚠️ 雲端 GPU |
| **建議可用空間** | **60–70 GB** | **80–100 GB** | **30–40 GB** | **60–80 GB** |
| 獨立顯卡 | ❌ | ❌ | ❌ | ❌（雲端 GPU） |

**實機**：至少 2 台 Android，**不同 SoC 廠牌**（如一台 Snapdragon、一台 Dimensity）。Lead 與身體各持一台。

### 11.4 硬體風險緩解措施〔必做，Phase 0 前完成〕

專案曾因「Unity as Library 開發時硬碟空間不足」而推翻 Unity 方案。重新導入前必須落實：

| # | 措施 | 效益 |
|---|---|---|
| ① | **Android build 交給 GitHub Actions ＋ GameCI** | Lead 不需維護完整 Unity＋NDK 環境，省 40–60 GB |
| ② | **ComfyUI 走雲端 GPU（RunPod / Vast.ai 按小時計費）** | 臉省 40–80 GB 與一張顯卡；10 隻為一次性生產 |
| ③ | **不採用 Unity as a Library**，全 Unity 單一 App | 避免重蹈 v1 失敗路徑 |

**前置條件**：上述未落實前，不啟動 Phase 1。

---

## 12. 風險排序（v2.1）

| 等級 | 風險 | 變化 | 緩解 |
|---|---|---|---|
| 🔴 | **AI 對話成本與延遲** | 不變 | 配額分級、B12 固定招呼層、單一快速模型 |
| 🔴 | **宗教場所內容治理**（龍山寺） | ★**新增** | 見 §12.2 |
| 🔴 | **AI 生成 mesh 可用性**（綁骨失敗、清理工時不可預測） | ★新增 | Phase 0 POC 早期驗證；CC4 備援 |
| 🟠 | **臉的 Unity 能力缺口**（F4–F8） | ★新增 | §6.3【待確認】；必要時補技術美術 |
| 🟠 | **身體為關鍵路徑單點** | ★新增 | Lead 預留支援；Phase 0 POC 避免並行壓在身體 |
| 🟠 | **中階 Android 效能與發熱** | ★新增 | Phase 0 實測；面數與貼圖預算控管 |
| 🟠 | **羅盤在都市磁干擾下的方位穩定性**（萬華街廓密集，較天文館嚴峻） | ★新增 | 校正 UI；方位容忍設計 |
| 🟠 | **GPS 都市峽谷效應**（萬華） | ★升級 | 實地量測後調整 `summon_radius_m` |
| 🟠 | 10 隻角色風格一致性 | ★升級（3D 後更難） | 固定 prompt/LoRA/參考構圖；或改 CC4 |
| 🟡 | ~~AR / Unity 整合~~ | ★降級（不用 AR SDK） | — |
| 🟡 | ~~Geospatial API 成本~~ | ★移除 | — |
| 🟡 | ~~ARCore 認證裝置覆蓋率~~ | ★移除 | — |
| 🟡 | Unity 繁中 IME 輸入 | ★新增 | Phase 0 驗證 |
| 🟡 | 當日事件、任務邏輯、GPS spoofing | 不變 | 沿用 v1 |
| 🟢 | 隱私 | 略升（照片觸及後端） | 即用即丟約束 |

**耗電與發熱**：因不採用真 AR，較 v1 的 AR 方案大幅改善，v1 的 AC11 省電設計得以保留。

### 12.2 ⚠️ 龍山寺切片的內容治理專節〔本次新增〕

CONTEXT.md 原選定天文館作為垂直切片，理由是中性、科普、無信仰爭議。改為龍山寺後，以下項目**必須在 Phase 3（B4/B5 安全邊界）之前處理**：

| 項目 | 說明 | 負責 |
|---|---|---|
| **寺方接洽** | 事前溝通遊戲性質與現場行為規範，取得態度確認 | Lead |
| **人格卡禁忌主題擴充** | `taboo_topics` 需明確涵蓋：教義解釋、神祇位階、靈驗與否、占卜結果、宗教比較 | Lead ＋ 敘事 |
| **`not_this_character` 明確化** | 「不是廟方人員、不是任何神祇、不是宗教解說員」 | Lead ＋ 敘事 |
| **B4 輸入端過濾強化** | 玩家詢問求籤、運勢、宗教爭議時的攔截與轉向策略 | 腦袋 |
| **B5 邊界規則注入** | 明確禁止對信仰內容作教義性陳述或裁決（對應 §3 新增 Avoid 條目） | 腦袋 |
| **現場行為指引** | App 內提示玩家在廟埕的適當舉止 | 身體 |
| **人工預寫 fallback 文案** | 觸及禁忌時的角色口吻轉向台詞 | Lead ＋ 敘事 |

> **建議【待確認】**：若上述內容工作在 Phase 3 前無法完成，建議垂直切片先回到天文館，龍山寺留在首發十個地標內、於封測後上線。
> 技術驗證（GPS、羅盤、實地勘查）仍可先在龍山寺進行，不受影響。

---

## 13. 排程（7 階段垂直切片，3 天／Sprint）

### 13.1 排程原則

**改為垂直切片**：v1 的橫向堆疊要到後期才有端到端可玩版本，代表要盲飛多個階段才知道核心迴圈成不成立。改為 Phase 2 就打通最細的一條端到端（Walking Skeleton），讓延遲、效能、現場體感三大風險提早曝光。

**代價**：Phase 1–2 會有部分臨時程式碼（假資料、寫死地標、灰模角色）之後要重寫。此代價已接受。

**三條軸線**：腦袋（後端）與臉（資產）可完全平行；**身體是唯一匯流點且為單點**，排程圍繞「不讓身體空等」設計。

**節奏**：1 Sprint = 3 個工作天。7 個 Phase，共約 **27 個 Sprint ≈ 81 個工作天 ≈ 16 週**。

### 13.2 Phase 0：技術驗證（2 Sprint／6 天）

| POC | 負責 | 通過門檻 |
|---|---|---|
| **P0-A 資產可行性** | 臉 | 見下方檢查表 |
| **P0-B 3DoF 方位穩定性** | 身體 | **在龍山寺現場**原地旋轉一圈，角色方位偏移 < 15°；含校正流程 |
| **P0-C Unity 繁中 IME** | 身體 | Android 實機中文輸入法不跳版、不吃字、不遮擋輸入框 |
| **P0-D 對嘴＋效能** | 身體 ＋ 臉 | 播 TTS 音檔 → 身體動作 ＋ 下巴開合；中階實機連續 5 分鐘 ≥ 30fps |
| **P0-E 後端環境** | 腦袋 | docker-compose 起 Postgres＋Redis，`/players` `/sense` `/summon` 通 |

**P0-A 通過門檻檢查表**（決定 ComfyUI 是否續用）：

```
□ Mixamo 自動綁骨成功，四肢權重無明顯崩壞
□ 手肘、膝蓋彎曲時無麻花狀擠壓
□ 眼睛可分離為獨立材質
□ JawOpen shape key 可製作且不破面
□ 減面至 20k tris 後外觀無明顯劣化
□ 單隻清理工時 < 4 小時（可預測性驗證）
```

**未通過 → 切換 CC4 備援路線**，並回頭修訂 §7 與 F1/F2/F3 定義。

> ⚠️ P0-B/C/D 皆由身體負責，為 Phase 0 關鍵路徑。建議順序：**B → C → D**，避免並行。
> ⚠️ **Phase 0 結束後，Phase 1–7 需複審**——P0-A 結果會改變整條資產產線定義。

### 13.3 Phase 1：骨架（上）（3 Sprint／9 天）

| 角色 | 內容 |
|---|---|
| 🧠 腦袋 | B3 人格卡載入、B7 短期記憶、S6 身分後端、S2 在場驗證 |
| 🦾 身體 | Bootstrap 場景、TokenManager、ApiClient、S1 GPS、EncounterScene 雛形 |
| 🎭 臉 | **灰模交付**（Mixamo 免費角色 ＋ JawOpen，符合 §7.3 規格但美術不論）、F1 概念設計啟動 |
| 🧭 Lead | Repo 建置、CI 建置、API 契約凍結、**龍山寺實地勘查（全員同行）** |

> **灰模是關鍵**：讓身體與臉能在 Phase 1 就開始開發 F4/F5/F8，不必等正式角色。正式角色進來時直接替換。

### 13.4 Phase 2：骨架（下）★端到端打通（4 Sprint／12 天）

**目標**：玩家走到龍山寺 → 看到標記 → 點召喚 → 進疊層畫面 → 對一句話 → 聽到語音 → 角色嘴巴動。功能全部陽春版（無任務、無共鳴值、無長期記憶）。

| 角色 | 內容 |
|---|---|
| 🧠 腦袋 | B1 Gemini 串接、B10 TTS、dialogue API 可用版 |
| 🦾 身體 | **S7 tile renderer（提前）**、地圖標記、對話 UI prefab、**S14 3DoF 定向** |
| 🎭 臉 | F5 uLipSync、F8 Animator、F2 清理管線（正式角色 #1 起手） |
| 🧭 Lead | 端到端整合測試、**首次現場實測**、**建立 §11.2.1 API 契約閘門與 codegen 流程** |

### 13.5 Phase 3：判定邏輯與安全層（4 Sprint／12 天）

| 角色 | 內容 |
|---|---|
| 🧠 腦袋 | B4 安全邊界、B2 Prompt 組裝、B12 快速問候、S3 防作弊、S4 任務表、S5 共鳴值表 |
| 🦾 身體 | 召喚流程完整化、任務 UI 雛形、S12 拍照 |
| 🎭 臉 | F3 Mixamo 綁骨 ＋ shape key（正式角色 #1） |
| 🧭 Lead | **§12.2 龍山寺內容治理項目必須在此階段完成** |

### 13.6 Phase 4：對話品質與內容（4 Sprint／12 天）

| 角色 | 內容 |
|---|---|
| 🧠 腦袋 | B5 史實邊界、B6 長期記憶、B9 當日情境、B11 解鎖敘事、B13 地標辨識 |
| 🦾 身體 | 感應/召喚雙模式對話串接、配額與 fallback UI |
| 🎭 臉 | **F4 靈魂感 shader**、疊層視覺完成度、正式角色 #1 完成 |
| 🧭 Lead | 人格卡內容驗收、fallback 文案 |

### 13.7 Phase 5：UI 完整化（4 Sprint／12 天）

| 角色 | 內容 |
|---|---|
| 🧠 腦袋 | S10 當日情境排程、S11 訊號推播 |
| 🦾 身體 | S8 任務/Profile 場景、S9 引導提問、S13 分享 |
| 🎭 臉 | F6 Addressables 打包、F7 下載/快取 |
| 🧭 Lead | 對外 API 全串通驗收 |

### 13.8 Phase 6：封測準備（3 Sprint／9 天）

| 角色 | 內容 |
|---|---|
| 全員 | 端到端 QA、效能調校、實機多機種測試、**龍山寺現場多次實測** |
| 🧠 腦袋 | B8 記憶摘要 batch |
| 🧭 Lead | 封測招募、現場行為指引、事故應變流程 |

### 13.9 Phase 7：封測執行與收尾（3 Sprint／9 天）

封測執行、數據回收、`usage_tiers` 調校、Prompt 參數依實測調整。

> **角色 #2–#10 不在此排程內。** 依 CONTEXT.md 垂直切片原則，**通過封測驗證後才擴展**到首發十個地標。此為獨立的內容擴充階段，不佔用上述 27 個 Sprint。

---

## 14. 驗收標準異動

完整清單見 `city_soul_AR_acceptance_criteria.md`。v2.1 異動如下：

### 14.1 沿用不變

AC1（在場驗證與召喚）、AC2（感應驗證）、AC3（mock location 觀察期）、AC4（微任務生命週期）、AC5（共鳴值）、AC6（Token 邊界）、AC7（混合式聊天）、AC8（配額）、AC9（Fallback）、AC10（地圖標記狀態機）、AC13（開發環境）、AC14（地標視覺辨識）

### 14.2 需改寫

| AC | 異動 |
|---|---|
| **AC11** 距離偵測省電方案 | 平台改為 Unity `Input.location`；30 秒間隔與「不申請背景定位權限」不變 |
| **AC12** 相機疊圖畫面 | 全面改寫為「實境疊層畫面」，見下 |

### 14.3 AC12 改寫版（實境疊層畫面）

- **AC12.1** Given 剛進入實境疊層場景 When 檢視對話窗 Then 預設為**展開**
- **AC12.2** Given 點擊收合 When 對話窗收合 Then 仍可看到相機畫面與 3D 角色，且可再次展開
- **AC12.3** Given 在 50m 內越靠近召喚點 When 持續移動 Then 角色縮放**連續**漸變，非跳躍式
- **AC12.4** Given 原地水平旋轉手機 When 靈魂方位角超出視野 Then 角色停止繪製，螢幕邊緣顯示方向指示器
- **AC12.5** Given 轉回靈魂方位 When 方位角進入視野 Then 角色重新出現於對應螢幕水平位置
- **AC12.6** Given `headingAccuracy` 劣化超過門檻 When 進入或停留於實境疊層場景 Then 顯示羅盤校正提示

### 14.4 新增 AC

| AC | 內容 |
|---|---|
| **AC15.1** | 資產交付時 shape key 必須包含 `JawOpen`，大小寫完全相符 |
| **AC15.2** | 角色面數 ≤ 20,000 tris |
| **AC15.3** | Addressables bundle < 10 MB |
| **AC15.4** | App 首次啟動**未預載**全部角色；僅於進入某地標 150m 範圍時下載該角色 |
| **AC16.1** | 播放 TTS 音檔時 `JawOpen` 隨音量包絡連續變化 |
| **AC16.2** | 中階 Android 實機連續運行實境疊層 5 分鐘，平均幀率 ≥ 30fps，無明顯發熱降頻 |
| **AC17.1** | Android APK 僅含 ARM64，不含 ARMv7 |
| **AC17.2** | APK 下載大小 < 100 MB |
| **AC17.3** | App 權限清單**不含**背景定位權限，**不含** ARCore 相關宣告 |
| **AC18.1** | token 使用 Android Keystore，**不得**使用 `PlayerPrefs` |
| **AC19.1** | 玩家詢問求籤、運勢、教義解釋等禁忌主題時，回應不含教義性陳述或裁決，且以角色口吻自然轉向 |
| **AC19.2** | 人格卡 `not_this_character` 明確排除「廟方人員」「任何神祇」「宗教解說員」 |

---

## 15. Roadmap（MVP 之後）

### 15.1 本次改版新增

1. **真 AR 空間錨定**（ARCore Geospatial API / 平面偵測 ＋ 遮擋）
   - 前置：重估 Geospatial 計費、ARCore 認證裝置覆蓋率、耗電模型
   - 資產相容：靈魂感 shader 已設計為可調回實體感，**不需重做資產**
2. **完整 viseme 口型（A/I/U/E/O/N）**
   - 前置：臉部重拓樸或改用 CC4；**N（閉嘴型）不得省略**
3. **服裝/道具切換**（撐傘、黃昏換裝）
   - 3D 化後成本高於 2.5D 圖層切換；**須在角色設計階段預留擴充點**
4. **iOS 版本**
5. **角色 #2–#10 擴充**（封測通過後）

### 15.2 五項技術壁壘（沿用 v1）

城市人格引擎 ｜ 多 Agent 協作 ｜ 世界記憶（持久化，⚠️需重新界定邊界以免與「不含玩家輸入」原則衝突）｜ AI 導演 ｜ AR 情境感知

> 「多 Agent」「AI 導演」會大幅增加 LLM 呼叫次數，排入時需一併重估成本模型。

---

## 16. 尚未解決的缺口

| 缺口 | 影響 | 時機 |
|---|---|---|
| ★ 臉的 Unity 能力是否足以持有 F4–F8 | 分工與排程 | **Phase 0 前確認** |
| ★ ComfyUI 產線可行性未驗證 | 資產產線全局 | **Phase 0 P0-A** |
| ★ 龍山寺內容治理七項（§12.2） | 內容安全 | **Phase 3 前** |
| ★ 寺方接洽與態度確認 | 專案可行性 | Phase 1 |
| ★ 龍山寺 `bearing_deg` 與 GPS 半徑實測 | S14／S2 | Phase 1 實地勘查 |
| ★ 中階 Android 基準機型定義 | AC16.2 驗收 | Phase 0 前 |
| ★ Unity tile renderer 圖磚載入/快取策略 | S7 | Phase 2 |
| 主題色與風格一致性規範 | F1 | Phase 1 |
| `canned_greetings` 觸發語與預寫回覆 | 人格卡 | Phase 3 前 |
| 150m 發光、疊層漸顯美術參數 | F 模組 | Phase 4 前 |
| 429 配額用完的角色口吻 fallback 文案 | 腦袋內容 | Phase 4 前 |
| Prompt Top-K / 記憶輪數 | 腦袋調校 | 先用 K=3、短期記憶 6 輪，封測後調整 |
| `usage_tiers` 分級策略 | 產品決策 | Phase 7 |

---

## 17. 附錄

### 17.1 ADR

- `0001-gcp-vertex-ai-for-mvp-dialogue.md`：MVP 對話統一使用 Vertex AI 單一快速模型；失敗回退人工台詞〔既有〕
- `0002-unity-3dof-overlay-over-real-ar.md`〔待撰寫〕：客戶端改用 Unity；不採用 Godot 與真 AR
- `0003-ulipsync-over-tts-viseme-timeline.md`〔待撰寫〕：對嘴改為客戶端即時音訊分析
- `0004-longshan-temple-vertical-slice.md`〔待撰寫〕：垂直切片地標由天文館改為龍山寺及其內容治理配套

### 17.2 舊文件處置

| 文件 | 處置 | 說明 |
|---|---|---|
| `CONTEXT.md` | **需修訂** | ①「2.5D 靈魂角色」條目改寫為 3D；②「完整 3D 角色」自 Avoid 移除；③ 垂直切片由天文館改龍山寺；④ 新增宗教內容 Avoid 條目 |
| `SDD.md`(v1) | **廢止** | 由本文件取代 |
| `city_soul_AR_flutter_architecture.md` | **廢止** | 整份為 Flutter 專案架構 |
| `city_soul_AR_project_mainpoint.md` | **封存** | 歷史快照，僅供決策脈絡 |
| `city_soul_AR_project_dev.md` | **封存** | §2.1 第三方工具評估結論已被推翻 |
| `city_soul_AR_WBS_API.md` | **需修訂** | F 模組全部重寫、新增 S14、B10 縮減 |
| `city_soul_AR_schema_api_sprint.md` | **需修訂** | `spirits` 新增兩欄、`TTSResult` 移除 viseme、Sprint 表改為 7 階段 |
| `city_soul_AR_業務規則與API契約.md` | **小修** | 僅 dialogue 與 spirits 兩支回應欄位 |
| `city_soul_AR_acceptance_criteria.md` | **需修訂** | AC11/AC12 改寫、新增 AC15–AC19 |
| `city_soul_AR_landmark_recognition.md` | **保留** | 不受本次改版影響 |
| `city_soul_AR_document_index.md` | **需重寫** | 推翻對照表需納入本次全部變更 |
| `citysoul-sprint-map.html` | **廢止／重製** | Sprint 結構已全面改變 |

### 17.3 給接手開發者 / Claude Code 的起手建議

1. **先讀 §1 推翻對照表**。專案歷史上有三次技術棧變更，舊文件仍在，容易誤讀
2. 落實 §11.4 三項硬體緩解措施，再啟動 Phase 1
3. 依 §11.2 新開兩個 repo；Unity 專案第一天就設好 Force Text / Visible Meta Files
4. 執行 Phase 0 五個 POC；**P0-A 未通過即切換 CC4 備援**，不要硬做
5. 後端用本地 docker-compose 起環境，不急著佈建 GCP
6. 每完成一個功能，對照 §14 的 AC 編號逐條驗收
7. 文件矛盾時一律以本文件為準
