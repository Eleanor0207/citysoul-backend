"""daily_event_cache（issue #26）

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07

SDD §3.1：當日情境快取，供 `GET /spirits/{placeId}/daily-event`（issue #26）
讀取，由每日排程呼叫 B9（issue #20）寫入。PK 是 `(place_id, event_date)`，
不是代理鍵——同一天同一地標只該有一列，排程重複觸發時靠這個主鍵約束擋住
重複，不用先查再判斷（同 0003 `resonance_events` 的設計）。
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_event_cache",
        sa.Column("place_id", sa.String(64), sa.ForeignKey("spirits.spirit_id"), primary_key=True),
        sa.Column("event_date", sa.Date(), primary_key=True),
        sa.Column("content", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("daily_event_cache")
