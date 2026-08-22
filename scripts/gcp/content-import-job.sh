#!/usr/bin/env bash
#
# 通用內容匯入 Job：對 Cloud SQL 跑任一支 `scripts.import_*` 匯入器。
#
#   scripts/gcp/content-import-job.sh import_quests
#   scripts/gcp/content-import-job.sh import_story_arcs --dry-run
#
# ## 為什麼是一支通用腳本，不是再複製五份
#
# `scripts/gcp/` 底下已經有五支幾乎逐字相同的 wrapper（spirits／personas／
# landmarks／notes／districts），差別只在最後那個 module 名稱。再加四支（quests、
# story_arcs、story_strings、daily_event_calendars）等於同一段設定維護九份——
# 而它們必須逐字一致，否則某一支的 secrets 少一把，錯誤訊息會看起來跟匯入
# 完全無關。
#
# 既有那五支保留不動：它們已經被寫進文件與別人的操作習慣裡，改名的代價比
# 留著大。新的匯入器走這一支。
#
# ## ⚠️ 映像檔要是最新的
#
# `content/` 是 `COPY content ./content` 打包進映像檔的，不是執行期讀本機檔案。
# 改完內容沒有重建映像檔就跑這支，匯入的會是上一版，而且**從 Job 的輸出看不
# 出來**——它會很正常地印出它讀到的那一版。
#
#   gcloud builds submit --project=citysoul \
#     --tag=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
#
# ## ⚠️ 劇情三支有相依，順序不能換
#
#   1. import_quests          —— story_arcs 的 required_quest_ids 要查得到任務
#   2. import_story_strings   —— info_cards 的 text_key 要查得到字串
#   3. import_story_arcs      —— 兩者都會在寫入前驗證，缺了就整批拒收
#
# 順序錯的話 Job 會失敗並指名缺什麼，不會寫進半套資料。

set -euo pipefail

MODULE="${1:?用法：content-import-job.sh <module 名稱，例如 import_quests> [額外參數...]}"
shift || true

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
# Job 名稱依 module 命名：citysoul-import-quests、citysoul-import-story-arcs…
JOB_NAME="${JOB_NAME:-citysoul-$(printf '%s' "${MODULE}" | tr '_' '-')}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 三把 token 金鑰在 `settings = Settings()` 就會被讀取，即使匯入完全用不到
# 它們。少給一把，Job 會停在 pydantic 的驗證錯誤，訊息看起來跟匯入毫無關係。
SECRETS="DATABASE_URL=citysoul-database-url:latest"
SECRETS="${SECRETS},SESSION_TOKEN_SECRET=session-token-secret:latest"
SECRETS="${SECRETS},ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest"
SECRETS="${SECRETS},SENSE_TOKEN_SECRET=sense-token-secret:latest"

# REDIS_URL 是必填欄位，但匯入不會真的去連 Redis。語法合法的佔位值就夠了。
ENV_VARS="APP_ENV=production,REDIS_URL=redis://127.0.0.1:6379/0,GCP_PROJECT_ID=${PROJECT_ID}"

ARGS="-m,scripts.${MODULE}"
for extra in "$@"; do
  ARGS="${ARGS},${extra}"
done

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
  --args="${ARGS}"

# --max-retries=0 同其他 Job：失敗要人去看。匯入器本身冪等，但自動重試會把
# 第一個錯誤埋掉。

run gcloud run jobs execute "${JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait
