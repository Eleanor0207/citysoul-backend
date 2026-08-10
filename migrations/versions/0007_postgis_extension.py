"""啟用 PostGIS extension（issue #46）

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07

`brain.districts` 的地理圍欄（`GEOGRAPHY(POLYGON, 4326)` + `ST_Contains`）
需要 PostGIS。0004 原本想順手 `CREATE EXTENSION postgis`，結果本機映像檔
沒裝這個 extension，撞牆記錄見該支 migration 的 docstring；現在映像檔已經
補上（`Dockerfile.postgres`），這裡才真的能建。

只啟用 extension，不建任何 `districts`／`story_arcs` 表——那些是被這張票
「擋住的東西」清單裡明列、之後才開的票，這裡只解前置。
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis")
