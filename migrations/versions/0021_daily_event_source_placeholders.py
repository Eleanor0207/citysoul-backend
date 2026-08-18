"""Add inactive markers showing where human-reviewed B9 content is required.

These rows are deliberately inactive and can never be selected by the source
loader. They are schema/operator breadcrumbs, not player-visible content.
"""

from alembic import op
from sqlalchemy import text

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_CALENDAR_ID = "00000000-0000-0000-0000-000000000020"
_NOTE_ID = "00000000-0000-0000-0000-000000000021"
_PLACEHOLDER = "PENDING_HUMAN_REVIEW"


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            INSERT INTO brain.daily_event_calendars
                (calendar_id, place_id, event_type, title, date_rule,
                 start_month, start_day, end_month, end_day, active, reviewed_by)
            VALUES
                (:calendar_id, '__pending_place__', 'festival', :placeholder,
                 'gregorian_fixed', 1, 1, 1, 1, false, :placeholder)
            """
        ),
        {"calendar_id": _CALENDAR_ID, "placeholder": _PLACEHOLDER},
    )
    op.get_bind().execute(
        text(
            """
            INSERT INTO brain.daily_event_curated_notes
                (note_id, place_id, rotation_order, note_text, active, reviewed_by)
            VALUES
                (:note_id, '__pending_place__', 0, :placeholder, false, :placeholder)
            """
        ),
        {"note_id": _NOTE_ID, "placeholder": _PLACEHOLDER},
    )


def downgrade() -> None:
    op.get_bind().execute(
        text(
            """
            DELETE FROM brain.daily_event_curated_notes
            WHERE note_id = :note_id
            """
        ),
        {"note_id": _NOTE_ID},
    )
    op.get_bind().execute(
        text(
            """
            DELETE FROM brain.daily_event_calendars
            WHERE calendar_id = :calendar_id
            """
        ),
        {"calendar_id": _CALENDAR_ID},
    )
