"""劇情變數與資訊卡：結局分歧的儲存位置，以及卡片定義進資料庫

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-22

三件事，同一條主線的三個缺口（backend#72）。

## 1. `players_story_variables`：玩家選了什麼

萬華 arc 文件 §7.1 定義了三個變數——`story_focus`（信件開場看了哪個位置）、
`reveal_lens`（剝皮寮揭露時怎麼理解）、`ending_mark`（結局把畫記給誰）——
它們決定個人化年代簿那一頁的措辭。

**先前完全沒有儲存位置。** 節點資料裡有 `set_once: {story_focus: person}`，
但沒有任何表能放它，所以玩家選了什麼在後端是不存在的資訊。

主鍵 `(player_id, arc_id, variable)` 直接表達 `set_once` 的語意：**一個變數
一條 arc 只能有一個值**。寫入端用 `ON CONFLICT DO NOTHING`，第一次寫進去的
就是最終值——文件 §2.3 明訂「`story_focus` 由第一個看的位置寫入，之後回看
不覆蓋」。這條規則因此由資料庫保證，不是靠呼叫端記得。

## 2. `brain.story_info_cards`：卡片定義

arc 文件 §12 的 `info_cards:` 區塊定義了三張卡（史實一段、虛構一段），文字
早就在 `brain.story_strings` 裡（`wanhua.card.*`），但**卡片本身從來沒有進
資料庫**——匯入器只寫 `story_arcs` 與 `story_beats`。結果是 beat 的
`show_info_card: card_longshan_rebuild` 指向一個查不到的東西。

`historical_text_key` 與 `fiction_text_key` 分成兩欄而不是一段合併文字：
文件 §1 要求每張卡明確區分「史實可考」與「本作故事」，合成一欄之後那條界線
就只剩下排版慣例。

## 3. `story_arcs.variables`：每個變數的合法值

文件 §12 的 `variables:` 區塊列了每個變數可以是哪些值。存進來之後，寫入端
才驗得了「`story_focus` 只能是 person／history／home」——否則客戶端送什麼
就存什麼，而錯字要等到結局那一頁措辭不對才會發現。

JSONB 而不是另一張表：它是 arc 的設定，不是可查詢的實體，而且整包一起讀寫。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_arcs",
        sa.Column("variables", postgresql.JSONB(), nullable=True),
        schema="brain",
    )

    op.create_table(
        "story_info_cards",
        sa.Column("card_id", sa.String(64), primary_key=True),
        sa.Column(
            "arc_id",
            sa.String(64),
            sa.ForeignKey("brain.story_arcs.arc_id"),
            nullable=True,
        ),
        # 值關聯 → brain.story_strings.text_key。不建外鍵的理由同 story_beats
        # 的其他 text 引用：匯入器負責檢查，資料庫擋不住陣列與跨批次的順序。
        sa.Column("historical_text_key", sa.Text(), nullable=True),
        sa.Column("fiction_text_key", sa.Text(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=True),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        schema="brain",
    )
    op.create_index(
        "ix_story_info_cards_arc", "story_info_cards", ["arc_id"], schema="brain"
    )

    op.create_table(
        "players_story_variables",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        # 值關聯 → brain.story_arcs.arc_id，不建跨 schema 外鍵（WBS-API 決策4）。
        sa.Column("arc_id", sa.String(64), primary_key=True),
        sa.Column("variable", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        # 哪一個節點寫的。只是稽核資訊——判定永遠看 value 本身。
        sa.Column("set_by_beat_id", sa.Text(), nullable=True),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("players_story_variables")
    op.drop_index("ix_story_info_cards_arc", table_name="story_info_cards", schema="brain")
    op.drop_table("story_info_cards", schema="brain")
    op.drop_column("story_arcs", "variables", schema="brain")
