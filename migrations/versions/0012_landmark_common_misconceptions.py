"""landmark_souls.common_misconceptions

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-13

史實層多一個欄位，放「廣為流傳但經查證為錯」的說法，以及角色被問到時該怎麼講。

結構（JSONB，[{...}, ...]）：

    {
      "misconception": "蔣渭水曾被關在現存這棟建築裡",
      "correction": "蔣渭水 1931 年逝世，現存建築 1932 年動工、1933 年完工。"
                    "關過他的是第一代木造北警察署，已拆除。",
      "say_instead": "關過他的是上一代的北署，那棟已經不在了",
      "source": "文資局"
    }

## 為什麼不併進 key_events

三層設定是**整段注入 prompt**（資料量有界，不走 RAG），所以 `key_events` 裡列的
每一條，模型都會當成可以直接講的事實。

誤解是負面內容。把「蔣渭水曾被關在現存這棟建築裡」寫進事實列表，即使旁邊註明
它是錯的，模型也沒有可靠的訊號知道要否定它——很可能就直接講出來了。分成獨立
欄位，`prompt_builder` 才能用不同模板渲染：正面事實列成「你知道這些」，誤解列成
「這些是常見誤解，被問到時這樣講」。

## 為什麼 say_instead 跟 correction 都要

`correction` 是依據，說明為什麼錯；`say_instead` 是角色實際會說出口的方向。
糾正玩家很容易講成訓話，而怎麼開口正是這類條目真正的難處——「這裡不是刑場」
講得不好就是在指正對方。

## 為什麼在史實層而不是人格層

跟 `character_personas.taboos` 是兩件事：taboos 說「不談什麼」，這裡說「談的時候
不能講錯」。同一條事實更正在人格改版時不會變，放進有版本的人格表等於每改一版
就要複製一次，遲早有一版漏掉。

## 為什麼 nullable

多數地標沒有這種條目，NOT NULL 會逼所有人填一個空陣列。目前已知兩條，在
`citysoul-doc` 的 `landmark/taiwan_new_cultural_movement_memorial.md` 與
`landmark/rongjin_gorgeous_time.md`。

現在加最便宜：`landmark_souls` 目前只有一列佔位資料，11 個地標的內容還沒匯入。
等匯完再加就變成一次資料遷移。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "landmark_souls",
        sa.Column("common_misconceptions", postgresql.JSONB(), nullable=True),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_column("landmark_souls", "common_misconceptions", schema="brain")
