#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-import-daily-event-notes」：把
# `content/daily_event_notes/*.yaml` 匯入 `brain.daily_event_curated_notes`。
#
# ## 為什麼這一支值得單獨排一個 job
#
# B9 的當日情境有三種來源：節慶日曆、官方活動、人工審核輪播池。前兩者只在特定
# 日子有東西，所以**平常日子能不能產出非 fallback 的敘事，完全取決於這一池**。
# 池是空的，玩家每天看到的都是同一句寫死的保底文案。
#
# ## 先決條件
#
# `spirits` 要先有對應的列（`import_spirits`）：這張表的 place_id 沒有外鍵擋著，
# 打錯字的話會安靜地永遠不被讀到，所以匯入器自己會比對 spirits 並擋下來。
#
# 內容改了要重跑，而且**映像檔要先重建**——content/ 是烘進映像檔的，不重建的話
# 跑的還是舊內容。
#
# 用法：
#   scripts/gcp/import-daily-event-notes-job.sh            # 部署後直接寫入
#   ARGS_SUFFIX=,--dry-run scripts/gcp/import-daily-event-notes-job.sh   # 只驗證不寫
#   ARGS_SUFFIX=,--prune scripts/gcp/import-daily-event-notes-job.sh     # 一併刪掉多出來的位置
#   DRY_RUN=1 scripts/gcp/import-daily-event-notes-job.sh  # 連 gcloud 都不呼叫，只印指令

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-import-daily-event-notes}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 加在 `-m,scripts.import_daily_event_notes` 後面的旗標，例如 `,--dry-run` 或 `,--prune`。
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
  --args="-m,scripts.import_daily_event_notes${ARGS_SUFFIX}"

# --max-retries=0 同其他 Job：失敗要人去看。腳本本身冪等，但自動重試會把第一個
# 錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
