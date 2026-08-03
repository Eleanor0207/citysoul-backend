# 城市靈魂 AR — Backend（Sprint 1 骨架）

這是照《WBS-API》Sprint 1 範圍搭的最小可跑骨架：

- S6 匿名玩家身分系統
- B3 人格卡載入與版本管理
- B7 短期記憶（Redis）連線層
- 附帶：`spirits` 表 + 天文館垂直切片 seed data（Sprint2 的 S2 在場驗證會用到）

**沒做的東西是刻意的**：S1 GPS 擷取其實是手機端（Flutter）的事，後端這邊
沒有東西可以刻；quest_progress／resonance／daily_event_cache 排在 Sprint3
之後才建，這裡先不生。

---

## 0. 前置需求

- Python 3.11+
- Docker + Docker Compose（本機跑 Postgres + Redis 用，之後才接 GCP）

## 1. 起本機資料庫/Redis

```bash
docker compose up -d
```

會起兩個容器：

- `db`：Postgres 16 + pgvector extension（跟 Cloud SQL 上會裝的 extension 一致，本機先驗證行為）
- `redis`：Redis 7

## 2. 裝 Python 套件

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. 設定環境變數

```bash
cp .env.example .env
```

本機預設值已經跟 docker-compose.yml 對好，不用改就能跑。

## 4. 初始化資料庫

```bash
python -m scripts.init_db
```

這支腳本會：

1. 建立 `brain` schema（放腦袋模組的表，跟身體的表隔開，對應 WBS-API 決策4）
2. 啟用 `vector` extension（先備好給 Sprint2 的 pgvector 表用）
3. 建立 `players`／`spirits`／`brain.persona_cards` 三張表
4. 塞入天文館垂直切片的 seed data（`spirits` 一筆 + 一張**未審核**的人格卡草稿）

## 5. 啟動服務

```bash
uvicorn app.main:app --reload
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

# 查詢天文館這個 spirit
curl http://localhost:8000/api/v1/spirits/taipei_planetarium
```

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

**3. Windows + Python 3.13：`psycopg2-binary` 常常沒有現成 wheel**
這個專案已經改用 `psycopg[binary]`（psycopg3），在 Windows 新版 Python 上相容性好很多。
如果你是照舊版指示裝了 `psycopg2-binary`，先解安裝乾淨再重裝：

```bash
pip uninstall psycopg2-binary psycopg2 -y
pip install -r requirements.txt
```

同時確認 `.env` 的 `DATABASE_URL` 開頭是 `postgresql+psycopg://`（不是 `+psycopg2://`），
兩者不能混用——scheme 寫 `+psycopg2` 但環境裡裝的是 psycopg3，就會出現連線層級的怪錯誤
（包括看起來像帳密錯誤，實際上是驅動兜不起來）。

三步都做完後重跑：

```bash
python -m scripts.init_db
```

---

## 檔案對照 WBS 工作包

| 檔案                                                        | 對應工作包                          | 說明                                                                                                  |
| ----------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `app/modules/body/models.py` `router.py` `schemas.py` | S6                                  | 匿名玩家身分：以 device_id 為唯一鍵，重複呼叫不重建                                                   |
| `app/modules/brain/models.py` `loader.py`               | B3                                  | 人格卡放獨立`brain` schema；`is_active` 只能由人工審核流程 flip，程式碼裡沒有寫任何自動通過的路徑 |
| `app/core/redis_client.py`                                | B7                                  | 對話 session 的 key 命名慣例先定下來，Sprint3 的 Prompt 組裝引擎（B2）會直接呼叫這裡的`get_session` |
| `app/db/seed.py`                                          | 對應 CONTEXT.md「封閉測試垂直切片」 | 刻意只 seed 天文館一筆，不要因為手滑就把十個首發靈魂建進來                                            |

## 下一步（Sprint 2）

1. `spirits` 表已經有了，接著做 S2 在場驗證：收玩家 GPS，算跟 `summon_radius_m` 的距離
2. B1 Vertex AI Gemini 串接：需要 GCP 專案 + 服務帳號金鑰，先去 GCP Console 開好專案
3. B6：在 `brain` schema 底下加 `memory_embeddings` 表（`vector` extension 這次已經先啟用了，直接建表即可）
4. 天文館的人格卡草稿目前 `is_active=False`，記得找人工審核流程之前先不要手動改成 True——這條是 CONTEXT.md「人格卡」定義的邊界，不是技術限制
