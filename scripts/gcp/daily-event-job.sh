#!/usr/bin/env bash
#
# 建立／更新 Cloud Run Job「citysoul-daily-event」（B9 當日情境批次），並把它接上
# Cloud Scheduler，每天清晨自動跑一次。
#
# ## 為什麼補這一支
#
# BE#40 已關，但 `scripts/gcp/` 一直沒有對應的 job——批次只能有人記得手動跑。
# 「玩家隔天回來看到變化」是 CONTEXT.md 對核心迴圈的定義的最後一句，而那句話
# 目前靠的是有沒有人記得執行一支腳本。這支把它變成排程。
#
# ## 這支跟其他 job wrapper 的差別：它會建立排程
#
# migrate／import-spirits／import-personas 都是「部署完立刻執行一次就結束」。
# 這支多了第二段：建立（或更新）一個 Cloud Scheduler job，之後每天由 Scheduler
# 觸發，不需要人。`RUN_NOW=1` 才會順便立刻執行一次。
#
# ## 排程時間為什麼是清晨 5 點
#
# 當日情境是玩家「早上開 App」看到的東西，所以要在人出門之前產好。批次本身會
# 對每隻靈魂打一次 Gemini，九隻大約數十秒；5 點跑完，最早的玩家也還沒起床。
# 時區明確指定 Asia/Taipei——不指定的話 Scheduler 走 UTC，會變成台灣下午 1 點，
# 也就是「玩家看到今天的內容之前，內容還沒生出來」。
#
# ## 重跑是安全的
#
# 快取主鍵是 `(place_id, event_date)`，同一天重跑走 insert-then-update。所以
# `--max-retries` 可以不是 0——這支跟其他 job 相反，失敗自動重試是對的，沒有
# 人會在清晨 5 點看 log。
#
# ## ⚠️ 映像檔要是最新的
#
# 同 import-personas-job.sh：程式碼是打包進映像檔的。改完 `daily_event_batch.py`
# 沒有重建映像檔就跑這支，跑的是上一版。
#
#   gcloud builds submit --project=citysoul \
#     --tag=asia-east1-docker.pkg.dev/citysoul/citysoul/backend:latest .
#
# ## ⚠️ 排程接上不等於內容就有了
#
# 這支解決的是「批次沒有觸發來源」。輪播池（`sources`）如果是空的，批次跑完
# 每隻靈魂仍然回 `is_fallback: true`——那是另一件事（待辦清單 P1 第 11b 項）。
# 順序是：先接排程（這支）→ 再填輪播池 → 才看得到非 fallback 的內容。
#
# 用法：
#   scripts/gcp/daily-event-job.sh              # 部署 Job ＋ 建立／更新排程
#   RUN_NOW=1 scripts/gcp/daily-event-job.sh    # 順便立刻執行一次
#   SCHEDULE_ONLY=1 scripts/gcp/daily-event-job.sh  # 只調排程，不碰 Job
#   DRY_RUN=1 scripts/gcp/daily-event-job.sh    # 連 gcloud 都不呼叫，只印指令

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-citysoul}"
REGION="${REGION:-asia-east1}"
JOB_NAME="${JOB_NAME:-citysoul-daily-event}"
SCHEDULER_JOB_NAME="${SCHEDULER_JOB_NAME:-citysoul-daily-event-nightly}"
SCHEDULE="${SCHEDULE:-0 5 * * *}"
TIME_ZONE="${TIME_ZONE:-Asia/Taipei}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/citysoul/backend:latest}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-citysoul-run@${PROJECT_ID}.iam.gserviceaccount.com}"
INSTANCE_CONNECTION_NAME="${INSTANCE_CONNECTION_NAME:-${PROJECT_ID}:${REGION}:citysoul}"

# 三把 token 金鑰在 `settings = Settings()` 就會被讀取，即使這支批次完全用不到
# 它們。少給一把，Job 會停在 pydantic 的驗證錯誤，訊息看起來跟當日情境毫無關係。
SECRETS="DATABASE_URL=citysoul-database-url:latest"
SECRETS="${SECRETS},SESSION_TOKEN_SECRET=session-token-secret:latest"
SECRETS="${SECRETS},ENCOUNTER_TOKEN_SECRET=encounter-token-secret:latest"
SECRETS="${SECRETS},SENSE_TOKEN_SECRET=sense-token-secret:latest"

# GCP_LOCATION 是 Vertex AI 的區域，值跟線上服務一致（service.yaml 是 global）。
# 這支跟匯入腳本不同——它**真的會呼叫 Gemini**，這一格給錯會在生成時才爆。
# REDIS_URL 是必填欄位，但批次不連 Redis，語法合法的佔位值就夠。
ENV_VARS="APP_ENV=production,REDIS_URL=redis://127.0.0.1:6379/0"
ENV_VARS="${ENV_VARS},GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=global"

run() {
  echo "+ $*"
  if [[ -z "${DRY_RUN:-}" ]]; then
    "$@"
  fi
}

if [[ -z "${SCHEDULE_ONLY:-}" ]]; then
  run gcloud run jobs deploy "${JOB_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --image="${IMAGE}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --set-cloudsql-instances="${INSTANCE_CONNECTION_NAME}" \
    --set-env-vars="${ENV_VARS}" \
    --set-secrets="${SECRETS}" \
    --max-retries=2 \
    --task-timeout=30m \
    --command=python \
    --args="-m,scripts.run_daily_event_batch"
fi

# ── Cloud Scheduler ────────────────────────────────────────────────────────
#
# Scheduler 沒有「觸發 Cloud Run Job」的原生目標型別，走的是 HTTP＋OAuth 呼叫
# Cloud Run Admin API 的 `:run`。URI 裡的 namespace 是**專案 ID**，不是 region。
#
# 觸發用的服務帳號沿用 citysoul-run。它需要對這個 Job 有 roles/run.invoker：
#
#   gcloud run jobs add-iam-policy-binding "${JOB_NAME}" \
#     --project="${PROJECT_ID}" --region="${REGION}" \
#     --member="serviceAccount:${SERVICE_ACCOUNT}" --role=roles/run.invoker
#
# 少了這一行，排程會準時觸發並安靜地拿到 403——Scheduler 的執行紀錄看得到，
# Job 的執行清單則什麼都不會多出來。這是這支最容易漏掉、也最難察覺的一步。
SCHEDULER_URI="https://${REGION}-run.googleapis.com"
SCHEDULER_URI="${SCHEDULER_URI}/apis/run.googleapis.com/v1"
SCHEDULER_URI="${SCHEDULER_URI}/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run"

# create 與 update 是兩個不同的子指令，重跑這支腳本時要走對的那一個，否則
# 第二次執行會以「已存在」失敗。
SCHEDULER_VERB=create
if [[ -z "${DRY_RUN:-}" ]] && gcloud scheduler jobs describe "${SCHEDULER_JOB_NAME}" \
     --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  SCHEDULER_VERB=update
fi

run gcloud scheduler jobs "${SCHEDULER_VERB}" http "${SCHEDULER_JOB_NAME}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --schedule="${SCHEDULE}" \
  --time-zone="${TIME_ZONE}" \
  --uri="${SCHEDULER_URI}" \
  --http-method=POST \
  --oauth-service-account-email="${SERVICE_ACCOUNT}" \
  --attempt-deadline=30m

if [[ -n "${RUN_NOW:-}" ]]; then
  run gcloud run jobs execute "${JOB_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --wait
fi
