"""玩家在劇情任務的步驟進度

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-23

## 為什麼需要這張表

劇情任務（`quests.quest_type = 'story'`）的 `steps` 是 JSONB，每一步有
`step_id` / `title` / `hint`（例如「屋脊上的痕跡」）。但**沒有任何地方存
「哪一步做完了」**——`quest_progress` 只有 `progress_value` 一個整數，
而且沒有任何程式在寫它。

結果是劇情任務只能整個完成或整個不完成，而三步做完兩步是**哪兩步**，
一個計數答不出來。對話要指示「還差哪一步」，就必須知道是哪一步。

## 為什麼不是 quest_progress.progress_value

同上：計數答不出「哪一步」。而且步驟是集合語意（做過就做過，不會退回），
用一列一步表達，重複提交靠主鍵去重，不需要先查再寫。

## 沒有 FK 指向 steps

`steps` 在 JSONB 裡，沒有可以指的表。`step_id` 的有效性由寫入端檢查
（該任務的 steps 裡查不到就 404）——這一層在資料庫做不到，也不值得為它
把 steps 拆成一張表：內容改版時整包覆寫比逐列 diff 簡單得多。

## 全部做完就自動完成任務

不另外存「任務完成」——那是 `quest_progress.status` 的事。步驟寫入端在最後
一步落地之後，同一次呼叫把任務標成完成。兩個地方各記一份「做完了沒」，
遲早會不一致。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_quest_steps",
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("players.player_id"),
            primary_key=True,
        ),
        sa.Column("quest_id", sa.String(64), primary_key=True),
        sa.Column("step_id", sa.String(64), primary_key=True),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # 讀取模式一定是「這個玩家在這個任務上做完了哪些步驟」。
    op.create_index(
        "idx_player_quest_steps_lookup",
        "player_quest_steps",
        ["player_id", "quest_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_player_quest_steps_lookup", table_name="player_quest_steps")
    op.drop_table("player_quest_steps")
