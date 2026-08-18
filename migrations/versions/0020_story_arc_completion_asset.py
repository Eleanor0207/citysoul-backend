"""brain.story_arcs 加 completion_asset_id：劇本完成圖（收藏視窗）

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-18

收藏視窗（圖鑑）有兩種格子：每個地標一張「初次相遇圖」，每條 arc 一張「劇本完成圖」。
前者靠 `media_assets.spirit_id` 就指得到，後者沒有任何欄位指得到——這支 migration
補的就是那一條線。

## 為什麼加在 story_arcs，而不是在 media_assets 加 arc_id

兩個方向都會通，但 `story_arcs.intro_document_asset_id`（0015）已經立了先例：
**arc 指向素材，不是素材指向 arc。** 照抄那一條有三個好處：

1. 方向一致。讀 schema 的人不必記「信件是這樣指、完成圖是那樣指」兩套規則。
2. `media_assets` 不會多一個對 99% 的列都是 NULL 的欄位。
3. **「一條 arc 只有一張完成圖」變成欄位天然保證的事。** 反過來放的話，兩列
   `media_assets` 可以同時宣稱屬於同一條 arc，資料庫擋不住，只能靠應用層自律。

值關聯 → `public.media_assets.asset_id`，**不建跨 schema 外鍵**（WBS-API 決策4），
跟 `intro_document_asset_id` 同一條規矩。

## 沒有一起加「有幾格」的定義表

收藏格數完全可以從既有的表推導：相遇格 = `spirits` 裡 `is_active=true` 的列，
劇本格 = `brain.story_arcs` 的列。多一張定義表就多一個要跟這兩張同步的地方，
而它們本來就是真相。

這也順帶處理了霞海城隍廟：它 `is_active=false`（08-16 起，唯一沒有近景文件的
地標），`/summon` 對它回 404，所以它天然不在清單裡、也拿不到圖——不需要任何特例。
之後補了近景文件、`is_active` 轉回 true，那一格就自己長出來。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_arcs",
        # 值關聯 → public.media_assets.asset_id，不建跨 schema 外鍵。
        sa.Column("completion_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("story_arcs", "completion_asset_id", schema="brain")
