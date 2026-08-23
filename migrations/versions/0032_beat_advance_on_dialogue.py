"""beat 可否由「與該靈魂對話」推進

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-23

## 為什麼需要這一欄

萬華主線有三個 gate beat（`beat_longshan_gate` / `beat_redhouse_gate` /
`beat_bopiliao_gate`），劇本寫的觸發條件是「持有某道具，**首次與某靈魂對話**」。

但 `trigger_condition` 這一欄**沒有任何程式在讀**——它只有匯入器在寫，是文件不是
邏輯。而客戶端只有年代簿會推進 beat，年代簿又只認五個章節，三個 gate 不在其中。

結果是**序章之後整條主線走不到**：第一章的前置是 longshan_gate，而沒有任何介面
能推進它。三個 gate 各卡在下一章前面，斷法完全一樣。

## 為什麼是明寫的欄位，不是推論

「沒有 required_quest_ids 又有 character_id 的就是 gate」這條規則現在剛好成立，
但它是**巧合不是契約**：之後某個 clue beat 拿掉任務要求，就會變成自動推進，
而症狀是玩家沒讀章節主線卻自己走完了——那種錯誤不會有人立刻發現。

所以由內容明講：`content/story/*.md` 的 beat 寫 `advance_on_dialogue: true`。

## 預設 false

沒寫的 beat 一律不自動推進。章節 beat（clue／reveal）要玩家在年代簿讀完劇本、
做完選擇才推進，那些選擇會寫進 `players_story_variables`——自動推進會跳過它們。
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_beats",
        sa.Column(
            "advance_on_dialogue",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("story_beats", "advance_on_dialogue", schema="brain")
