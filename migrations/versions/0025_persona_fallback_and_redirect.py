"""Add quota/LLM-failure fallback text and taboo redirect style to character_personas.

backend#48. All three are nullable — NULL means not yet authored, callers fall
back to the existing generic copy rather than treating a missing value as an
error. Content itself is authored separately (draft content pending Lead
review, tracked in content/personas/*.yaml).
"""

import sqlalchemy as sa
from alembic import op


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_personas",
        sa.Column("quota_fallback", sa.Text(), nullable=True),
        schema="brain",
    )
    op.add_column(
        "character_personas",
        sa.Column("llm_failure_fallback", sa.Text(), nullable=True),
        schema="brain",
    )
    # taboos 說「不談什麼」；這個欄位說「被問到時怎麼轉開,用角色口吻,不是
    # 系統式拒絕」——見 backend#48 AC2。跟 taboos 分開存,因為它是給模型的
    # 風格示範,不是條列式的規則清單,兩者在 prompt 裡的排版方式不一樣。
    op.add_column(
        "character_personas",
        sa.Column("taboo_redirect_style", sa.Text(), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("character_personas", "taboo_redirect_style", schema="brain")
    op.drop_column("character_personas", "llm_failure_fallback", schema="brain")
    op.drop_column("character_personas", "quota_fallback", schema="brain")
