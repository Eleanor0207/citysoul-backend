"""Add audit fields to story arcs and beats.

Story content is intentionally active when imported for the MVP.  ``reviewed_by``
is an audit field only; it is not an activation gate for these two tables.
"""

import sqlalchemy as sa
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("story_arcs", "story_beats"):
        op.add_column(
            table,
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            schema="brain",
        )
        op.add_column(table, sa.Column("reviewed_by", sa.Text(), nullable=True), schema="brain")


def downgrade() -> None:
    for table in ("story_beats", "story_arcs"):
        op.drop_column(table, "reviewed_by", schema="brain")
        op.drop_column(table, "active", schema="brain")
