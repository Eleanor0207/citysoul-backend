#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-migrate」：對 Cloud SQL 跑
# `alembic upgrade head`。
#
# ## 為什麼是 Job 而不是在 app 啟動時跑 migration
#
# Cloud Run 會同時起多個實例。如果 migration 綁在應用程式啟動流程裡，擴容時
# 就會有多個實例同時對同一個資料庫跑 DDL；而且只要有一個 revision 失敗，
# 整個服務就起不來——等於把「schema 有問題」升級成「服務全掛」。Job 是獨立的
# 一次性執行，失敗就是失敗，正在服務的版本不受影響。
#
# ## 為什麼跟 app 用同一個映像檔
#
# migration 會 import `app.core.database` 與各模組的 models（原因見
# migrations/env.py 開頭）。用另一個映像檔就要維護第二份相依清單，而它必須跟
# app 的逐字一致，否則會出現「本機 autogenerate 出來的 migration 在 Job 裡
# 跑不動」這種只在雲端出現的錯誤。
#
# ## ⚠️ CREATE EXTENSION 需要 cloudsqlsuperuser
#
# migration 0006 會執行 `CREATE EXTENSION postgis`（0001 則要 vector）。
# Cloud SQL 上這需要 `cloudsqlsuperuser` 角色：內建的 `postgres` 帳號有，
# 後來自己建的一般使用者沒有，會噴 permission denied to create extension。
# DATABASE_URL 裡的帳號要挑對。
#
# 用法：
#   scripts/gcp/migrate-job.sh              # 建立/更新後執行
#   DRY_RUN=1 scripts/gcp/migrate-job.sh    # 只印出要跑的指令，不真的執行
#
# 先決條件：映像檔已推上 Artifact Registry（見 README「部署到 Cloud Run」）。

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-migrate}"
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
  --command=alembic \
  --args=upgrade,head

# --max-retries=0 是刻意的。migration 失敗要人去看，自動重試只會讓同一個壞掉的
# revision 再撞資料庫一次，並把真正的第一個錯誤埋在後面的重試日誌底下。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
