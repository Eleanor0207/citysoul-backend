#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-import-spirits」：把
# `content/spirits.yaml` 匯入 Cloud SQL 的 `spirits` 與 `brain.characters`。
#
# ## 為什麼要另開一支 Job
#
# 同 `load-districts-job.sh`：`gcloud run jobs execute` 不支援 `--command` 覆寫
# （只有 `--args`），migrate job 的 entrypoint 是 `alembic`，借不到。
#
# ## 這支跟 migrate 的差別是內容不是 schema
#
# 靈魂的座標會改（實地勘查之後每一個都要覆蓋一次），改的時候重跑這支。匯入器
# 本身是 UPSERT，重跑安全。
#
# ## ⚠️ 映像檔要是最新的
#
# `content/spirits.yaml` 是 `COPY content ./content` 打包進映像檔的，不是執行期
# 讀本機檔案。改完 YAML 沒有重建映像檔就跑這支，匯入的會是上一版的座標，而且
# 從 Job 的輸出看不出來——它會很正常地印出它讀到的那一版。
#
#   gcloud builds submit --project=citysoul \
#     --tag=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
#
# ## 先決條件
#
# `brain.landmark_souls` 要先有對應的列，否則匯入器會列出缺哪幾個並以 1 結束
# （外鍵插不進去）。那是 `scripts.import_landmarks` 的事。
#
# 用法：
#   scripts/gcp/import-spirits-job.sh            # 部署後直接寫入
#   ARGS_SUFFIX=,--dry-run scripts/gcp/import-spirits-job.sh   # 只驗證不寫
#   DRY_RUN=1 scripts/gcp/import-spirits-job.sh  # 連 gcloud 都不呼叫，只印指令

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-import-spirits}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 加在 `-m,scripts.import_spirits` 後面的旗標，例如 `,--dry-run`。
ARGS_SUFFIX="${ARGS_SUFFIX:-}"

# 三把 token 金鑰在 `settings = Settings()` 就會被讀取，即使這支腳本完全用不到
# 它們。少給一把，Job 會停在 pydantic 的驗證錯誤，訊息看起來跟匯入毫無關係。
SECRETS="DATABASE_URL=citysoul-database-url:latest"
SECRETS="${SECRETS},SESSION_TOKEN_SECRET=session-token-secret:latest"
SECRETS="${SECRETS},ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest"
SECRETS="${SECRETS},SENSE_TOKEN_SECRET=sense-token-secret:latest"

# REDIS_URL 是必填欄位，但匯入不會真的去連 Redis。語法合法的佔位值就夠了。
ENV_VARS="APP_ENV=production,REDIS_URL=redis://127.0.0.1:6379/0,GCP_PROJECT_ID=${PROJECT_ID}"

run() {
  echo "+ $*"
  if [[ -z "${DRY_RUN:-}" ]]; then
    "$@"
  fi
}

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
  --args="-m,scripts.import_spirits${ARGS_SUFFIX}"

# --max-retries=0 同其他 Job：失敗要人去看。腳本本身冪等，但自動重試會把第一個
# 錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
