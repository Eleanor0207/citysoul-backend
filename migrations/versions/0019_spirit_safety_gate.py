"""spirits.safety_gate_enabled

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-14

哪些靈魂要在生成之前跑 B4 輸入端安全檢查。

## 為什麼是逐地標開關，不是全開

B4（`SafetyGate`）用一次 Gemini 呼叫做輸入分類，所以**開了就是每輪對話多一次
模型呼叫**——延遲加倍、成本加倍。SDD 把「AI 對話成本與延遲」列為 🔴 高風險，
現在用 `gemini-2.5-flash-lite` 而不是 `3.5-flash` 的理由正是 1 秒與 5 秒的差別。

而風險不是均勻分布的。玩家問天文館「文物該不該還給對岸」的機率，跟問故宮的
差了一個量級。全開等於為了三個地標讓十個地標一起變慢變貴。

## 為什麼開關在 spirits 而不在人格卡

人格卡有版本（`(character_id, version)`），旗標放在那裡等於每次改版都要記得
帶過去——漏一次的表現是**安全檢查靜悄悄地關掉了**，沒有任何錯誤訊息。

這個屬性也不隨人格改版而變：故宮會被問政治，跟祂這一版的說話風格無關。

## 預設 false

新增的地標預設不開，是刻意的：開了要付成本，該由人明確決定。判斷依據是研究檔
的敏感類型分類（`landmark_research_sop.md` §2），B/C/D 類值得開。

目前開的三個在 `content/spirits.yaml`：
故宮（政治）、霞海城隍廟（宗教）、臺灣新文化運動紀念館（歷史創傷＋政治）。
龍山寺同樣是宗教場所，但它的 taboos 是全專案最完整的一組，先觀察一輪再決定。
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spirits",
        sa.Column(
            "safety_gate_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
    )


def downgrade() -> None:
    op.drop_column("spirits", "safety_gate_enabled")
