"""spirits 欄位改名與座標型別對齊

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07

《資料表技術文件》附錄遷移清單的第一批：只做改名與型別，不動任何結構。
刻意跟後面的結構調整分開——這批是機械式的，出事的話一眼看得出是哪裡。

改名對照：

    place_id           → spirit_id
    name               → display_name
    summon_radius_m    → summon_radius_meters
    sense_radius_m     → sense_radius_meters

改名的理由是文件裡十幾張表的外鍵都拼 `spirit_id`，只有這張表的主鍵叫
`place_id`；少數服從多數。而且 `place_id` 現在有歧義——Google Place ID
也叫這個名字。

**API 的 wire contract 完全不變。** 路徑仍是 `/spirits/{placeId}`，
`SpiritResponse` 仍回 `place_id` / `summon_radius_m` / `sense_radius_m`——
Unity client 的 DTO 寫死了那些 JSON key，而資料庫欄位名跟對外欄位名沒有
必須一致的理由。對應處理在 `schemas.SpiritResponse` 的 validation_alias。

用 `ALTER TABLE ... RENAME COLUMN` 而不是「新增欄位、搬資料、刪舊欄位」：
rename 是 metadata-only 操作，不重寫資料、不會漏搬、不需要停機。外鍵約束
（resonance / resonance_events 指向 spirits）會被 PostgreSQL 自動跟著更新，
不需要重建。
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_RENAMES = [
    ("place_id", "spirit_id"),
    ("name", "display_name"),
    ("summon_radius_m", "summon_radius_meters"),
    ("sense_radius_m", "sense_radius_meters"),
]


def upgrade() -> None:
    for old, new in _RENAMES:
        op.alter_column("spirits", old, new_column_name=new)

    # 座標從 double precision 改成 NUMERIC(9,6)。
    #
    # 距離判斷是遊戲規則的一部分（50m 內才算在場），規則的輸入值不應該帶
    # 浮點誤差。9 位總長／6 位小數足以表示地球上任何一點到約 11 公分。
    #
    # 現有資料是 25.0373983（7 位小數），轉換時會四捨五入成 25.037398——
    # 差距約 3 公分，遠小於 GPS 本身的誤差，也遠小於 50m 的半徑。
    for column in ("latitude", "longitude"):
        op.alter_column(
            "spirits",
            column,
            type_=sa.Numeric(9, 6),
            existing_type=sa.Float(),
            existing_nullable=False,
            postgresql_using=f"{column}::numeric(9,6)",
        )


def downgrade() -> None:
    for column in ("latitude", "longitude"):
        op.alter_column(
            "spirits",
            column,
            type_=sa.Float(),
            existing_type=sa.Numeric(9, 6),
            existing_nullable=False,
            postgresql_using=f"{column}::double precision",
        )

    for old, new in _RENAMES:
        op.alter_column("spirits", new, new_column_name=old)
