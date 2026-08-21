# 城市靈魂 AR — Backend（Sprint 1 骨架）

這是照《WBS-API》Sprint 1 範圍搭的最小可跑骨架：

- S6 匿名玩家身分系統
- B3 人格卡載入與版本管理
- B7 短期記憶（Redis）連線層
- 附帶：`spirits` 表 + 龍山寺垂直切片 seed data（Sprint2 的 S2 在場驗證會用到）

**沒做的東西是刻意的**：S1 GPS 擷取其實是手機端（Flutter）的事，後端這邊
沒有東西可以刻；quest_progress／resonance／daily_event_cache 排在 Sprint3
之後才建，這裡先不生。

---

## 0. 前置需求

- [uv](https://docs.astral.sh/uv/)（管理虛擬環境與套件，全專案統一用這個，方便之後遷移環境）
- Docker + Docker Compose（本機跑 Postgres + Redis 用，之後才接 GCP）

## 1. 起本機資料庫/Redis

```bash
docker compose up -d --build
```

會起兩個容器：

- `db`：Postgres 16 + pgvector + PostGIS（跟 Cloud SQL 上會裝的 extension 一致，本機先驗證行為）
- `redis`：Redis 7

### 為什麼要 `--build`

`db` 不是現成映像檔，是 `Dockerfile.postgres` 建出來的。我們同時需要 `vector`
（B6 記憶檢索）和 `postgis`（區域圍欄），而沒有官方映像檔兩個都有——
`pgvector/pgvector:pg16` 只有 pgvector，直接 `CREATE EXTENSION postgis` 會噴
`extension "postgis" is not available`。所以在它上面疊裝 PostGIS。

**第一次啟動、或改過 `Dockerfile.postgres` 之後，一定要帶 `--build`**，
否則 compose 會沿用舊的映像檔，PostGIS 不會出現。之後日常啟動 `docker compose up -d`
就夠了。

Cloud SQL for PostgreSQL 16 兩個 extension 都原生支援，不需要對應的自建映像檔——
那邊由 migration `0006` 的 `CREATE EXTENSION` 負責啟用。

## 2. 裝 Python 套件

```bash
uv sync
```

這會照 `.python-version`（3.12）自動抓對應版本的 Python、在 `.venv` 建虛擬環境，
並依 `pyproject.toml` + `uv.lock` 鎖定的版本安裝套件（含開發用的 `pytest`/`httpx`）。
不需要手動 `pip install`，也不需要先手動建 venv。

之後所有指令都用 `uv run python -m <module>` 執行（例如 `uv run python -m pytest`）。

> ⚠️ Windows 上不要用 `uv run pytest` / `uv run uvicorn` 這種直接叫執行檔的形式，
> 會噴 `uv trampoline failed to canonicalize script path`。走 `python -m` 沒有這個問題。

會自動使用專案的虛擬環境，不需要手動 `activate`。

## 3. 設定環境變數

```bash
cp .env.example .env
```

本機預設值已經跟 docker-compose.yml 對好，不用改就能跑。

### requirements.txt 是產生出來的，不要手改

相依的真相來源是 `pyproject.toml` ＋ `uv.lock`。`requirements.txt` 只是給
不用 uv 的環境（Docker、Cloud Run、CI）的匯出檔，改了它不會影響任何人的安裝。

改過相依之後重新產生：

```bash
uv export --no-hashes --no-dev --no-emit-project --no-annotate \
  --format requirements-txt -o requirements.txt
```

要包含測試相依就拿掉 `--no-dev`。

### ⚠️ GCP 憑證：不要產生 service account 金鑰

存取 Vertex AI 走 **Application Default Credentials**。本機跑一次：

```bash
gcloud auth application-default login
gcloud config set project citysoul
```

**不要**去 Console 下載 service account 金鑰 JSON，也不要把金鑰放進 Secret
Manager——那只是把金鑰換個地方放，它仍然長期有效、仍然要輪替、仍然可能被寫進
log。ADC 拿到的是短期 token，沒有東西需要輪替，也沒有東西可以外洩。

`.env` 因此**沒有任何 GCP 憑證欄位**，只有「呼叫哪個模型」。

> 完整理由見 `docs/adr/0003-adc-over-service-account-keys.md`（本機檔案，
> `docs/` 不進版控）。

如果金鑰不小心進了 git：**刪 commit 沒有用，必須輪替那把金鑰。**

## 4. 初始化資料庫

```bash
uv run python -m scripts.init_db
```

這支腳本只做兩件事：

1. `alembic upgrade head` —— 建 `brain` schema、`vector` extension、所有的表
2. 塞入龍山寺垂直切片的 seed data（一座城市、一個地標、一個角色、一版**未審核**的人格草稿）

### schema 的唯一真相是 `migrations/versions/`

**不要用 `Base.metadata.create_all()`，也不要在別的地方寫 `ALTER TABLE`。**
以前 `init_db.py` 有一份手寫的冪等 ALTER 清單，當時就註明「只會愈長愈醜」——
它長了一行就被 Alembic 取代掉了。

`tests/conftest.py` 也是跑 migration 而不是 `create_all`。這是刻意的：用
`create_all` 的話，測試永遠在驗「models 說 schema 該長怎樣」，而正式環境拿到
的是 migration 的產物，**migration 寫錯了測試照樣全綠**。

### 已經有資料的舊資料庫怎麼接上

不需要砍掉重建。先宣告目前狀態，再往前跑：

```bash
uv run python -m alembic stamp 0001   # 0001 等同舊的 create_all，不改變任何東西
uv run python -m alembic upgrade head
```

### 新增一支 migration

```bash
# 1. 先改 app/**/models.py
# 2. 產生草稿（revision id 自己指定，照順序編號，不要用隨機 hash）
uv run python -m alembic revision --autogenerate --rev-id 0006 -m "說明"
# 3. **打開產生的檔案改過再用**（見下）
# 4. 套用並確認 models 與資料庫一致
uv run python -m alembic upgrade head
uv run python -m alembic check     # 要看到 "No new upgrade operations detected"
```

> ⚠️ **autogenerate 的產出一定要人看過。** 它有兩個已知的盲點：
>
> - **看不出改名。** 把 `place_id` 改成 `spirit_id`，它會產生「drop 舊欄位 +
>   add 新欄位」——資料就沒了。改名要自己寫成 `op.alter_column(...,
>   new_column_name=...)`。
> - **不會搬資料。** 換型別、拆表、補預設值，都要自己寫。
>
> 另外 `alembic.ini` **必須維持純 ASCII**：configparser 用系統編碼讀它，在
> zh-TW Windows 上是 cp950，寫中文註解會在 alembic 啟動前就 `UnicodeDecodeError`。
> 要寫說明就寫在 `migrations/env.py`（那個檔案是 UTF-8 讀的）。

### 退回上一版

```bash
uv run python -m alembic downgrade -1
```

每一支 migration 都驗過可以來回。但 `0005` 的 downgrade 會把 `persona_cards`
的**表**建回來、**資料不會**回來——人格內容的搬移是單向的。

## 5. 啟動服務

```bash
uv run python -m uvicorn app.main:app --reload --reload-dir app
```

打開 http://localhost:8000/docs 會看到自動產生的 API 文件。

## 5.5 開發測試主控台

<http://localhost:8000/dev/console>（線上：`/dev/console`）

一個瀏覽器頁面，用來手動走完「選身分 → 選地標 → 召喚 → 對話」，不需要 Unity
client，也不需要真的站在龍山寺前面。右邊兩個下拉選單分別選登入身分與地標，
選了地標會自動把它自己的座標填進去——想測在場驗證失敗，把緯度改掉再召喚。

底下那排按鈕打的是各支查詢端點（當日情境／每日任務／共鳴值／個人檔案／記憶
摘要），右側永遠顯示最後一次呼叫的原始回應，含 4xx。

### 它不是產品的一部分

- 掛在 `/dev`，整組 `include_in_schema=False`，**不會進 `contracts/openapi.json`**。
- 不提供正式 API 以外的權限：切換身分走的是真正的 `POST /api/v1/players`，
  主控台自己不發 token。
- 不繞過驗證：召喚一樣打 `/api/v1/summon`，一樣要通過在場驗證。

`/dev/players` 與 `/dev/spirits` 只是「測試時人要看得到有什麼」。正式 API 沒有
列表端點是因為 App 以裝置為中心、客戶端從地圖點選，不是因為功能缺了一塊——
不要因為主控台有就把它們搬進 `/api/v1`。

用 `DEV_CONSOLE_ENABLED=false` 關掉。預設開著（本機開發需要），**對外的正式
部署要明確關掉**。

### 對話回 `fallback` 是正常的

回應的 `source` 欄位有三種值，主控台會直接標在氣泡上：

| source | 意思 |
|---|---|
| `canned` | B12 預寫招呼命中，完全沒有呼叫模型 |
| `generated` | 真的走了 Gemini |
| `fallback` | 沒有生效人格卡，或模型失敗 |

seed 進去的龍山寺人格是**未審核草稿**（`active=False`），所以在審核通過之前，
每一句都會走 `fallback`。那是內容治理的閘門在生效，不是 B1 壞掉。要驗真正的
Gemini 生成，得先有一版通過審核、`active=True` 的人格卡。

## 6. 手動測試

```bash
# health check
curl http://localhost:8000/health

# 建立一個匿名玩家（同一個 device_id 重複呼叫會回傳同一個 player，不會重複建立）
curl -X POST http://localhost:8000/api/v1/players \
  -H "Content-Type: application/json" \
  -d '{"device_id": "test-device-001"}'

# 查詢龍山寺這個 spirit
curl http://localhost:8000/api/v1/spirits/longshan_temple
```

## 7. 跑自動化測試

```bash
uv run python -m pytest
```

測試需要本機 Postgres/Redis 已啟動（見第1步）。

### CI

`.github/workflows/test.yml` 在 push 到 `main` 與對 `main` 開 PR 時跑，
四道關卡：

| 步驟 | 擋住什麼 |
|---|---|
| `alembic upgrade head` | 全新空資料庫建不起來（開發機的資料庫是逐步演化來的，證明不了這件事） |
| `downgrade base` → `upgrade head` | migration 不可逆 |
| `alembic check` | models 加了欄位但忘記寫 migration |
| `pytest -q` | 其餘全部 |

`alembic check` 那道是重點：少了它，有人在 `models.py` 加欄位卻沒寫 migration
時，測試仍然全綠（conftest 跑的是 migration，那個欄位根本不存在，而剛好沒有
測試碰到它），要到部署才炸。

CI **不需要任何 GCP 憑證**（ADR-0003）。Gemini 的真實呼叫測試在沒有憑證時
自動 skip。三把 token 金鑰在 CI 用假值，但**必須兩兩不同**，有測試在守。

另外有一個獨立的 `contract-gate` job，只在 PR 上跑，見下一節。

---

## API 契約：`contracts/openapi.json`

**這份檔案是後端與 `citysoul-client` 之間的唯一真相。** `citysoul-client` 不放
副本，只放一個記錄來源版本的 lock 檔。

改過任何 Pydantic 模型或路由的 `responses=` 之後，重新產生並一起 commit：

```bash
uv run python -m scripts.generate_openapi_contract
```

忘記重跑的話，本機 `pytest` 就會紅（`tests/test_api_contract.py` 的「快照未過期」
那條），不必推上去等 CI。

### 為什麼契約要進 git

後端與客戶端分屬兩個 repo 之後多了一個失敗模式：**後端改了 API、客戶端不知道。**
以 3 天一個 Sprint 的節奏，這會頻繁發生。快照進 git 之後，任何一次契約變動都會
出現在 code review 的 diff 裡，而不是隱形發生。

`contract-gate` job 再往前一步：PR 上用 `oasdiff` 比對新舊契約，破壞性變更
（刪欄位／改型別／改必填）直接讓 build 失敗。

### 版本相容原則：只加不減

- 後端**可以**新增欄位；**不得**刪除或改名既有欄位。
- 客戶端忽略自己讀不懂的欄位。

真的需要破壞性變更時，貼 PR 標籤 `breaking-change` 可以跳過閘門，但**必須同時
提交 v2 路由**（SDD v2.1 §11.2.1）。那是流程規範，由 review 把關，不由工具強制——
貼標籤是一個顯式的意圖表態，讓它不會在趕 Sprint 時被順手放行。

### OpenAPI 3.1.0，不要降級到 3.0

已評估過，結論是不可行且有害：FastAPI 的版本字串只是原封不動塞進輸出，**改它不會
改 schema 內容**。Pydantic v2 產出的 nullable 是 `anyOf: [{$ref}, {type: null}]`，
而 `type: null` 在 3.0 不合法。宣告 3.0 只會產出一份自稱 3.0、內容卻是 3.1 的無效
文件，工具照 3.0 規則解析可能**靜默產出錯誤的 DTO**——比誠實的 3.1 更糟，因為 3.1
至少是有效文件，工具不支援時會明確報錯。

若日後 codegen 真的需要 3.0，正解是後處理走真正的 3.1→3.0 轉換器，不是改宣告字串，
更不是為了工具去扭曲 Pydantic 模型。

---

## 部署到 Cloud Run

映像檔由根目錄的 `Dockerfile` 建出來。它跟 `Dockerfile.postgres` 是兩件不相干
的事：後者是本機開發用的資料庫映像檔（pgvector + PostGIS），只給
docker-compose 用，永遠不上雲。

`.dockerignore` 與 `.gcloudignore` 也是兩份各自維護的清單：前者管本機
`docker build` 的 build context，後者管 `gcloud ... --source` 上傳到雲端建置
環境的內容。內容高度重疊，但不要假設它們會一致。

### 設定從哪裡來

`app/core/config.py` 的 `Settings` 全部讀環境變數，本機讀 `.env`，正式環境由
Cloud Run 的 `--set-env-vars` 與 `--set-secrets` 提供。**程式碼裡沒有任何一處
會去呼叫 Secret Manager 的 API**——Cloud Run 直接把 secret 版本掛成環境變數，
少一層執行期相依，也少一份要維護的 IAM。

秘密的部分放 Secret Manager（`DATABASE_URL`、三把 token 金鑰），非秘密的
（`GCP_PROJECT_ID`、`GEMINI_MODEL` 等）走 `--set-env-vars`。憑證本身仍然不進
Secret Manager，那是 ADR-0003 的事，跟這裡的設定值是兩回事。

### 0. 一次性的資源

以下在 `citysoul` 專案上已經建好，**不需要重跑**，列在這裡是為了知道服務靠什麼
活著（換專案重建時照這個順序）：

```bash
gcloud services enable run.googleapis.com sqladmin.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

gcloud artifacts repositories create citysoul \
  --repository-format=docker --location=asia-east1

gcloud sql instances create citysoul --region=asia-east1 \
  --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro \
  --storage-size=10GB --storage-type=HDD --availability-type=zonal --no-backup
gcloud sql databases create citysoul --instance=citysoul

gcloud iam service-accounts create citysoul-run
```

`citysoul-run` 這個 service account 需要四個專案層級角色
（`cloudsql.client`、`secretmanager.secretAccessor`、`aiplatform.user`、
`storage.objectAdmin`），外加**對自己**的 `iam.serviceAccountTokenCreator`——
最後這個是 TTS 簽章 URL 用的，ADC 沒有金鑰檔，簽章要走 IAM SignBlob。

Secret Manager 裡有五個 secret：`citysoul-database-url`、三把 token 金鑰，以及
`google-weather-api-key`。三把 token 金鑰**各自獨立產生**，不是產一次複製三份
——兩把相同就等於兩種 token 可以互相冒充（SDD 第6節）。

> ⚠️ **`google-weather-api-key` 是 2026-08-21 新增的**（backend#75「當地氛圍」）。
> `scripts/gcp/service.yaml` 會掛載它，**secret 不存在的話整個 revision 起不來**
> ——不是天氣壞掉而已，是服務部署失敗。先建再部署：
>
> ```bash
> gcloud services enable weather.googleapis.com --project=citysoul
> printf '%s' "<API_KEY>" | gcloud secrets create google-weather-api-key \
>   --project=citysoul --data-file=-
> ```
>
> 金鑰請在 Google Cloud Console 限制成只能呼叫 Weather API。它只待在後端，
> 客戶端不持有（SDD §20.5.2）。

> ⚠️ 資料庫密碼只用英數字元。它要塞進 `DATABASE_URL` 的 userinfo 欄位，
> 出現 `/ + = @` 就必須 percent-encoding，而那正是「本機測得過、雲端連不上」
> 最常見的來源。

### 1. 建置並推上 Artifact Registry

```bash
docker build -t asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
gcloud auth configure-docker asia-east1-docker.pkg.dev   # 第一次才需要
docker push asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest
```

本機沒有 Docker 的話改用 `gcloud builds submit --tag <同一個標籤>`，在雲端建置。

### 2. 跑 migration

```bash
scripts/gcp/migrate-job.sh          # 加 DRY_RUN=1 可以先看要跑什麼
```

這支腳本建立（或更新）並執行 Cloud Run Job `citysoul-migrate`，用**同一個
映像檔**跑 `alembic upgrade head`。

**migration 不綁在服務啟動流程裡**，是刻意的：Cloud Run 會同時起多個實例，
綁在啟動流程等於讓多個實例同時對同一個資料庫跑 DDL；而且 revision 一失敗，
整個服務就起不來——把「schema 有問題」升級成「服務全掛」。

> ⚠️ `DATABASE_URL` 裡的帳號要有 `cloudsqlsuperuser`。migration `0001`/`0006`
> 會 `CREATE EXTENSION vector` / `postgis`，一般使用者做不到這件事。目前用的是
> 內建的 `postgres` 帳號，它有；後來自己建的一般使用者沒有。

### 2.5 塞垂直切片 seed data（只有全新的資料庫需要）

migration 只建表，不放資料。沒有 seed，`GET /spirits/longshan_temple` 會 404——
`spirits` 表是空的。

```bash
gcloud run jobs deploy citysoul-seed \
  --region=asia-east1 \
  --image=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest \
  --service-account=citysoul-run@citysoul.iam.gserviceaccount.com \
  --set-cloudsql-instances=citysoul:asia-east1:citysoul \
  --set-env-vars=APP_ENV=production,REDIS_URL=redis://127.0.0.1:6379/0,GCP_PROJECT_ID=citysoul \
  --set-secrets=DATABASE_URL=citysoul-database-url:latest,SESSION_TOKEN_SECRET=session-token-secret:latest,ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest,SENSE_TOKEN_SECRET=sense-token-secret:latest \
  --max-retries=0 --command=python --args=-m,scripts.init_db
gcloud run jobs execute citysoul-seed --region=asia-east1 --wait
```

`seed_vertical_slice()` 每一段都先查再寫，重跑不會產生重複資料。

### 3. 部署服務

服務有兩個容器（app + Redis sidecar），所以走 YAML 而不是一長串旗標：

```bash
gcloud run services replace scripts/gcp/service.yaml --region=asia-east1
```

> ⚠️ **Redis 是 sidecar，不是 Memorystore。** 這省下每月約 US$40，代價是資料
> 不持久、不跨實例共用——所以 `maxScale` 鎖在 1。那不是效能設定，是正確性的
> 前提：第二個實例會有自己的一份 Redis，同一個玩家的對話歷史會隨路由結果而不同。
> 真實流量前必須換成 Memorystore + Direct VPC egress。理由與細節寫在
> `scripts/gcp/service.yaml` 開頭。

單一容器版本（沒有 Redis，對話端點會 500）的等價指令：

```bash
gcloud run deploy citysoul-backend \
  --image=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest \
  --region=asia-east1 \
  --service-account=citysoul-run@citysoul.iam.gserviceaccount.com \
  --set-cloudsql-instances=citysoul:asia-east1:citysoul \
  --set-env-vars=APP_ENV=production,GCP_PROJECT_ID=citysoul,GCP_LOCATION=global \
  --set-secrets=DATABASE_URL=citysoul-database-url:latest,SESSION_TOKEN_SECRET=session-token-secret:latest,ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest,SENSE_TOKEN_SECRET=sense-token-secret:latest \
  --set-env-vars=REDIS_URL=redis://127.0.0.1:6379/0 \
  --allow-unauthenticated --max-instances=2 --memory=512Mi
```

目前的服務網址：<https://citysoul-backend-gkoatlfdwa-de.a.run.app>

Cloud Run 給同一個服務兩個網址，兩個都通、都指向同一個修訂版：
`...gkoatlfdwa-de.a.run.app`（`gcloud run services describe` 回報的那個，也是
`citysoul-client` 的 `env.json` 用的）與
`...1096472835040.asia-east1.run.app`（含專案編號的舊格式，`services replace`
執行完會印這個）。**以前者為準**——兩邊混用時，看 log 或比對客戶端設定會多花
一次「這是不是同一個服務」的確認。

> ⚠️ `--allow-unauthenticated` 表示這個網址**任何人都打得到**。玩家用的 API
> 本來就要公開，但在還沒有正式流量的階段，它也是任何人都能建匿名玩家、消耗
> Gemini 額度的入口。`--max-instances=2` 是這個階段的成本上限，不是效能設定。
>
> `REDIS_URL` 目前是個連不到的佔位值（見下方「還沒做完的部分」）。`redis.from_url`
> 是延遲連線，所以服務起得來、多數端點正常，只有會碰到對話 session 的路徑會炸。

Cloud SQL 的 `DATABASE_URL` 走 unix socket，不是 IP：

```text
postgresql+psycopg://<user>:<pass>@/<db>?host=/cloudsql/citysoul:asia-east1:citysoul
```

### 3.5 接上當日情境的每日排程

```bash
scripts/gcp/daily-event-job.sh      # 加 DRY_RUN=1 可以先看要跑什麼
```

建立（或更新）Cloud Run Job `citysoul-daily-event`，並把它接上 Cloud Scheduler，
每天 05:00（Asia/Taipei）跑一次 B9 當日情境批次。**時區一定要明確指定**——不指定
的話 Scheduler 走 UTC，會變成台灣下午 1 點，那時候玩家早就看過今天的內容了。

觸發用的服務帳號需要對這個 Job 有 `roles/run.invoker`：

```bash
gcloud run jobs add-iam-policy-binding citysoul-daily-event   --project=citysoul --region=asia-east1   --member=serviceAccount:citysoul-run@citysoul.iam.gserviceaccount.com   --role=roles/run.invoker
```

少了這一行，排程會準時觸發並安靜地拿到 403：Scheduler 的執行紀錄看得到，Job
的執行清單則什麼都不會多出來。

> ⚠️ **接上排程不等於內容就有了。** 輪播池是空的時候，批次跑完每隻靈魂仍然回
> `is_fallback: true`。順序是：先接排程 → 再填輪播池 → 才看得到非 fallback 的內容。

### 4. 驗一下

```bash
U=https://citysoul-backend-gkoatlfdwa-de.a.run.app
curl -s $U/health
curl -s $U/api/v1/spirits
curl -s $U/api/v1/spirits/longshan_temple
curl -s -X POST $U/api/v1/players -H "Content-Type: application/json" \
  -d '{"device_id":"smoke-001"}'
```

四個都通表示映像檔、Cloud SQL 連線、Secret Manager 掛載、seed data 這條鏈是通的。
`GET /api/v1/spirits` 要回九筆——地圖上的召喚點就是這份清單，回一筆代表只有
垂直切片的龍山寺進了資料庫，其餘八個沒有匯入（`scripts/import_spirits.py`）。

⚠️ **`Done` 不等於上線。** `gcloud run services replace` 比對的是 yaml 內容，
`scripts/gcp/service.yaml` 裡的映像檔 digest 沒換的話，它會判定「設定沒有變更」、
不建新修訂版，**然後照樣印出 `Done`**。驗收時看 revision 編號有沒有跳：

```bash
gcloud run revisions list --service=citysoul-backend --region=asia-east1 --limit=3
```

### 還沒做完的部分

上面幾步足以讓服務跑起來並連上資料庫，但以下還是缺的，不要以為部署完就等於
上線：

- **Redis**：目前是同一個實例裡的 sidecar（見上），不是 Memorystore。資料不
  持久、不跨實例，`maxScale` 因此鎖在 1。要放開擴縮就得接 Memorystore +
  Direct VPC egress。
- **TTS 簽章 URL**：`generate_signed_url()` 在 ADC（無金鑰檔）下需要 IAM
  SignBlob，service account 要對自己有 `roles/iam.serviceAccountTokenCreator`。
  沒設定就是簽章失敗，而不是降級成沒有語音。
- **推播**：`app/modules/body/push.py` 目前只有抽象介面，沒有真的送出實作。
- **Cloud Scheduler**：B8 夜間記憶批次與 S10 每日事件還沒有觸發來源。
- **CD**：`.github/workflows/` 只有 `test.yml`，部署還是手動跑上面的指令。
- **`/dev/console` 是開著的**（`DEV_CONSOLE_ENABLED=true`）。現階段刻意如此——
  沒有 Unity client 就沒有別的方法驗端到端。上真實玩家前要關掉。

---

## 遇到 `password authentication failed` 怎麼辦

照下面順序做，通常是這兩個原因之一：

**1. Postgres volume 是舊資料，帳密沒套用新設定**
Postgres 官方映像檔只有在 volume 第一次初始化時才會套用 `POSTGRES_USER`/`POSTGRES_PASSWORD`，
volume 裡如果已經跑過一次舊設定，改 docker-compose 也不會生效，必須清掉重來：

```bash
docker compose down -v   # -v 會連 volume 一起刪，本機開發資料沒差
docker compose up -d
```

**2. `.env` 跟 docker-compose.yml 的帳密沒有逐字對上**
不要手key，直接重新複製一次：

```bash
cp .env.example .env
```

確認 `.env` 裡 `DATABASE_URL` 的 user/password 是 `city_soul` / `city_soul_dev`（注意底線），
跟 `docker-compose.yml` 裡 `POSTGRES_USER`/`POSTGRES_PASSWORD` 逐字一致。

**3. `psycopg[binary]` 版本/平台 wheel 對不上**
這個專案固定用 `psycopg[binary]`（psycopg3），`.python-version` 鎖定 3.12 是為了確保
`uv sync` 能抓到現成的 wheel（3.14 目前還沒有對應 wheel）。如果懷疑環境跑歪了，
直接重建虛擬環境比手動修補快：

```bash
rm -rf .venv
uv sync
```

同時確認 `.env` 的 `DATABASE_URL` 開頭是 `postgresql+psycopg://`（不是 `+psycopg2://`）。

三步都做完後重跑：

```bash
uv run python -m scripts.init_db
```

---

## 檔案對照 WBS 工作包

| 檔案                                                        | 對應工作包                          | 說明                                                                                                  |
| ----------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `app/modules/body/models.py` `router.py` `schemas.py` | S6                                  | 匿名玩家身分：以 device_id 為唯一鍵，重複呼叫不重建                                                   |
| `app/modules/brain/models.py` `loader.py`               | B3                                  | 人格三層（city／landmark／character）放獨立`brain` schema；`active` 只能由人工審核流程 flip，程式碼裡沒有寫任何自動通過的路徑 |
| `app/core/redis_client.py`                                | B7                                  | 對話 session 的 key 命名慣例先定下來，Sprint3 的 Prompt 組裝引擎（B2）會直接呼叫這裡的`get_session` |
| `app/db/seed.py`                                          | 對應 CONTEXT.md「封閉測試垂直切片」 | 刻意只 seed 龍山寺一筆，不要因為手滑就把十個首發靈魂建進來                                            |

## 下一步（Sprint 2）

1. `spirits` 表已經有了，接著做 S2 在場驗證：收玩家 GPS，算跟 `summon_radius_m` 的距離
2. B1 Vertex AI Gemini 串接：需要 GCP 專案 + 服務帳號金鑰，先去 GCP Console 開好專案
3. B6：在 `brain` schema 底下加 `memory_embeddings` 表（`vector` extension 這次已經先啟用了，直接建表即可）
4. 龍山寺的人格草稿目前 `active=False`，記得找人工審核流程之前先不要手動改成 True——這條是 CONTEXT.md「人格卡」定義的邊界，不是技術限制。要在手機上 demo 請跑 `uv run python -m scripts.seed_spike`，那支腳本另建一版明確標記未審核的 version 2，主 seed 不動
