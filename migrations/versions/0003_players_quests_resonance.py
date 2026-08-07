"""players 綁定欄位、quest_progress 主鍵、移除 resonance.stage

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07

《資料表技術文件》附錄遷移清單的第二批：三張身體模組的表的結構調整。

## players：匿名仍然是預設，登入是附加資訊

文件草稿原本把 `auth_provider` 寫成 `NOT NULL`，那會讓匿名玩家在資料庫層級
無法存在——而 S6 的整個設計就是「不登入就能玩」。所以：

- `device_id` 是身分，`NOT NULL UNIQUE`（0001 就是這樣，不動）
- 登入相關的四個欄位全部可為 NULL
- `CHECK ((auth_provider IS NULL) = (auth_provider_id IS NULL))` 擋掉
  「有 provider 沒有 id」的半綁定狀態
- 唯一性用 **partial unique index**，不是表上的 `UNIQUE(...)`。匿名玩家全都是
  `(NULL, NULL)`，而 Postgres 的 UNIQUE 不擋重複 NULL——那樣寫不會報錯，
  但也就完全沒有在保護任何東西。

`account_id` 改名為 `auth_provider_id`：它就是「provider 那邊的使用者 id」，
只是少了「哪一家 provider」這半邊資訊。目前全欄位都是 NULL（綁定流程還不
存在），所以這是零風險的改名。對外 JSON 仍然叫 `account_id`（client 的
`PlayerDto` 寫死了），對應在 `schemas.PlayerResponse`。

`usage_tier_id` **不在這一批**。它是 `NOT NULL REFERENCES usage_tiers`，
必須等 0004 把 `usage_tiers` 連同 `closed_beta` 那筆資料一起建好才能加。

## quest_progress：換成代理主鍵

複合主鍵 `(player_id, quest_id)` 表達不了「同一個玩家同一個任務、不同天各一
筆」的每日任務，而 `quest_photo_submissions`（之後才建）也需要一個單一欄位
可以指。

唯一性改由兩個 partial unique index 保證。既有資料列的 `issued_date` 是 NULL，
落在 `uq_quest_progress_onetime` 也就是 `(player_id, quest_id)` 上——**跟舊的
複合主鍵完全等價**，所以現有的狀態機邏輯不需要跟著改。

文件裡 `quest_progress` 還有 `step_states` / `current_step_index` /
`expires_at` 等欄位，那些屬於 `quests` 目錄表那一整套設計，這裡不做。

## resonance：刪掉 stage 欄位

`stage` 是 `resonance_value` 跨過 10/40/100 之後的結果。`stage_for_value()`
每次都從 value 重算、從不讀這個欄位——程式碼裡的註解也明講「不信任它」。
一個永遠不被信任的快取欄位，存在的唯一效果是讓下一個人誤用它。
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── players ──────────────────────────────────────────────────────────
    op.alter_column("players", "account_id", new_column_name="auth_provider_id")
    op.add_column("players", sa.Column("auth_provider", sa.String(32), nullable=True))
    op.add_column("players", sa.Column("display_name", sa.String(64), nullable=True))
    op.add_column("players", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.add_column(
        "players",
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "players",
        sa.Column("total_summons", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "players",
        sa.Column("notification_opt_in", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_check_constraint(
        "ck_players_auth_pair",
        "players",
        "(auth_provider IS NULL) = (auth_provider_id IS NULL)",
    )
    op.create_index(
        "uq_players_auth",
        "players",
        ["auth_provider", "auth_provider_id"],
        unique=True,
        postgresql_where=sa.text("auth_provider IS NOT NULL"),
    )

    # ── quest_progress ───────────────────────────────────────────────────
    op.add_column("quest_progress", sa.Column("issued_date", sa.Date(), nullable=True))
    op.drop_constraint("quest_progress_pkey", "quest_progress", type_="primary")
    op.add_column(
        "quest_progress",
        sa.Column("progress_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_primary_key("quest_progress_pkey", "quest_progress", ["progress_id"])
    op.create_index(
        "uq_quest_progress_onetime",
        "quest_progress",
        ["player_id", "quest_id"],
        unique=True,
        postgresql_where=sa.text("issued_date IS NULL"),
    )
    op.create_index(
        "uq_quest_progress_daily",
        "quest_progress",
        ["player_id", "quest_id", "issued_date"],
        unique=True,
        postgresql_where=sa.text("issued_date IS NOT NULL"),
    )

    # ── resonance ────────────────────────────────────────────────────────
    op.drop_column("resonance", "stage")


def downgrade() -> None:
    # stage 的值可以從 resonance_value 重算，所以退回去不會遺失資訊。
    op.add_column(
        "resonance", sa.Column("stage", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(
        """
        UPDATE resonance SET stage =
            (resonance_value >= 10)::int
          + (resonance_value >= 40)::int
          + (resonance_value >= 100)::int
        """
    )

    op.drop_index("uq_quest_progress_daily", table_name="quest_progress")
    op.drop_index("uq_quest_progress_onetime", table_name="quest_progress")
    op.drop_constraint("quest_progress_pkey", "quest_progress", type_="primary")
    op.drop_column("quest_progress", "progress_id")
    op.create_primary_key("quest_progress_pkey", "quest_progress", ["player_id", "quest_id"])
    op.drop_column("quest_progress", "issued_date")

    op.drop_index("uq_players_auth", table_name="players")
    op.drop_constraint("ck_players_auth_pair", "players", type_="check")
    op.drop_column("players", "notification_opt_in")
    op.drop_column("players", "total_summons")
    op.drop_column("players", "last_active_at")
    op.drop_column("players", "avatar_url")
    op.drop_column("players", "display_name")
    op.drop_column("players", "auth_provider")
    op.alter_column("players", "auth_provider_id", new_column_name="account_id")
