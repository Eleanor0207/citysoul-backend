"""brain 的兩個 UUID 主鍵補上 gen_random_uuid() 預設值

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-13

`brain.canned_greetings.greeting_id` 與 `brain.memory_embeddings.memory_id` 在
`citysoul_data_schema.md` §7／§10 都寫成 `UUID PRIMARY KEY DEFAULT gen_random_uuid()`，
但 0005 建表時只給了 SQLAlchemy 的 `default=uuid.uuid4`——那是 **Python 端**的預設，
不是資料庫的。

## 差別在哪裡：走 ORM 沒事，走 raw SQL 就爆

`default=uuid.uuid4` 只在 SQLAlchemy 幫你組 INSERT 時才會被套用。任何不經過 ORM
的寫入——匯入腳本、psql、之後的後台工具——都會送出一個沒有 `greeting_id` 的
INSERT，然後撞 NOT NULL：

    null value in column "greeting_id" violates not-null constraint

`scripts/import_personas.py` 就是這樣撞出來的。當下可以在腳本裡自己 `uuid4()` 繞過，
但那等於每一個未來的寫入端都要記得做同一件事，而忘記做的表現是**寫入直接失敗**
——好在它吵，不是靜默錯誤，但沒有理由留著。

`public` schema 那幾張表沒有這個問題：0013 建的 `media_assets`、`player_inventory`
本來就寫了 `server_default=gen_random_uuid()`。這支 migration 是讓 `brain` 跟上。

## 為什麼是 gen_random_uuid() 而不是移除 NOT NULL

主鍵不能為 NULL。要嘛寫入端每次都給值，要嘛資料庫自己會生——後者才是文件寫的、
也才是不需要每個人記得的那個。pgcrypto 在 PostgreSQL 13 以後內建 `gen_random_uuid()`，
不需要額外 extension。
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE brain.canned_greetings "
        "ALTER COLUMN greeting_id SET DEFAULT gen_random_uuid()"
    )
    op.execute(
        "ALTER TABLE brain.memory_embeddings "
        "ALTER COLUMN memory_id SET DEFAULT gen_random_uuid()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE brain.memory_embeddings ALTER COLUMN memory_id DROP DEFAULT")
    op.execute("ALTER TABLE brain.canned_greetings ALTER COLUMN greeting_id DROP DEFAULT")
