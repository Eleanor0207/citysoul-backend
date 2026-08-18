"""Daily cache for B14 guided questions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guided_question_cache",
        sa.Column(
            "place_id",
            sa.String(64),
            sa.ForeignKey("spirits.spirit_id"),
            primary_key=True,
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
    op.drop_table("guided_question_cache")
