"""landmark_events：地標的官方公開活動（B9 白名單第二類的來源）

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-18

B9（`brain/daily_event.py`）的 `DailyEventInputs.official_events` 從第一天就存在，
但一直沒有人填。這張表就是它的來源：由 `scripts/fetch_landmark_events.py` 從
文化部 iCulture 開放資料抓進來，經人工審核後餵給 S10 的排程。

## 為什麼在 public schema 而不是 brain

它是「要不要推給玩家看」的呈現層資料，跟 `daily_event_cache` 同一邊。而且
`spirit_id` 指向 `public.spirits`，同一個 schema 才下得了外鍵——WBS-API 決策4
限制的是**跨 schema** 不建外鍵，不是身體自己的表之間。

## 🔒 active 預設 false，沒有任何程式路徑會把它設成 true

跟 `brain.districts`（0018）與 `brain.character_personas` 同一個慣例。抓取腳本
只寫內容，不碰審核狀態；放行只能由 `scripts/review_landmark_events.py` 這條
人工路徑做。

理由不是不信任 iCulture——那是政府開放資料、由場館自報，符合 SDD 白名單第二類
「地標官方公開活動」。理由是**我們沒有辦法事先知道抓回來的東西長什麼樣**：
場館報錯期程、活動性質跟地標調性不合（龍山寺旁邊三百公尺辦的搖滾演唱會會被
haversine 對上）、或單純是一筆測試資料。審核閘是這些情況唯一的攔截點。

## content_hash 的用途是「內容變了就退回未審核」

重抓時如果活動內容有變（改期、改名），`active` 會被打回 false、審核簽名清空。
這跟 `import_landmarks.py` 的 `UPSERT_DISTRICT` 是同一個道理：內容換了而審核
狀態留著，等於讓上一次的簽名替這一次的文字背書，那比一開始就沒有審核更糟。

用一欄雜湊而不是在 SQL 裡逐欄比對，是為了讓「哪些欄位算內容」只寫在一個地方
（`landmark_events.content_fingerprint()`）。逐欄比對的話，日後加一個欄位而忘了
加進比對條件，就會出現「內容改了但審核沒退回」——而那不會有任何錯誤訊息。

## 為什麼 start_date / end_date 可以是 NULL，但沒有期程的活動不會被推

開放資料的日期欄位品質參差，解析不出來是常態。存 NULL 是誠實的表達；
「不推沒有結束日期的活動」那條規則寫在 `landmark_events.is_running_on()`，
不是靠 NOT NULL 約束擋——約束擋掉的話整筆資料會在匯入時消失，我們就再也
看不到「有幾筆因為日期壞掉而被丟棄」。
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "landmark_events",
        # `iculture:{UID}:{n}`。n 是同一個活動底下第幾個不同場地——一個展覽
        # 可能巡迴多個場館，而我們是把「場地」對到地標的。
        sa.Column("event_id", sa.String(160), primary_key=True),
        sa.Column(
            "spirit_id",
            sa.String(64),
            sa.ForeignKey("spirits.spirit_id"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("venue_name", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        # 來源代號。授權標示的文字由它決定（見 `landmark_events.SOURCE_LABELS`）——
        # 標示不寫死在客戶端，換資料源不該要我們發一版 App。
        sa.Column("source", sa.String(32), nullable=False, server_default="iculture"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        # 🔒 見 docstring。沒有任何程式路徑會把它設成 true。
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # 挑選當日活動的唯一查詢形狀：某地標、已放行、今天還在檔期內。
    # 三個欄位的順序就是過濾的選擇性順序。
    op.create_index(
        "ix_landmark_events_lookup",
        "landmark_events",
        ["spirit_id", "active", "end_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_landmark_events_lookup", table_name="landmark_events")
    op.drop_table("landmark_events")
