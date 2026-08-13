"""brain.story_arcs／story_beats／resonance_unlockables

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-13

主線劇情的腦袋側資料表。`citysoul_data_schema.md` §8／§9。

## 這三張表沒有審核欄位，這是已知的缺口

`character_personas` 有 `active` / `reviewed_by` / `reviewed_at`，改人格要 append
新版本並重新審核。`story_beats.narrative_directive` **同樣會注入 prompt**，內容
治理風險與人格卡同級，但 schema 文件的設計裡沒有這三個欄位——匯入就直接生效。

這裡照文件建，不自作主張加欄位。在補上之前，審核只能靠流程：Lead 過目 → 才產
YAML → 才跑匯入器。要不要補是待決事項，見 `citysoul-doc` 的
`story/wanhua_district_storyline_aming_v0.2.md` §9.2。

## prerequisite_beat_ids 是陣列，資料庫擋不住它的錯

`TEXT[]` 沒辦法宣告「每個元素都必須是存在的 `beat_id`」——Postgres 沒有陣列元素
外鍵。所以三件事一定要由匯入器檢查，不能指望資料庫：

1. **無懸空引用**：打錯一個字，那個 beat 永遠解不開，而且沒有任何錯誤訊息。
2. **無環**：A 需要 B、B 需要 A，兩個都永遠觸發不了。
3. **無孤島**：每個 beat 都要從某個無前置的起點走得到。

三種壞掉的方式都是**靜默**的：玩家卡住，log 乾淨，測試也不會紅。

## sequence_order 的唯一性是 (arc_id, character_id, sequence_order)

MVP 採單一角色內的線性排序（§8），所以同一條 arc 裡三個地標的第一個 beat 都是
`sequence_order = 1`，這是對的。跨角色的先後由 `prerequisite_beat_ids` 表達，
不由這個數字表達——寫成 `(arc_id, sequence_order)` 唯一會把正確的劇本擋掉。

## character_id 是真外鍵，其餘是值關聯

`story_beats.character_id` → `brain.characters` 在同一個 schema 內，建實體外鍵。
`required_item_ids` 指向 `public.player_inventory.item_id`、`story_arcs`
的 `intro_document_asset_id` 指向 `public.media_assets` 都是**跨 schema**，依
WBS-API 決策4 只用值關聯，不建外鍵。

## resonance_unlockables.content 放的是敘事指令，不是成品台詞

欄位名叫 `content` 很容易誤解。它跟 `story_beats.narrative_directive` 是同一類
東西——描述「要講什麼、什麼語氣」，由 B11 據此生成字句。寫成成品台詞會讓三次
解鎖聽起來像罐頭，而且繞過語氣層與 relationship_stage 的深度限制。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_arcs",
        sa.Column("arc_id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "district_id", sa.Text(), sa.ForeignKey("brain.districts.district_id"), nullable=True
        ),
        sa.Column("intro_document_title", sa.Text(), nullable=True),
        sa.Column("intro_document_content", sa.Text(), nullable=True),
        # 值關聯 → public.media_assets.asset_id，不建跨 schema 外鍵。
        sa.Column("intro_document_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        schema="brain",
    )

    op.create_table(
        "story_beats",
        sa.Column("beat_id", sa.Text(), primary_key=True),
        sa.Column("arc_id", sa.Text(), sa.ForeignKey("brain.story_arcs.arc_id"), nullable=True),
        sa.Column(
            "character_id",
            sa.Text(),
            sa.ForeignKey("brain.characters.character_id"),
            nullable=True,
        ),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        # 描述性文字。實際判斷邏輯在 body 狀態機，而狀態機只能查資料庫狀態——
        # 任何需要理解玩家自然語言才能判斷的條件都不能寫進這一欄。
        sa.Column("trigger_condition", sa.Text(), nullable=False),
        sa.Column("narrative_directive", sa.Text(), nullable=False),
        # 見 docstring：資料庫擋不住懸空引用與環，靠匯入器。
        sa.Column("prerequisite_beat_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        # 值關聯 → public.player_inventory.item_id。
        sa.Column("required_item_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("contingency_notes", sa.Text(), nullable=True),
        sa.Column("one_time", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # 見 docstring：唯一性含 character_id，不能只有 (arc_id, sequence_order)。
        sa.UniqueConstraint(
            "arc_id", "character_id", "sequence_order", name="uq_story_beats_sequence"
        ),
        schema="brain",
    )
    op.create_index("ix_story_beats_arc", "story_beats", ["arc_id"], schema="brain")

    op.create_table(
        "resonance_unlockables",
        sa.Column("unlock_id", sa.Text(), primary_key=True),
        sa.Column(
            "character_id",
            sa.Text(),
            sa.ForeignKey("brain.characters.character_id"),
            nullable=True,
        ),
        # 門檻沿用固定的 10 / 40 / 100（AC5.3），不為個別角色另訂數值。
        sa.Column("min_resonance", sa.Integer(), nullable=False),
        sa.Column("unlock_type", sa.Text(), nullable=False),  # 'memory_fragment' / 'secret_story'
        # 見 docstring：這是敘事指令，不是成品台詞。
        sa.Column("content", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "character_id", "min_resonance", "unlock_type", name="uq_resonance_unlockables"
        ),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_table("resonance_unlockables", schema="brain")
    op.drop_index("ix_story_beats_arc", table_name="story_beats", schema="brain")
    op.drop_table("story_beats", schema="brain")
    op.drop_table("story_arcs", schema="brain")
