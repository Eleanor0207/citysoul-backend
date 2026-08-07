"""brain 三層人格取代 persona_cards

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07

《資料表技術文件》第7節。`brain.persona_cards` 的單張 JSONB 卡片，換成
city → landmark → character 三層具名欄位。

## 為什麼拆成三層

史實層決定角色「知道什麼」，人格層決定角色「怎麼說話」。兩者刻意分離，
避免生成時互相干擾。city 基調可以被同一座城市的所有靈魂共用，不需要在每張
人格卡裡重抄一遍。

三層都是靜態設定資料，資料量有界，**不做向量檢索**——整段注入 prompt 更穩定
也更省成本。RAG 是給 `memory_embeddings` 那種會無限成長的東西用的。

## 版本管理與人工審核原樣保留

`persona_cards` 有 `version` / `reviewed_by` / `reviewed_at` / `is_active`，
文件草稿的 `character_personas` 只有一個 `active` 和 `updated_at`——那是就地
覆寫，沒有歷史、沒有審核人記錄，出事時無法回溯是誰在什麼時候改的。那不是舊
設計的包袱，是人格內容能不能上線的機制，所以整組帶過來了。

改人格 = `INSERT` 一筆新 `version`，舊版留著；審核通過才把 `active` 換過去。
「一個角色同時只能有一個生效版本」由 partial unique index 保證。

🔒 **程式碼裡沒有任何路徑會把 `active` 設成 true。**

## brain.characters 為什麼要獨立一張表

人格有版本，主鍵是 `(character_id, version)`。但 `spirits`、`story_beats`、
`resonance_unlockables` 要指的是「這個角色」而不是「這個角色的第 3 版」，
需要一個穩定的單欄位主鍵可以引用。

## canned_greetings 獨立成表（不是塞回 JSONB）

B12 快速問候（AC7.1）原本住在 `persona_cards.content["canned_greetings"]`。
三層攤成具名欄位之後它沒有位置——這是把人格卡拆開時真正的缺口，不是細節。

做成獨立的表而不是一個 JSONB 欄位，因為它本來就是一對多。附帶的好處是
`response_text NOT NULL` 與 `trigger_phrases TEXT[]` 讓「某一筆格式打錯」
這種狀態在資料庫層級就不可能存在——`greetings.py` 裡那一整段防禦性解析
因此可以拿掉。

外鍵指向 `(character_id, version)` 而不是只有 `character_id`：預寫台詞是人工
審核過的內容，改台詞就是改人格內容，應該走「新版本 → 重新審核」的同一條路，
不能繞過去。

## spirits 接上角色

`spirits.character_id` / `landmark_id` 是**值關聯，不建外鍵**（WBS-API 決策4：
身體與腦袋的表不建跨 schema 外鍵）。`UNIQUE(character_id)` 強制一個地標靈魂
只有一種人格；未來要支援人格變體時移除這個約束、改中介表。

## 沒有做的部分

`brain.districts` 與 `story_arcs` 需要 PostGIS，而本機的
`pgvector/pgvector:pg16` 映像檔沒有裝（見 0004 的說明）。`landmark_souls`
因此還沒有 `district_id` 欄位——那是之後 `ALTER TABLE` 加一個可為 NULL 的
欄位，不影響這裡的結構。

## 資料搬移

**沒有自動搬移。** 現有唯一的一張人格卡是龍山寺的 version 1，`is_active=False`
的未審核草稿，內容大半是 `PENDING_NARRATIVE_REVIEW` 佔位字串。把佔位字串
機械式地搬進具名欄位，只會產生一份看起來已經填好、其實沒有的資料。seed script
會用三層的形狀重新建立那張草稿，一樣是 `active=False`。

`persona_cards` 在這支 migration 裡 drop 掉。要保留舊內容的話，downgrade 會
把表建回來，但**不會**把資料變回去——這是單向的。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TEXT_ARRAY = postgresql.ARRAY(sa.Text())


def upgrade() -> None:
    op.create_table(
        "city_souls",
        sa.Column("city_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("macro_history_summary", sa.Text(), nullable=False),
        sa.Column("core_tone_descriptors", _TEXT_ARRAY, nullable=True),
        sa.Column("shared_values", _TEXT_ARRAY, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="brain",
    )

    op.create_table(
        "landmark_souls",
        sa.Column("landmark_id", sa.String(64), primary_key=True),
        sa.Column(
            "city_id", sa.String(64), sa.ForeignKey("brain.city_souls.city_id"), nullable=False
        ),
        sa.Column("name", sa.String(128), nullable=False),
        # [{year, event, detail}, ...]
        sa.Column("founding_facts", postgresql.JSONB(), nullable=False),
        sa.Column("key_events", postgresql.JSONB(), nullable=True),
        sa.Column("cultural_significance", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="brain",
    )

    # 角色身分層：穩定的單欄位主鍵，讓其他表有東西可以指。
    op.create_table(
        "characters",
        sa.Column("character_id", sa.String(64), primary_key=True),
        sa.Column(
            "landmark_id",
            sa.String(64),
            sa.ForeignKey("brain.landmark_souls.landmark_id"),
            nullable=False,
        ),
        schema="brain",
    )

    op.create_table(
        "character_personas",
        sa.Column(
            "character_id",
            sa.String(64),
            sa.ForeignKey("brain.characters.character_id"),
            primary_key=True,
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("archetype", sa.Text(), nullable=False),
        sa.Column("speech_style", sa.Text(), nullable=False),
        sa.Column("personality_traits", _TEXT_ARRAY, nullable=True),
        sa.Column("values", _TEXT_ARRAY, nullable=True),
        # 安全下限：敘事審查只能往上加，不能移除既有條目。
        sa.Column("taboos", _TEXT_ARRAY, nullable=True),
        # 負面人格聲明（「不是廟方人員，不是神明本身」）。跟 taboos 是兩件事：
        # taboos 說「不談什麼」，這裡說「不是誰」。宗教場域尤其需要。
        sa.Column("not_this_character", sa.Text(), nullable=True),
        # 虛構授權：明確說明神祕感的來源，以及不能宣稱什麼。
        sa.Column("imagination_license", sa.Text(), nullable=True),
        sa.Column("quest_themes", _TEXT_ARRAY, nullable=True),
        sa.Column("tone_override", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="brain",
    )
    op.create_index(
        "uq_character_personas_active",
        "character_personas",
        ["character_id"],
        unique=True,
        schema="brain",
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "canned_greetings",
        sa.Column("greeting_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("character_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger_phrases", _TEXT_ARRAY, nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["character_id", "version"],
            ["brain.character_personas.character_id", "brain.character_personas.version"],
            name="canned_greetings_persona_fkey",
        ),
        # 空台詞與沒有觸發語的台詞都是無意義的資料列。NOT NULL 擋不住空字串
        # 與空陣列，這兩條 CHECK 才擋得住——讓壞狀態寫不進來，比在讀取端
        # 反覆檢查可靠。
        sa.CheckConstraint("response_text <> ''", name="ck_canned_greetings_response"),
        sa.CheckConstraint(
            "cardinality(trigger_phrases) > 0", name="ck_canned_greetings_triggers"
        ),
        schema="brain",
    )
    op.create_index(
        "ix_canned_greetings_persona",
        "canned_greetings",
        ["character_id", "version"],
        schema="brain",
    )

    # spirits 接上角色。值關聯，不建跨 schema 外鍵。
    op.add_column("spirits", sa.Column("character_id", sa.String(64), nullable=True))
    op.add_column("spirits", sa.Column("landmark_id", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_spirits_character_id", "spirits", ["character_id"])

    op.drop_table("persona_cards", schema="brain")


def downgrade() -> None:
    op.create_table(
        "persona_cards",
        sa.Column("spirit_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="brain",
    )
    # 表回來了，資料不會。搬移是單向的，見 docstring。

    op.drop_constraint("uq_spirits_character_id", "spirits", type_="unique")
    op.drop_column("spirits", "landmark_id")
    op.drop_column("spirits", "character_id")

    op.drop_table("canned_greetings", schema="brain")
    op.drop_table("character_personas", schema="brain")
    op.drop_table("characters", schema="brain")
    op.drop_table("landmark_souls", schema="brain")
    op.drop_table("city_souls", schema="brain")
