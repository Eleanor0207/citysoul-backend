"""spirits 新增靈魂方位（bearing_deg / height_offset_m）

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-08

SDD v2.1 §10.2 的靈魂方位設定，供客戶端 S14 的 3DoF 定向服務使用。少了它，
城市靈魂只能永遠黏在螢幕正中央，玩家「轉動手機尋找它」的體驗不成立。

- `bearing_deg`：相對召喚點的方位角，真北 0°、順時針。
- `height_offset_m`：相對玩家視線高度的垂直偏移。

## 為什麼現在做

契約凍結點在 Phase 1。凍結之後「只加不減」原則生效，這個欄位變更就得走
`/api/v2`——**趁凍結前做是改幾行，凍結後做要開新版路由。**

## 偏離 issue #30 原文

原文寫「採冪等 ALTER 加進初始化腳本，維持與現有做法一致」，並把導入 Alembic
列為 Out of Scope 另開票。那張票（#31）**已經做完了**，Alembic 現在是這個 repo
schema 的唯一真相來源。此時再寫冪等 ALTER 等於繞過既有機制，違背 README 的
「不要用 create_all，也不要在別的地方寫 ALTER TABLE」。原文的判斷在它被寫下的
時空是對的，前提變了，所以走 migration。

## DOUBLE PRECISION 而非 NUMERIC

跟隔壁的 `latitude` / `longitude` 用 NUMERIC 不同，這兩個欄位刻意用浮點數。
經緯度是**遊戲規則的輸入**（50m 內才算在場），規則的輸入不該帶浮點誤差；方位角
只是渲染用的呈現參數，差 0.0001 度沒有任何玩家能察覺，也不會改變任何判定結果。

`NOT NULL DEFAULT 0` 讓既有的靈魂列自動取得合理值：0° 即正北、無垂直偏移。
這是相容的新增，不是破壞性變更。
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirits",
        sa.Column(
            "bearing_deg",
            sa.Float(precision=53),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "spirits",
        sa.Column(
            "height_offset_m",
            sa.Float(precision=53),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("spirits", "height_offset_m")
    op.drop_column("spirits", "bearing_deg")
