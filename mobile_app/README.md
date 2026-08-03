# citysoul_app（城市靈魂 AR — Flutter 前端）

Sprint1 骨架：S6 匿名玩家身分系統的 App 端（啟動畫面背景建立身分，無登入UI）。
分層結構依《城市靈魂AR遊戲 — Flutter前端架構》：`lib/core`、`lib/services`、`lib/features`。

## 前置需求

- Flutter SDK（`flutter --version` 確認可用）
- 後端已依專案根目錄 README 起好（`docker compose up -d` + `uv run uvicorn app.main:app --reload`）

## 開發

```bash
flutter pub get
flutter analyze
flutter test
```

## 對接本機後端

預設 API base URL 指向 Android emulator 存取本機的位址（`http://10.0.2.2:8000`），
可用 `--dart-define` 覆寫：

```bash
flutter run --dart-define=API_BASE_URL=http://localhost:8000
```

（用 `-d chrome`/`-d web-server` 這類瀏覽器 target 開發除錯時，後端需要
`APP_ENV=local` 才會開啟 CORS——見專案根目錄 `app/main.py`；正式的 Android/iOS
client 不受此限制，CORS 只是瀏覽器限定的機制。）

## 檔案對照 WBS 工作包

| 檔案 | 對應工作包 | 說明 |
|---|---|---|
| `lib/services/secure_store.dart` | S6 | 裝置安全儲存區抽象（seam），測試用記憶體版 fake |
| `lib/services/player_api.dart` | S6 | `POST /api/v1/players` 呼叫的抽象介面（seam）+ HTTP 實作 |
| `lib/services/identity_bootstrap_service.dart` | S6 | 產生/讀取 `device_id`、呼叫後端、儲存 `session_token` 的完整流程 |
| `lib/services/api_client.dart` | — | Dio 實例，攔截器自動帶 `session_token` |
| `lib/features/startup/startup_screen.dart` | S6 | 啟動畫面，背景執行身分建立，無登入UI |
| `lib/features/home/home_screen.dart` | — | 佔位首頁，真正的探索地圖畫面（S7）留待後續 ticket |

## 下一步（Ticket #6：GPS 前景定位服務 S1）

在此骨架基礎上新增 `LocationService`，見專案根目錄 `docs/SDD.md` 第11節。
