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

**`--build` 是必要的**（issue #46）：`db` 服務不是直接拉官方映像檔，而是
`Dockerfile.postgres`（`FROM pgvector/pgvector:pg16` 再疊裝 `postgresql-16-postgis-3`）。
官方 `pgvector/pgvector` 映像檔只有 pgvector，沒有 PostGIS——而 `brain.districts`
的地理圍欄（`ST_Contains`）需要 PostGIS，B6 長期記憶檢索需要 pgvector，兩者
都要、沒有現成映像檔兩個都有，所以自建一層。第一次啟動、或改了
`Dockerfile.postgres` 之後都要帶 `--build`，否則會用到舊的映像檔快取。

會起兩個容器：

- `db`：Postgres 16 ＋ pgvector ＋ PostGIS extension（Cloud SQL for PostgreSQL 16
  兩者皆支援：PostGIS 3.5.2、pgvector 0.8.0，[官方文件](https://docs.cloud.google.com/sql/docs/postgres/extensions)）
- `redis`：Redis 7

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
