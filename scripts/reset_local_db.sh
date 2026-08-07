#!/usr/bin/env bash
# 本機開發用：切分支時，Postgres 目前的 alembic 版本可能跟這個分支的
# migrations/versions/ 對不上（例如某分支多了一支還沒 merge 的 migration），
# 導致 `alembic upgrade head` 直接報「找不到 revision」。
#
# 開發資料沒有留存價值（README 已經這樣定調），砍掉重來比手動修補快：
#   docker compose down -v && docker compose up -d && scripts.init_db
#
# 用法：
#   ./scripts/reset_local_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose down -v
docker compose up -d

echo "等待 Postgres 就緒..."
until docker compose exec -T db pg_isready -U city_soul > /dev/null 2>&1; do
  sleep 1
done

uv run python -m scripts.init_db
