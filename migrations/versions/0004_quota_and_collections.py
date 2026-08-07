"""配額分級與相遇收藏

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07

《資料表技術文件》附錄的第三批裡**不涉及人格卡**的部分：三組純新增。
brain 三層取代 `persona_cards` 拆到 0005，理由見那支 migration 的說明。

## usage_tiers / usage_tier_limits（AC8、#32）

「一個 tier 對多筆 limit」的正規化設計，不是每種資源開一個欄位。加一種新配額
只是 `INSERT` 一筆，不用 `ALTER TABLE`、不用重新部署——`landmark_recognition_daily`
就是這個設計成立的實例。

`closed_beta` 那筆資料寫在 migration 裡而不是 seed script。`players.usage_tier_id`
是 `NOT NULL`，沒有它的資料庫連一個匿名玩家都建不出來；那是 schema 能不能運作
的前提，不是範例資料。

`is_default` 的「只能有一筆 TRUE」由 partial unique index 保證。
`BOOLEAN DEFAULT FALSE` 擋不住兩筆都是 TRUE。

計數器**不落地 Postgres**，走 Redis（見文件第6節），所以這裡沒有 `quota_usage` 表。

## encounter_collections（AC5.2）

`UNIQUE (player_id, place_id)` 是「每個地標只加一次共鳴值」的依靠，跟
`resonance_events` 同一個道理：先寫、撞到約束才知道重複，不是先查再寫。

⚠️ 這張表**不存原始照片，也不存 GPS 座標**。CONTEXT.md 的定義是「玩家、地標、
收藏時間、本機辨識結果與共鳴貢獻」——`recognized_label` 是本機辨識出來的標籤，
不是位置。加座標欄位進來就變成移動軌跡了。

## ⚠️ PostGIS 不在這一批（原本打算一起做，撞牆了）

`brain.districts` 的地理圍欄需要 PostGIS。本來想在這裡先 `CREATE EXTENSION`，
因為愈早撞到問題愈好——結果真的撞到了：

    extension "postgis" is not available
    Could not open extension control file
    "/usr/share/postgresql/16/extension/postgis.control"

`docker-compose.yml` 用的 `pgvector/pgvector:pg16` **只裝了 pgvector，沒有
PostGIS**。要兩個都有，得自己建一個映像檔（`FROM pgvector/pgvector:pg16` 再
`apt-get install postgresql-16-postgis-3`），Cloud SQL 那邊也要另外確認。

目前沒有任何表需要 PostGIS，所以不擋這一批。做 `districts` 之前必須先解決。
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CLOSED_BETA = "closed_beta"

# 封測期保守初始值。這組數字只是起點，之後直接改 usage_tier_limits 的資料
# 就好，不用重新部署——那正是這個 schema 設計的用途。
_CLOSED_BETA_LIMITS = {
    "dialogue_calls_daily": 50,
    "daily_tokens": 150_000,
    "prompt_max_chars": 500,
    "api_rate_per_minute": 6,
    "landmark_recognition_daily": 10,
}


def upgrade() -> None:
    usage_tiers = op.create_table(
        "usage_tiers",
        sa.Column("tier_id", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(64), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_usage_tiers_default",
        "usage_tiers",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    usage_tier_limits = op.create_table(
        "usage_tier_limits",
        sa.Column(
            "tier_id",
            sa.String(32),
            sa.ForeignKey("usage_tiers.tier_id"),
            primary_key=True,
        ),
        sa.Column("resource_type", sa.String(32), primary_key=True),
        sa.Column("limit_value", sa.Integer(), nullable=False),
    )

    op.bulk_insert(
        usage_tiers,
        [{"tier_id": CLOSED_BETA, "display_name": "封閉測試", "is_default": True}],
    )
    op.bulk_insert(
        usage_tier_limits,
        [
            {"tier_id": CLOSED_BETA, "resource_type": resource, "limit_value": value}
            for resource, value in _CLOSED_BETA_LIMITS.items()
        ],
    )

    # 分三步而不是一次 NOT NULL：既有玩家要先拿到一個 tier，欄位才能宣告不可為
    # NULL。刻意**不留 server_default**——「新玩家用哪個 tier」的答案是
    # `usage_tiers.is_default`，在欄位上再放一個預設值等於同一件事有兩個真相，
    # 之後改預設 tier 時一定會有人只改到其中一邊。
    op.add_column("players", sa.Column("usage_tier_id", sa.String(32), nullable=True))
    op.execute(f"UPDATE players SET usage_tier_id = '{CLOSED_BETA}'")
    op.alter_column("players", "usage_tier_id", nullable=False)
    op.create_foreign_key(
        "players_usage_tier_id_fkey", "players", "usage_tiers", ["usage_tier_id"], ["tier_id"]
    )

    op.create_table(
        "encounter_collections",
        sa.Column("collection_id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "player_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        sa.Column(
            "place_id", sa.String(64), sa.ForeignKey("spirits.spirit_id"), nullable=False
        ),
        # 本機辨識出來的標籤，不是位置。這張表沒有座標欄位，也不該有。
        sa.Column("recognized_label", sa.String(128), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("player_id", "place_id", name="uq_encounter_collections"),
    )


def downgrade() -> None:
    op.drop_table("encounter_collections")
    op.drop_constraint("players_usage_tier_id_fkey", "players", type_="foreignkey")
    op.drop_column("players", "usage_tier_id")
    op.drop_table("usage_tier_limits")
    op.drop_index("uq_usage_tiers_default", table_name="usage_tiers")
    op.drop_table("usage_tiers")
