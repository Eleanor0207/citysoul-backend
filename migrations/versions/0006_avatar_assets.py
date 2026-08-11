"""avatar_assets（issue #38）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07

GET /assets/{avatarId}（v2.1 §7.4）需要一個地方記「這個 avatar 目前的 bundle
URL 跟版本號是什麼」，好讓客戶端 F7（下載與快取）問得出「我快取的這版還是
最新的嗎」。這張表就是那個單一真相來源——不存資產本身，只存指到哪個版本。

`version` 是字串，不是遞增整數：版本號格式（時間戳、語意化版本、bundle
hash）是資產產線的決定，不該被這張表的型別綁死。
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "avatar_assets",
        sa.Column("avatar_id", sa.String(64), primary_key=True),
        sa.Column("bundle_url", sa.Text(), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("avatar_assets")
