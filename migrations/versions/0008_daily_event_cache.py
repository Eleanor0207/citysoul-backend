"""daily_event_cache（S10／#26）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-08

當日情境的快取（SDD §3.1）。內容由 B9 生成（#20），這張表管的是「什麼時候
生成的、放在哪、什麼時候過期」——生成與快取刻意分屬不同模組（v2.1 §6.4）。

## PK 是 (place_id, event_date)

「一個地標一天一筆」由主鍵保證，不是靠排程自己記得別重複觸發。排程重複觸發
是正常的（重試、多實例、手動補跑），所以去重必須在資料庫層級——同 #16 共鳴
入帳與 #32 配額的處理。

## event_date 是 DATE 而不是 TIMESTAMP

日界以 Asia/Taipei 午夜為準（`quests.taipei_today()`）。存成帶時區的時間點會
逼每個讀取端自己再算一次「這是台北的哪一天」，而那正是 #15 踩過的坑。

## content 是 JSONB 而不是一欄文字

B9 的 `DailyEventContent` 目前只有 narrative_text 有用，但它還帶著 sources
（稽核用）與 is_fallback。攤平成欄位的話，B9 每加一個欄位這裡就要一支 migration。

## expires_at 存在但不由讀取端強制

保底策略是「今天沒有就回昨天」，所以過期的內容仍然有用——它比空畫面好。
`expires_at` 是給清理工作用的訊號，不是讀取時的過濾條件。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_event_cache",
        sa.Column(
            "place_id",
            sa.String(64),
            sa.ForeignKey("spirits.spirit_id"),
            primary_key=True,
        ),
        sa.Column("event_date", sa.Date(), primary_key=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("daily_event_cache")
