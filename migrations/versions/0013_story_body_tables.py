"""主線劇情的身體側資料表（media_assets／quests／player_inventory／dialogue_turns／players_story_progress）

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-13

`citysoul_data_schema.md` §3／§4／§5 早就定義好了，但沒有任何 migration 建過。
區域主線（`citysoul-doc` 的 `story/`）需要這五張，所以一起補上。

建立順序有相依：`media_assets` → `quests` → `player_inventory`（後者的外鍵指向
前兩者）。同一支 migration 內按順序寫即可。

## ⚠️ `quest_progress.quest_id` 刻意**不加**外鍵

schema 文件 §3 寫 `quest_id TEXT NOT NULL REFERENCES quests(quest_id)`，但這裡
沒有照做，因為實作與文件在這一點上已經分歧了：

`quests.py` 的每日任務 id 是**推導出來的**（`quest_id_for_spirit()` 回傳
`f"{spirit_id}:daily"`），從來不寫進任何表。現有的 `quest_progress` 列帶著
`longshan_temple:daily` 這種 id，而 `quests` 表裡不會有對應的列。加上外鍵的
後果是每日任務**當場全部壞掉**——不是資料有點髒，是核心迴圈直接不能跑。

`quests` 表因此只放**人工撰寫的任務**（story 類型、主線的拍照任務）。要把每日
任務也納入外鍵約束，得先決定每日任務要不要有實體列，那是一次獨立的設計決定，
不該夾在建表裡順手做掉。

## `dialogue_turns` 不存座標

哪一次對話發生在哪裡，由 `spirit_id` 表達——靈魂本身就是地點。這張表帶
`player_id` 與時間，再加座標就是移動軌跡，只是換個名字。這是 CONTEXT.md
「不存移動軌跡」的硬限制，不是可以之後再補的欄位。

## `players_story_progress` 是進度的唯一真相

`dialogue_turns.story_beat_id` 只是日誌上的註記。「這個 beat 觸發了沒」永遠查
這張表，兩邊對不上時不必猜要信哪一個。

主鍵 `(player_id, beat_id)` 讓同一個 beat 天然只能記錄一次，寫入端用
`ON CONFLICT DO NOTHING` 就夠，不需要先查再寫。

## `uq_inventory_player_item` 是防重複發放的機制本身

`grant_arc_intro_document()` 的去重完全依賴這個唯一索引——玩家在圍欄範圍內來回
走動會重複觸發，靠應用層「先查有沒有再寫」在併發下會漏。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_assets",
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # 'spirit_avatar' / 'landmark_photo' / 'quest_illustration'
        # / 'player_summon_photo' / 'quest_verification_photo' / 'story_item_illustration'
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("owner_type", sa.Text(), nullable=False),  # 'system' / 'player'
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("spirit_id", sa.Text(), sa.ForeignKey("spirits.spirit_id"), nullable=True),
        sa.Column("gcs_path", sa.Text(), nullable=False),
        sa.Column("cdn_url", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # ⚠️ 沒有 EXIF 欄位，也不要加。手機照片的 EXIF 帶拍攝座標與時間，
        # 原樣保存等同建立位置紀錄；上傳流程必須在寫進 Cloud Storage 之前剝掉。
        sa.CheckConstraint("owner_type IN ('system', 'player')", name="ck_media_assets_owner_type"),
    )

    op.create_table(
        "quests",
        sa.Column("quest_id", sa.Text(), primary_key=True),
        sa.Column("spirit_id", sa.Text(), sa.ForeignKey("spirits.spirit_id"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        # 'daily' / 'story' / 'resonance_gated'
        sa.Column("quest_type", sa.Text(), nullable=False),
        sa.Column("min_resonance", sa.Integer(), nullable=False, server_default="0"),
        # 值關聯 → brain.story_beats，不建外鍵（WBS-API 決策4：身體與腦袋的表
        # 不建跨 schema 外鍵）。僅 story 類型有值。
        sa.Column("story_beat_id", sa.Text(), nullable=True),
        sa.Column("steps", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("reward_type", sa.Text(), nullable=True),  # 'resonance' / 'item'
        sa.Column("reward_value", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint(
            "quest_type IN ('daily', 'story', 'resonance_gated')", name="ck_quests_type"
        ),
    )

    op.create_table(
        "player_inventory",
        sa.Column(
            "inventory_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        # 'badge' / 'collectible' / 'story_memento' / 'story_key_item' / 'story_document'
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("item_id", sa.Text(), nullable=False),
        sa.Column(
            "source_quest_id", sa.Text(), sa.ForeignKey("quests.quest_id"), nullable=True
        ),
        sa.Column(
            "illustration_asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_assets.asset_id"),
            nullable=True,
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_inventory_player", "player_inventory", ["player_id"])
    # 見 docstring：這個索引就是防重複發放的機制，不是效能索引。
    op.create_index(
        "uq_inventory_player_item", "player_inventory", ["player_id", "item_id"], unique=True
    )

    op.create_table(
        "dialogue_turns",
        sa.Column("turn_id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            nullable=False,
        ),
        sa.Column("spirit_id", sa.Text(), sa.ForeignKey("spirits.spirit_id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),  # 'player' / 'spirit'
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("resonance_value_at_time", sa.Integer(), nullable=True),
        # 值關聯 → brain.story_beats，不建外鍵。
        sa.Column("story_beat_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # ⚠️ 沒有座標欄位，見 docstring。
        sa.CheckConstraint("role IN ('player', 'spirit')", name="ck_dialogue_turns_role"),
    )
    op.create_index(
        "idx_dialogue_player_spirit_time",
        "dialogue_turns",
        ["player_id", "spirit_id", "created_at"],
    )

    op.create_table(
        "players_story_progress",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        # 值關聯 → brain.story_beats，不建外鍵。
        sa.Column("beat_id", sa.Text(), primary_key=True),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("players_story_progress")
    op.drop_index("idx_dialogue_player_spirit_time", table_name="dialogue_turns")
    op.drop_table("dialogue_turns")
    op.drop_index("uq_inventory_player_item", table_name="player_inventory")
    op.drop_index("idx_inventory_player", table_name="player_inventory")
    op.drop_table("player_inventory")
    op.drop_table("quests")
    op.drop_table("media_assets")
