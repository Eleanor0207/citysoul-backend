"""media_assets 加 caption：收藏放大檢視的說明文字

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-18

收藏視窗的放大檢視要在圖底下放一段說明文字（A.L. 2026-08-18）。文字量可能不少，
所以客戶端那邊是可捲動的。

## 為什麼放在 media_assets 而不是另開定義表

說明文字是**跟著那張圖走的**——它講的是這張圖畫了什麼、為什麼值得收藏，換一張圖
通常也要換一段文字。放在同一列，兩者天然一起換、一起審。

另開 `collection_definitions` 的話，圖與文字各一欄、可以分開到位（文字能比美術先寫好），
但代價是多一張要跟 `spirits` 與 `brain.story_arcs` 同步的表——而那兩張才是「有哪些
格子」的真相。A.L. 2026-08-18 選了前者。

⚠️ **代價要記著**：地標的圖還沒進 `media_assets` 時，那一格就**也不會有說明文字**，
因為根本沒有列可以掛。美術交圖之前想先把文案寫好的話，得先建一列只有 caption、
`cdn_path` 指向佔位圖的資料。

## 為什麼是 nullable

`media_assets` 不只放收藏圖——玩家上傳的照片、頭像資產都在同一張表，那些沒有
說明文字。而且收藏圖本身也允許「有圖沒文字」的中間狀態。
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("caption", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "caption")
