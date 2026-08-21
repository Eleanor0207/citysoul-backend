#!/usr/bin/env bash
#
# 建立／更新並執行 Cloud Run Job「citysoul-import-landmarks」：把
# `content/landmarks/*.yaml` 匯入 Cloud SQL 的 `brain.landmark_souls` 與
# `brain.districts`（區級基調）。
#
# ## 為什麼補這一支
#
# 其他四份內容匯入器（spirits／personas／districts／daily-event-notes）都有
# wrapper，只有史實層沒有——但 `common_misconceptions` 這種內容改動需要重跑
# 匯入，手打 gcloud 參數會踩到跟人格卡同一顆地雷（見下方 SECRETS）。
#
# ## 這支匯入的是史實層，沒有審核欄位
#
# `landmark_souls` 不像 `character_personas` 有 `active`／`reviewed_by`，
# **寫進去就生效**，沒有第二道閘。把關只能在匯入前：YAML 由
# `landmark_md_to_yaml.py` 從 citysoul-doc 的研究檔產生，而研究檔經 Lead 覆核。
# 匯入器擋得掉佔位字串與空的史實表，擋不掉寫錯的史實。
#
# 也沒有版本欄位可以擋重跑：同一個 `landmark_id` 重跑是 UPDATE，冪等。這點跟
# 人格卡相反——那邊改內容一定要 version 加一，這邊不用。
#
# ## ⚠️ 映像檔要是最新的
#
# `content/landmarks/` 是 `COPY content ./content` 打包進映像檔的，不是執行期讀
# 本機檔案。改完 YAML 沒有重建映像檔就跑這支，匯入的會是上一版的史實，而且從
# Job 的輸出看不出來——它會很正常地印出它讀到的那一版。
#
#   gcloud builds submit --project=citysoul \
#     --tag=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
#
# 內容的真正來源是 citysoul-doc 的 `landmark/*.md`。順序是：改 md →
# `python -m scripts.landmark_md_to_yaml` → 重建映像檔 → 跑這支。
#
# ## 先決條件
#
# `brain.characters` 要先有對應的列（`import_spirits` 建的）。整批驗完才開交易，
# 任何一份不合格就整批不寫——不會留下「前六份進去了、後三份沒有」那種狀態。
#
# 用法：
#   scripts/gcp/import-landmarks-job.sh            # 部署後直接寫入
#   ARGS_SUFFIX=,--dry-run scripts/gcp/import-landmarks-job.sh   # 只驗證不寫
#   DRY_RUN=1 scripts/gcp/import-landmarks-job.sh  # 連 gcloud 都不呼叫，只印指令

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-import-landmarks}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 加在 `-m,scripts.import_landmarks` 後面的旗標，例如 `,--dry-run`。
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
  --args="-m,scripts.import_landmarks${ARGS_SUFFIX}"

# --max-retries=0 同其他 Job：失敗要人去看。腳本本身冪等，但自動重試會把第一個
# 錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
