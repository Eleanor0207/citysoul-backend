#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-import-personas」：把
# `content/personas/*.yaml` 匯入 Cloud SQL 的 `brain.character_personas` 與
# `brain.canned_greetings`。
#
# ## 為什麼補這一支
#
# `citysoul-import-personas` 這個 Job 在 GCP 上早就存在（backend#56 建的），但
# repo 裡只有 spirits 與 districts 有 wrapper，人格卡沒有——結果是每次要匯入
# 人格卡都得手打一長串 gcloud 參數，而那串裡有三把 token 金鑰是「不給就會失敗、
# 但失敗訊息跟人格卡毫無關係」的地雷（見下方 SECRETS 的說明）。
#
# ## 這支跟 migrate 的差別是內容不是 schema
#
# 同 `import-spirits-job.sh`。人格卡改版（新增欄位、修文案、擴充問候語觸發語）
# 之後重跑這支。匯入器對「同一個 character_id + version 已存在」是跳過，不是
# 覆蓋——所以**改內容一定要同時把 `version` 加一**，否則跑了等於沒跑，而且
# 輸出會平靜地印出「已存在，略過」。
#
# ## ⚠️ 映像檔要是最新的
#
# `content/personas/` 是 `COPY content ./content` 打包進映像檔的，不是執行期讀
# 本機檔案。改完 YAML 沒有重建映像檔就跑這支，匯入的會是上一版的人格卡，而且
# 從 Job 的輸出看不出來——它會很正常地印出它讀到的那一版。
#
#   gcloud builds submit --project=citysoul \
#     --tag=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
#
# ## 先決條件
#
# `brain.characters` 要先有對應的列，那是 `import_spirits` 建的。兩支都要跑的
# 時候順序是 **spirits 先、personas 後**：characters 是人格卡的外鍵目標。
#
# 用法：
#   scripts/gcp/import-personas-job.sh            # 部署後直接寫入
#   ARGS_SUFFIX=,--dry-run scripts/gcp/import-personas-job.sh   # 只驗證不寫
#   DRY_RUN=1 scripts/gcp/import-personas-job.sh  # 連 gcloud 都不呼叫，只印指令

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-import-personas}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 加在 `-m,scripts.import_personas` 後面的旗標，例如 `,--dry-run`。
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
  --args="-m,scripts.import_personas${ARGS_SUFFIX}"

# --max-retries=0 同其他 Job：失敗要人去看。腳本本身冪等，但自動重試會把第一個
# 錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
