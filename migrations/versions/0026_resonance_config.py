"""共鳴值規則搬進資料表

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-19

backend#73（#52 拍板）：`resonance.py` 原本把門檻與各來源點數寫死成 Python
常數，改成資料表——比照 `usage_tier_limits`（migration 0004）的正規化模式，
一個 key 對一個 value，之後再調數字只是改一筆資料，不用重新部署。

跟配額不同的是共鳴值規則不分 tier，是全域單一一組值，所以這裡不需要複合
主鍵，`config_key` 本身就夠。

## 種子資料

前 5 筆對應現行 `resonance.py` 常數與已拍板的門檻步調（#52，維持 SDD v2.2
現行數值不變）：

- `threshold_stage_1` / `_2` / `_3`：10 / 40 / 100
- `amount_encounter_collection`：10
- `amount_quest`：20

後 2 筆是這輪一併拍板、但程式碼還沒接上的新來源（#70 每日對話、#72 劇情
結局），先把值種進表裡，等那兩張票落地時直接讀表，不用再開一次 migration：

- `amount_dialogue`：10（#70，尚無程式碼讀取）
- `amount_story_completion`：30（#72，尚無程式碼讀取）
"""

import sqlalchemy as sa
from alembic import op


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


_SEED_VALUES = {
    "threshold_stage_1": 10,
    "threshold_stage_2": 40,
    "threshold_stage_3": 100,
    "amount_encounter_collection": 10,
    "amount_quest": 20,
    "amount_dialogue": 10,
    "amount_story_completion": 30,
}


def upgrade() -> None:
    resonance_config = op.create_table(
        "resonance_config",
        sa.Column("config_key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
    )

    op.bulk_insert(
        resonance_config,
        [{"config_key": key, "value": value} for key, value in _SEED_VALUES.items()],
    )


def downgrade() -> None:
    op.drop_table("resonance_config")
