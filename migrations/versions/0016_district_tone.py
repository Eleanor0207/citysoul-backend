"""brain.districts 加基調欄位

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-13

行政區基調素材：`core_tone_descriptors` / `shared_values` / `macro_history_summary`，
欄位與 `brain.city_souls` 對應的三欄同名同型別。

## 為什麼加在行政區而不是塞進 city_souls

10 份地標研究檔**全部寫了自己那一區的基調，沒有一份寫城市層的**。那不是偷懶：
「臺北的基調」抽象到寫不出有用的東西，而區級的差異是真的會改變講話方式——

    萬華   市井、信仰、包容、歲月韌性、煙火氣
    西門   次文化聚落、流行潮流、新舊混血、多元包容、展演劇場

兩地走路十分鐘，塞進同一列 `city_souls` 會被平均掉。內容寫成什麼樣子，是這一層
放錯高度的證據。

## ⚠️ 這一層目前不接進 prompt

`prompt_builder` 沒有讀 `districts`（實際上它也還沒讀 `city_souls` 與
`landmark_souls`）。所以這支 migration 給區級基調一個正確的家，**執行期成本為零**。

刻意分開做的理由：每多一層注入，每一次對話都多付一段 token，而且多一個彼此矛盾
的地方。目前首發只有萬華三個地標——同一個區，多一層跟少一層講出來的話不會有差別。
等第二個區真的上線、能實際比較生成結果時，再決定覆蓋順序（初步方向是
character > district > city），那時才有東西可以驗。

## 這改變了 districts 的定位

0014 的 docstring 說 `districts` 是「敘事分組標籤，不是遊戲地理的主結構」。加上
基調之後它同時是**地理圍欄**與**敘事層**——後半是新的。`city → landmark` 的垂直
關係仍然不變，`landmark_souls.district_id` 仍可為 NULL。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "districts",
        sa.Column("core_tone_descriptors", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="brain",
    )
    op.add_column(
        "districts",
        sa.Column("shared_values", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="brain",
    )
    op.add_column(
        "districts",
        sa.Column("macro_history_summary", sa.Text(), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("districts", "macro_history_summary", schema="brain")
    op.drop_column("districts", "shared_values", schema="brain")
    op.drop_column("districts", "core_tone_descriptors", schema="brain")
