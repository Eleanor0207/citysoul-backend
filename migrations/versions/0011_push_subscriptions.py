"""push_subscriptions（S11／#39）

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08

SDD v1 §3.1。推播訂閱：一個玩家一列。

## 主鍵是 player_id，不是代理鍵

換手機、token 輪替時必須是**覆蓋**而不是累積。用代理鍵的話，一個玩家會慢慢
長出十幾列失效的 token，而群發時得自己想辦法挑「最新的那個」——那個判斷遲早
會出錯，然後推播就送到別人的舊裝置上。

主鍵直接是 `player_id`，資料庫層級就不可能出現第二列。

## is_subscribed 是欄位而不是刪除列

退訂用 `is_subscribed=false` 而不是刪掉那一列，因為 token 還有用：玩家重新訂閱時
不需要重新註冊裝置。刪掉的話，退訂再訂閱會變成一次完整的重新註冊流程。

⚠️ **沒有任何位置欄位。** 推播是通知，不是內容本身——玩家點進來後才呼叫既有
端點取內容，所以這張表不需要知道他在哪裡。
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column(
            "player_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        sa.Column("push_token", sa.String(256), nullable=False),
        sa.Column("is_subscribed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions")
