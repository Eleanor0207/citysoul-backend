"""story_beats 加上 required_quest_ids：任務完成成為推進劇情的第三道門

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-22

## 為什麼需要這一欄

萬華主線的 arc 文件（`citysoul-doc/story/wanhua_district_storyline_aming_landmark_photo_v1.md`
§12）在每個 beat 的 `trigger` 裡寫了 `quest_completed: q_longshan_repair_trace`
這類條件，但匯入器只認得 `beat_completed` 與 `has_item` 兩種——`quest_completed`
被安靜丟棄。結果是玩家**不做任何任務也能把整條主線推完**，而三個地標觀察任務
在劇情上完全沒有作用。

那個缺口沒有症狀：沒有錯誤、沒有 log、測試也不會紅，只是門沒關。

## 為什麼是欄位，不是執行期解析 trigger_condition

`trigger_condition` 那一欄已經存著整包 trigger JSON，理論上執行期解析得出來。
不那樣做的理由跟 `required_item_ids` 當初獨立成欄位一樣：

1. **匯入時就能驗**——打錯字的 quest_id 會在匯入被擋下，而不是變成一個玩家
   永遠解不開的節點
2. **查詢得出來**——「哪些 beat 依賴這個任務」是一次 SQL，不是把每一列的 JSON
   撈出來 parse
3. **跟既有的兩道門同形**——前置 beat、必要道具、必要任務三者都是 TEXT[]，
   讀的人不必記得第三種是特例

## 值關聯，不建外鍵

指向 `public.quests.quest_id`。跟 `required_item_ids` 指向 `player_inventory.item_id`
同樣不建外鍵——Postgres 沒有陣列元素外鍵，這件事只能由匯入器檢查。

既有資料一律 NULL：在這之前沒有任何 beat 依賴任務，NULL 代表「這一節不需要
完成任何任務」，跟 `required_item_ids` 的 NULL 語意一致。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_beats",
        sa.Column("required_quest_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("story_beats", "required_quest_ids", schema="brain")
