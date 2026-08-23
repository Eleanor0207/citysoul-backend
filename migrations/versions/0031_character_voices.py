"""角色嗓音：每隻靈魂的 TTS 嗓音、語速與音高

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-23

## 為什麼需要這張表

`settings.tts_voice_name` 是**全域一個值**，等於九隻靈魂共用同一個聲音，
而且跟角色外型無關——中年男性的龍山寺與少女造型的天文館講起話來一模一樣。

## 為什麼不加在既有的表上

**不加 `brain.characters`**：那張表的類別註解明講「只有識別與歸屬，沒有內容」。
嗓音是內容，加進去就破壞了那個不變量。

**不加 `brain.character_personas`**：那張表是版本化的，`active` 只能由人工審核
流程 flip（見該類別的 🔒 註解）。而 `pitch` 是要靠耳朵反覆試的參數，每動一次
就開新版本＋重新審核太重，而且會把「人格內容改過」與「聲音調過」混進同一段歷史。

## 兩個 CHECK 是 Google API 的合法範圍

`speaking_rate` 0.25–4.0、`pitch` −20.0–20.0 半音。

擋在資料庫層而不是只在匯入腳本檢查，是因為超範圍的值會讓 TTS 在**執行期**失敗，
而 TTS 失敗是安靜降級成純文字的（見 `brain/tts.py` 的模組註解）——沒有人會發現
聲音消失了。擋在寫入時，錯誤才會在匯入當下就看得到。

## 不存 language_code

實測 `zh-TW` 搭 `cmn-TW-*` 的嗓音名稱 Google 接受，不必兩個欄位一起維護。
語言碼繼續走 `settings.tts_language_code`。

## 沒有資料的靈魂

這張表**允許缺列**。查不到就退回全域預設，也就是這次改動之前的行為。
「還沒配音」與「配成預設值」要分得開，所以匯入器不會替沒寫 `voice:` 的靈魂
補一列預設值。
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "character_voices",
        sa.Column(
            "character_id",
            sa.String(64),
            sa.ForeignKey("brain.characters.character_id"),
            primary_key=True,
        ),
        sa.Column("voice_name", sa.Text, nullable=False),
        sa.Column("speaking_rate", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("pitch", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "speaking_rate BETWEEN 0.25 AND 4.0",
            name="ck_character_voices_rate",
        ),
        sa.CheckConstraint(
            "pitch BETWEEN -20.0 AND 20.0",
            name="ck_character_voices_pitch",
        ),
        schema="brain",
    )


def downgrade() -> None:
    op.drop_table("character_voices", schema="brain")
