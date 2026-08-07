"""baseline: 現有 create_all 的等價狀態

Revision ID: 0001
Revises:
Create Date: 2026-08-07

這支 migration **不改變任何東西**。它的內容是 `Base.metadata.create_all()`
在今天會建出來的東西，一字不差。

存在的理由是讓已經有資料的開發資料庫可以直接：

    uv run python -m alembic stamp 0001

宣告「我已經在這個狀態了」，不需要砍掉重建。空的資料庫則走
`alembic upgrade head`，效果與過去的 `scripts.init_db` 相同。

真正的變更全部在 0002。這兩支刻意分開，就是為了讓「對齊文件」那批破壞性
操作有一個明確的、可回退的起點。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import pgvector.sqlalchemy

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    # brain schema 與 pgvector。原本在 scripts/init_db.py 的 main() 裡，
    # 現在歸 migration 管——「資料庫長什麼樣」全部由 migration 定義，
    # 沒有第二個地方會動 schema。
    op.execute("CREATE SCHEMA IF NOT EXISTS brain")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "players",
        sa.Column("player_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.String(128), nullable=False, unique=True),
        sa.Column("account_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "spirits",
        sa.Column("place_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("summon_radius_m", sa.Integer(), nullable=False),
        sa.Column("sense_radius_m", sa.Integer(), nullable=False, server_default="150"),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "quest_progress",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        sa.Column("quest_id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress_value", sa.Integer(), nullable=False),
        sa.Column("attempts_today", sa.Integer(), nullable=False),
        sa.Column("attempts_date", sa.Date(), nullable=False),
        sa.Column("current_token_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "resonance",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        sa.Column(
            "spirit_id", sa.String(64), sa.ForeignKey("spirits.place_id"), primary_key=True
        ),
        sa.Column("resonance_value", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column(
            "last_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "resonance_events",
        sa.Column("resonance_event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        sa.Column("spirit_id", sa.String(64), sa.ForeignKey("spirits.place_id"), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column(
            "awarded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "player_id", "source_type", "source_id", name="uq_resonance_events_source"
        ),
    )

    op.create_table(
        "persona_cards",
        sa.Column("spirit_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="brain",
    )

    op.create_table(
        "memory_embeddings",
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), primary_key=True),
        # 刻意沒有 ForeignKey：WBS-API 決策4，身體與腦袋的表不建跨 schema 外鍵。
        sa.Column("player_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spirit_id", sa.String(64), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="brain",
    )
    op.create_index(
        "ix_memory_embeddings_embedding_cosine",
        "memory_embeddings",
        ["embedding"],
        schema="brain",
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_memory_embeddings_player_spirit",
        "memory_embeddings",
        ["player_id", "spirit_id"],
        schema="brain",
    )


def downgrade() -> None:
    op.drop_table("memory_embeddings", schema="brain")
    op.drop_table("persona_cards", schema="brain")
    op.drop_table("resonance_events")
    op.drop_table("resonance")
    op.drop_table("quest_progress")
    op.drop_table("spirits")
    op.drop_table("players")
    # 刻意不 drop schema 與 extension：downgrade 的用途是退回上一個 schema 版本，
    # 不是把資料庫還原成出廠狀態。extension 留著不會有害，drop 掉反而可能弄壞
    # 同一個資料庫上的其他東西。
