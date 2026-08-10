"""
enable_postgis

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-07 19:38:00.000000

Ticket #46．PostGIS extension 啟用。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 啟用 PostGIS 地理資訊擴充功能
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis;")
