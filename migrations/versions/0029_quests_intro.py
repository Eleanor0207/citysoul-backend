"""quests 加上 intro：任務自己的說明文字

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-22

`quests` 有 `title` 與 `steps`，但沒有地方放「這個任務要玩家做什麼」的那一段
說明。萬華主線的三個任務各有一段引言（文件 §4.2／§5.2／§6.2 的「玩家行動」
那一列），沒有欄位的話它只能塞進 `steps` 的 JSONB 裡——那會讓 `steps` 從一個
步驟清單變成「清單加上一個不是步驟的東西」，讀的人每次都要多想一次。

## 這是玩家逐字讀到的內容

跟 `canned_greetings.response_text`、`story_arcs.intro_document_content` 同一類：
**不經 LLM**，寫什麼玩家就看到什麼。所以它的措辭是成品，不是給模型的指令。

nullable——daily 型任務不進這張表，而未來的 resonance_gated 型任務不一定需要
引言。NULL 代表「這個任務沒有引言」，不是缺資料。
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quests", sa.Column("intro", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("quests", "intro")
