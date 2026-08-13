"""brain.districts 與 landmark_souls.district_id

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-13

區域地理圍欄。`citysoul_data_schema.md` §9，PostGIS 已於 0006 啟用。

## boundary 是唯一判斷依據

`center_lat` / `center_lng` / `radius_meters` **只給地圖 UI 畫概略圓形**，不參與
任何判斷。真實行政區界不是圓的——用圓形判斷會同時產生誤觸發（把隔壁區的玩家算
進來）與漏觸發（把區內邊角的玩家排除掉），而且兩種錯誤沒辦法同時調小。

萬華區的實際多邊形在 `data/districts/wanhua.geojson`（內政部界線圖，759 個頂點）。
這支 migration **只建表，不塞資料**——邊界資料屬於內容匯入，不是 schema。

## GiST 索引

`GEOGRAPHY` 欄位沒有索引時，`ST_Contains` 要逐列掃描並對每一個多邊形做完整幾何
運算。目前只有一個區，差別看不出來；但索引要在資料進來之前建好，不然之後補建
會鎖表。

## boundary 用原生 DDL 宣告，不引入 geoalchemy2

`Geography` 型別 SQLAlchemy 本身不認得。與其為了一個欄位多一個相依，這裡直接
下 `ALTER TABLE ... ADD COLUMN boundary geography(Polygon,4326)`——`districts.py`
本來就刻意走 raw SQL 做 PostGIS 運算（理由見該檔 docstring），這裡沿用同一個取捨。

## district_id 可為 NULL

`districts` 是**敘事分組標籤，不是遊戲地理的主結構**。`city → landmark` 的垂直
關係不變，分區只是額外掛上去的一層，不是每個地標都得屬於某個區。
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "districts",
        sa.Column("district_id", sa.Text(), primary_key=True),
        sa.Column(
            "city_id", sa.Text(), sa.ForeignKey("brain.city_souls.city_id"), nullable=True
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # 以下三欄僅供地圖 UI 畫概略範圍，不是判斷依據，見 docstring。
        sa.Column("center_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("center_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("radius_meters", sa.Integer(), nullable=True),
        schema="brain",
    )
    # 見 docstring：原生 DDL，不引入 geoalchemy2。
    op.execute("ALTER TABLE brain.districts ADD COLUMN boundary geography(Polygon,4326)")
    op.create_index(
        "idx_districts_boundary",
        "districts",
        ["boundary"],
        unique=False,
        postgresql_using="gist",
        schema="brain",
    )

    op.add_column(
        "landmark_souls",
        sa.Column("district_id", sa.Text(), nullable=True),
        schema="brain",
    )
    op.create_foreign_key(
        "landmark_souls_district_id_fkey",
        "landmark_souls",
        "districts",
        ["district_id"],
        ["district_id"],
        source_schema="brain",
        referent_schema="brain",
    )


def downgrade() -> None:
    op.drop_constraint(
        "landmark_souls_district_id_fkey", "landmark_souls", schema="brain", type_="foreignkey"
    )
    op.drop_column("landmark_souls", "district_id", schema="brain")
    op.drop_index("idx_districts_boundary", table_name="districts", schema="brain")
    op.drop_table("districts", schema="brain")
