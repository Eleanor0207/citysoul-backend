"""spirits 新增方位設定（issue #30）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07

SDD v2.1 §10.2：`spirits` 新增 `bearing_deg`（相對召喚點的方位角，真北 0°、
順時針）與 `height_offset_m`，供 S14 3DoF 定向服務使用。`GET /spirits/{placeId}`
回應相應新增巢狀 `orientation` 物件。

⚠️ **偏離 issue #30 原文的一處**：原 spec 寫「目前建表走
`Base.metadata.create_all`，這裡採冪等 ALTER（`ADD COLUMN IF NOT EXISTS`），
導入 Alembic 是獨立工作、另開票」。那份 spec 是在 Alembic 導入（#31，
migration 0001）**之前**寫的，現在 Alembic 已經是這個 repo 的 schema 唯一
真相來源（見 0001 baseline 與後續五支 migration），用冪等 ALTER 反而是
繞過現有機制、走一條這個 repo 已經淘汰的路。這裡改用正常的 Alembic
migration，跟 0002～0007 的做法一致。

`DOUBLE PRECISION`（issue #30 明訂），不是 `spirits.latitude/longitude`
用的 `NUMERIC`——這兩個值驅動視覺呈現，不是像 haversine 那樣拿來做
「50m 內才算在場」的規則判斷，浮點誤差在這裡不影響任何遊戲規則。
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirits",
        sa.Column("bearing_deg", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "spirits",
        sa.Column("height_offset_m", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("spirits", "height_offset_m")
    op.drop_column("spirits", "bearing_deg")
