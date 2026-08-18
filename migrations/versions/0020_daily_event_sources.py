"""Add reviewed B9 daily-event source tables and persona fallback text.

The calendar table contains both recurring festival rules and one-off official
events. Curated notes use an explicit stable order so selection can be derived
from the Taipei event date without storing another daily-state truth.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_personas",
        sa.Column("daily_event_fallback", sa.Text(), nullable=True),
        schema="brain",
    )

    op.create_table(
        "daily_event_calendars",
        sa.Column(
            "calendar_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("place_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("date_rule", sa.String(32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("start_month", sa.SmallInteger(), nullable=True),
        sa.Column("start_day", sa.SmallInteger(), nullable=True),
        sa.Column("end_month", sa.SmallInteger(), nullable=True),
        sa.Column("end_day", sa.SmallInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('festival', 'official_event')",
            name="ck_daily_event_calendar_event_type",
        ),
        sa.CheckConstraint(
            "date_rule IN ('gregorian_fixed', 'lunar_fixed', 'lunar_month_end', 'gregorian_range')",
            name="ck_daily_event_calendar_date_rule",
        ),
        sa.CheckConstraint("title <> ''", name="ck_daily_event_calendar_title"),
        sa.CheckConstraint(
            "(date_rule = 'gregorian_range' AND start_date IS NOT NULL AND end_date IS NOT NULL AND start_date <= end_date AND start_month IS NULL AND start_day IS NULL AND end_month IS NULL AND end_day IS NULL) OR "
            "(date_rule IN ('gregorian_fixed', 'lunar_fixed') AND start_date IS NULL AND end_date IS NULL AND start_month BETWEEN 1 AND 12 AND start_day BETWEEN 1 AND 31 AND end_month BETWEEN 1 AND 12 AND end_day BETWEEN 1 AND 31) OR "
            "(date_rule = 'lunar_month_end' AND start_date IS NULL AND end_date IS NULL AND start_month BETWEEN 1 AND 12 AND start_day IS NULL AND end_month = start_month AND end_day IS NULL)",
            name="ck_daily_event_calendar_date_shape",
        ),
        schema="brain",
    )
    op.create_primary_key(
        "pk_daily_event_calendars", "daily_event_calendars", ["calendar_id"], schema="brain"
    )
    op.create_index(
        "ix_daily_event_calendars_place_active",
        "daily_event_calendars",
        ["place_id", "active"],
        schema="brain",
    )

    op.create_table(
        "daily_event_curated_notes",
        sa.Column(
            "note_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("place_id", sa.String(64), nullable=False),
        sa.Column("rotation_order", sa.Integer(), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "place_id", "rotation_order", name="uq_daily_event_curated_notes_rotation"
        ),
        sa.CheckConstraint("rotation_order >= 0", name="ck_daily_event_curated_notes_order"),
        sa.CheckConstraint("note_text <> ''", name="ck_daily_event_curated_notes_text"),
        schema="brain",
    )
    op.create_primary_key(
        "pk_daily_event_curated_notes", "daily_event_curated_notes", ["note_id"], schema="brain"
    )
    op.create_index(
        "ix_daily_event_curated_notes_place_active",
        "daily_event_curated_notes",
        ["place_id", "active"],
        schema="brain",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_daily_event_curated_notes_place_active",
        table_name="daily_event_curated_notes",
        schema="brain",
    )
    op.drop_table("daily_event_curated_notes", schema="brain")
    op.drop_index(
        "ix_daily_event_calendars_place_active",
        table_name="daily_event_calendars",
        schema="brain",
    )
    op.drop_table("daily_event_calendars", schema="brain")
    op.drop_column("character_personas", "daily_event_fallback", schema="brain")
