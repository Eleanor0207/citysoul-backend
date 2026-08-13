"""brain.districts 與 brain.city_souls 補審核欄位

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-13

`active` / `reviewed_by` / `reviewed_at`，語意與 `character_personas` 的同名欄位
一致。

## 為什麼現在補

這兩張表的基調文字**即將被注入 prompt**。在那之前，它們是「匯入就生效、沒有人
看過也一樣」——`scripts/import_landmarks.py` 今天把萬華區的基調寫進去時，過程中
沒有任何一個關卡問過人。

人格卡要人簽名才能上線，同樣會進 prompt 的基調卻不用，這個不對稱沒有理由。而且
基調的風險不是零：萬華那段寫了「頂下郊拚的移民拓墾衝突」，族群衝突史的措辭是有
分寸問題的。

**順序很重要：閘門要在接線之前補。** 反過來做的話，中間那段時間每一次萬華區對話
都會帶著沒人審過的文字，而那正是最難事後補救的東西。

## 沒有版本欄位，跟 character_personas 不同

人格是 append 新版本、舊版留著（主鍵 `(character_id, version)`），因為人格改動
需要能回溯「哪一版說了什麼」。基調沒有這個需求：它是描述一個地方長什麼樣子，
改了就是改了，不會有「玩家當時遇到的是第 2 版基調」這種爭議。

代價是改基調沒有歷史。真的需要時再加版本，那是一次獨立的決定。

## active 預設 false，既有資料一併關掉

`server_default="false"` 讓既有的萬華列直接變成未審核狀態——這是刻意的。那筆
資料確實沒有人審過，讓它保持「已生效」才是說謊。
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TABLES = ("districts", "city_souls")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
            schema="brain",
        )
        # nullable：還沒審過的列本來就沒有審核者，填一個佔位字串會讓
        # 「誰審的」這個問題有一個看起來像答案的答案。
        op.add_column(table, sa.Column("reviewed_by", sa.Text(), nullable=True), schema="brain")
        op.add_column(
            table,
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            schema="brain",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "reviewed_at", schema="brain")
        op.drop_column(table, "reviewed_by", schema="brain")
        op.drop_column(table, "active", schema="brain")
