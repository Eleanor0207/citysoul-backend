"""daily_event_cache 表（S10，#26）

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08

SDD §3.1。跟 B9（#20，內容生成）刻意拆開：這張表只管「存」，不管「怎麼
生成」。PK 是 `(place_id, event_date)`——同一天同一地標只該有一列，這條
由主鍵本身保證，不是應用層檢查（排程重複觸發時，撞到主鍵衝突就當作
已經有了，見 `app.modules.body.daily_event.trigger_daily_event_generation`）。

⚠️ **編號衝突提醒**：`issue-30-api-contract` 分支上也有一支 `0006`
（spirits 方位設定）。兩支都是從 main 的 0005 分出來的，兩邊合併時勢必
要有一支改編號成 0007——這是本機分支比較週期（4人各自獨立開發）刻意允許
發生的情況，不是這裡的錯，見 `docs/dev-notes/branch-tracker.md`。
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
