"""B14 引導提問分遠近兩層，並讓人格卡自帶提問保底句

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-21

兩件事，同一張票（BE 13）：

## 1. `guided_question_cache` 的主鍵加上 `proximity`

原本的鍵是 `place_id + event_date`，一個地標一天只存得下一組提問。現在同一天
同一個地標有兩組不同的內容——150m 感應圈一組、50m 在場一組——舊的鍵會讓先
進來的那一組把另一組擋在門外，第二種距離的玩家讀到的是不屬於他那一圈的題目。

`proximity` 只有 'far'（sense_token，150m）與 'near'（encounter_token，50m）
兩個值，由 CHECK 約束擋住其他字串。既有資料一律視為 'near'：舊的那一組是在
沒有遠近之分的時候生成的，內容假設玩家看得到建築物，那是近圈的假設。

## 2. `character_personas.guided_question_fallback`

LLM 生不出來時的保底提問，改成每張卡自己的三句，而不是十隻靈魂共用同一組。
nullable——NULL 代表這張卡還沒寫，呼叫端退回「用 quest_themes 組句」，再退回
通用兩句。跟 `daily_event_fallback` 那一批的邏輯一致：缺值不是錯誤。
"""

import sqlalchemy as sa
from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guided_question_cache",
        sa.Column(
            "proximity",
            sa.String(8),
            nullable=False,
            server_default="near",
        ),
    )
    op.create_check_constraint(
        "ck_guided_question_cache_proximity",
        "guided_question_cache",
        "proximity IN ('far', 'near')",
    )
    op.drop_constraint(
        "guided_question_cache_pkey", "guided_question_cache", type_="primary"
    )
    op.create_primary_key(
        "guided_question_cache_pkey",
        "guided_question_cache",
        ["place_id", "event_date", "proximity"],
    )

    op.add_column(
        "character_personas",
        sa.Column("guided_question_fallback", sa.ARRAY(sa.Text()), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column(
        "character_personas", "guided_question_fallback", schema="brain"
    )
    op.drop_constraint(
        "guided_question_cache_pkey", "guided_question_cache", type_="primary"
    )
    # 降版時只留近圈那一組，遠圈的先刪掉，否則舊主鍵會撞。
    op.execute("DELETE FROM guided_question_cache WHERE proximity <> 'near'")
    op.create_primary_key(
        "guided_question_cache_pkey",
        "guided_question_cache",
        ["place_id", "event_date"],
    )
    op.drop_constraint(
        "ck_guided_question_cache_proximity",
        "guided_question_cache",
        type_="check",
    )
    op.drop_column("guided_question_cache", "proximity")
