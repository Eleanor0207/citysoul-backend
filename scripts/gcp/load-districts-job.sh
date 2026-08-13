#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-load-districts」：把
# `data/districts/*.geojson` 載進 `brain.districts`。
#
# ## 為什麼另開一支 Job 而不是覆寫 citysoul-migrate
#
# `gcloud run jobs execute` **不支援 `--command` 覆寫**（只有 `--args`），而
# migrate job 的 entrypoint 是 `alembic`，所以沒辦法借它跑 `python -m`。
# 另一條路是 `jobs update` 改掉 command、跑完再改回來，但那會讓 migrate job
# 在中間那段時間處於「指令是錯的」狀態——中途失敗就留在那裡了。
#
# ## 為什麼邊界資料不放進 migration
#
# 那是內容不是 schema。行政區界會修（雖然很少），修的時候應該重跑這支 Job，
# 而不是寫一支「更新資料」的 migration。理由詳見 scripts/load_districts.py。
#
# 用法：
#   scripts/gcp/load-districts-job.sh
#   DRY_RUN=1 scripts/gcp/load-districts-job.sh
#
# 先決條件：migration 0014 已套用（`brain.districts` 存在），且映像檔內含
# `data/districts/`（Dockerfile 有 `COPY data ./data`）。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-load-districts}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# ⚠️ 三把 token 金鑰在 alembic 開始跑之前就會被讀取，即使 migration 完全用不到
# 它們。原因在 app/core/config.py：`settings = Settings()` 是模組層級的物件，
# 而 migrations/env.py 必須 import settings 才拿得到 database_url。少給一把，
# Job 會停在 pydantic 的驗證錯誤，訊息看起來跟 migration 毫無關係。
SECRETS="DATABASE_URL=citysoul-database-url:latest"
SECRETS="${SECRETS},SESSION_TOKEN_SECRET=session-token-secret:latest"
SECRETS="${SECRETS},ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest"
SECRETS="${SECRETS},SENSE_TOKEN_SECRET=sense-token-secret:latest"

# REDIS_URL 同樣是必填欄位，但 migration 不會真的去連 Redis。這裡給一個語法
# 合法的佔位值就夠了，不需要為了跑 migration 而把 Job 接進 VPC。
ENV_VARS="APP_ENV=production,REDIS_URL=redis://127.0.0.1:6379/0,GCP_PROJECT_ID=${PROJECT_ID}"

run() {
  echo "+ $*"
  if [[ -z "${DRY_RUN:-}" ]]; then
    "$@"
  fi
}

# `jobs deploy` 在 Job 不存在時建立、存在時更新，所以這支腳本可以重複執行，
# 不需要先判斷是第一次還是第 N 次部署。
run gcloud run jobs deploy "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE}" \
  --service-account="${SERVICE_ACCOUNT}" \
  --set-cloudsql-instances="${INSTANCE_CONNECTION_NAME}" \
  --set-env-vars="${ENV_VARS}" \
  --set-secrets="${SECRETS}" \
  --max-retries=0 \
  --task-timeout=10m \
  --command=python \
  --args=-m,scripts.load_districts

# --max-retries=0 同 migrate job：載入失敗要人去看。這支腳本本身是冪等的
# （UPSERT），重跑安全，但自動重試會把第一個錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
