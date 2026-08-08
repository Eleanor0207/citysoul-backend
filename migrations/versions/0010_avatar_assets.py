"""avatar_assets：Addressables catalog 與版本（#38）

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08

客戶端 F6 打包 Addressables 後上傳 Cloud Storage，F7 呼叫
`GET /assets/{avatarId}` 取得 URL 與版本，據以判斷要不要重新下載。

## 版本是這張表存在的理由

客戶端必須能問「我快取的這版還是最新的嗎」，而不是每次啟動都重抓整包。
所以 `version` 必須：**改了要變，沒改要穩定不變**。

一個每次回傳隨機值（或 `now()`）的實作也能通過「改了要變」，但那會讓客戶端
每次啟動都重抓——所以版本是一個**明確寫入的欄位**，不是從時間戳或 hash 算的。

## 為什麼進資料表而不是設定檔

資產版本會在客戶端每次發版時改變，而後端不該為了一個字串重新部署。這跟
`usage_tier_limits` 是同一個理由（見 0004）。

## URL 分成 catalog 與 bundle

Addressables 的 remote catalog 是索引，bundle 是實際內容，兩者可能放在不同
路徑（甚至不同 bucket）。合成一個欄位的話，之後要分開就是破壞性變更。
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "avatar_assets",
        sa.Column("avatar_id", sa.String(64), primary_key=True),
        sa.Column("catalog_url", sa.Text(), nullable=False),
        sa.Column("bundle_url", sa.Text(), nullable=False),
        # 明確寫入的版本識別，不是算出來的。理由見 docstring。
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("avatar_assets")
