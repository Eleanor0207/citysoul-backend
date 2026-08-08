"""encounter_collections 補上 landmark_recognized / resonance_awarded（S12／#44）

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-08

SDD §3.1 列的欄位是 `landmark_recognized` 與 `resonance_awarded`，但 0004 建表
時只放了 `recognized_label`。兩者不是同一件事：

- `recognized_label`：裝置端本機辨識出來的**標籤字串**（0004 的設計）
- `landmark_recognized`：雲端 B13 的**判定結果**（布林），決定要不要給徽章
- `resonance_awarded`：這次收藏**有沒有真的入帳**共鳴值

第三個特別重要。`UNIQUE(player_id, place_id)` 保證一個地標只加一次共鳴值，
所以「有這一列」不等於「這次加了值」——重複收藏時列還在，但沒有入帳。少了這個
欄位，就沒有辦法從資料本身分辨那兩種情況。

兩個都 `NOT NULL DEFAULT false`：既有列（若有）代表舊流程收藏的，沒有雲端辨識
結果、也無從得知當時是否入帳，false 是誠實的預設。

⚠️ 仍然**沒有**照片、GPS 座標或影像雜湊欄位。加任何一個進來，這張表就變成
移動軌跡了（CONTEXT.md 的硬限制）。
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "encounter_collections",
        sa.Column("landmark_recognized", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "encounter_collections",
        sa.Column("resonance_awarded", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("encounter_collections", "resonance_awarded")
    op.drop_column("encounter_collections", "landmark_recognized")
