"""spirits 方位設定（bearing_deg／height_offset_m）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07

SDD v2.1 §10.2：3DoF 定向需要靈魂相對召喚點的方位角與高度偏移。Phase 1
契約凍結前補上，見 issue #30。

兩欄皆 `NOT NULL DEFAULT 0`：現有的龍山寺 seed row 沒有這兩個值，0 是
「未特別設定方位，正對玩家、無高度偏移」的合理預設，不是佔位的假資料。
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirits",
        sa.Column("bearing_deg", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "spirits",
        sa.Column("height_offset_m", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("spirits", "height_offset_m")
    op.drop_column("spirits", "bearing_deg")
