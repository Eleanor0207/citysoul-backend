"""daily_event_cache 表（S10，#26）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08

SDD §3.1。跟 B9（#20，內容生成）刻意拆開：這張表只管「存」，不管「怎麼
生成」。PK 是 `(place_id, event_date)`——同一天同一地標只該有一列，這條
由主鍵本身保證，不是應用層檢查（排程重複觸發時，撞到主鍵衝突就當作
已經有了，見 `app.modules.body.daily_event.trigger_daily_event_generation`）。

（歷史註記：`issue-30-api-contract` 分支上原本也有一支編號 0006 的
migration——兩張票都從當時的 head 0005 分出去，各自取了下一個編號。合併時
把那支改成 0007 接在這支後面，衝突已解決。）
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_event_cache",
        sa.Column(
            "place_id", sa.String(64), sa.ForeignKey("spirits.spirit_id"), primary_key=True
        ),
        sa.Column("event_date", sa.Date(), primary_key=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("daily_event_cache")
