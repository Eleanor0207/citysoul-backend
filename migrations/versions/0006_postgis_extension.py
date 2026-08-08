"""啟用 PostGIS extension

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-08

`brain.districts` 的地理圍欄需要 PostGIS 的 `GEOGRAPHY` 型別與 `ST_Contains`。
這支 migration **只啟用 extension，不建任何表**。

## 為什麼跟建表拆開

`districts` / `story_arcs` / `landmark_souls.district_id` 是 #46 明列「被擋住」
的東西，各自要另開票——它們牽涉萬華區主線劇本的內容設計，不是 schema 決定得了
的。這裡先把硬前置解掉，讓那些票開得下去。

## 為什麼 extension 進 migration 而不是只進映像檔

`Dockerfile.postgres` 讓 extension 的**檔案**存在，`CREATE EXTENSION` 才讓它在
**這個資料庫裡**生效——兩件不同的事。Cloud SQL 上沒有 Dockerfile，只有這支
migration，所以它是正式環境唯一會啟用 PostGIS 的地方。`vector` 在 0001 也是
這樣處理的，這裡沿用同一個慣例。

`IF NOT EXISTS` 是因為既有的開發資料庫可能已經手動裝過了。

## downgrade 不 DROP

`DROP EXTENSION postgis` 會連帶砍掉所有依賴它的欄位與索引。回退一支「只是啟用
extension」的 migration 不該有這種破壞力，所以 downgrade 留空——多一個沒在用的
extension 是無害的。
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")


def downgrade() -> None:
    # 刻意留空，理由見 docstring。
    pass
