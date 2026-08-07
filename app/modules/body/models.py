"""
身體模組資料表：players、spirits。

daily_event_cache／push_subscriptions 排在後面的 Sprint（對應 S10/S11），
現在先不建，避免一次生太多還沒用到的表。

刻意不存在的表：任何形式的「玩家移動軌跡 / 位置歷史」表。
這是 CONTEXT.md「在場紀錄」與「前景即時情境反應」兩條定義疊加後的硬限制，
寫在這裡提醒未來加欄位時不要不小心違反。
"""
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class Player(Base):
    """
    S6．匿名玩家身分系統。

    **匿名是預設，登入是附加資訊。** `device_id` 是身分，一定有值；
    `display_name` / `auth_provider` / `auth_provider_id` / `avatar_url`
    只有綁定過帳號的玩家才有。綁定是對既有列做 UPDATE，`player_id` 不變，
    共鳴值與任務進度原地保留，不是建一個新玩家再搬資料。

    唯一性用 partial unique index（見 migration 0003）而不是表上的
    `UniqueConstraint`：匿名玩家全都是 `(NULL, NULL)`，而 Postgres 的 UNIQUE
    不擋重複 NULL，寫成表級約束不會報錯，但也保護不到任何東西。
    """

    __tablename__ = "players"

    player_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(128), nullable=False, unique=True)  # 裝置本地身分
    display_name = Column(String(64), nullable=True)
    auth_provider = Column(String(32), nullable=True)  # 'google' / 'apple' / 'email'
    auth_provider_id = Column(String(128), nullable=True)
    avatar_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_active_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    total_summons = Column(Integer, nullable=False, server_default="0", default=0)
    notification_opt_in = Column(
        Boolean, nullable=False, server_default="true", default=True
    )

    __table_args__ = (
        # 半綁定狀態（有 provider 沒有 id，或反過來）在應用層沒有意義，
        # 讓資料庫直接擋掉。
        CheckConstraint(
            "(auth_provider IS NULL) = (auth_provider_id IS NULL)",
            name="ck_players_auth_pair",
        ),
        Index(
            "uq_players_auth",
            "auth_provider",
            "auth_provider_id",
            unique=True,
            postgresql_where=text("auth_provider IS NOT NULL"),
        ),
    )


class Spirit(Base):
    """
    地標／召喚點基本資料。

    封閉測試垂直切片階段（CONTEXT.md）只會有龍山寺這一筆，
    seed script 也只塞這一筆，不要一次把十個首發靈魂都建進來。

    兩個半徑是**兩段不同的體驗**，不是同一個值的寬鬆版本（SDD 第7.1／7.2節）：
    `sense_radius_meters`（150m）進入感應範圍，地標淡淡發光、可以隔空聊天；
    `summon_radius_meters`（50m）才算在場成立，可以召喚與挑戰任務。

    欄位名跟對外 API 的欄位名**刻意不同**（DB 的 `spirit_id` 對上 JSON 的
    `place_id`）。Unity client 的 DTO 寫死了那些 key，而資料庫欄位名沒有必須
    跟 wire contract 一致的理由。對應寫在 `schemas.SpiritResponse`。
    """

    __tablename__ = "spirits"

    spirit_id = Column(String(64), primary_key=True)
    display_name = Column(String(128), nullable=False)
    # NUMERIC(9,6) 而不是浮點數：距離判斷是遊戲規則的一部分（50m 內才在場），
    # 規則的輸入值不該帶浮點誤差。6 位小數約 11 公分。
    #
    # `asdecimal=False` 讓 Python 端仍拿到 float。儲存是精確的十進位，但距離
    # 計算（haversine）本來就是浮點數學，讀出來立刻轉 Decimal 只會逼得
    # `geo.py` 到處做型別轉換，換不到任何精度。
    latitude = Column(Numeric(9, 6, asdecimal=False), nullable=False)
    longitude = Column(Numeric(9, 6, asdecimal=False), nullable=False)
    summon_radius_meters = Column(Integer, nullable=False, default=50)
    sense_radius_meters = Column(Integer, nullable=False, default=150, server_default="150")
    is_active = Column(Boolean, nullable=False, default=True)


class QuestProgress(Base):
    """
    S4．可驗證微任務進度（SDD 第3.1／7.4節）。

    這張表都在 public schema，跟 players／spirits 同一邊，所以外鍵是可以建的
    ——WBS-API 決策4 限制的是「身體與腦袋跨 schema 不建外鍵」，不是身體自己
    的表之間。

    `status` 只有 `in_progress` / `completed` 兩個值。回應中出現的
    `daily_limit_reached` **不是**資料庫狀態，是「今天嘗試次數已用完」這個
    查詢當下才算得出來的結果——它跟日期有關，存進資料庫隔天就是錯的。

    主鍵是代理鍵 `progress_id`，唯一性靠兩個 partial unique index（0003）：
    `issued_date IS NULL` 的一次性任務唯一於 `(player_id, quest_id)`；
    每日任務唯一於 `(player_id, quest_id, issued_date)`，不同天各一列。

    `issued_date`（哪一天發的）跟 `attempts_date`（哪一天試的）是兩件事，
    兩個都要。前者決定唯一性與過期，後者決定 `daily_limit_reached`。
    """

    __tablename__ = "quest_progress"

    progress_id = Column(BigInteger, Identity(always=False), primary_key=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), nullable=False)
    quest_id = Column(String(64), nullable=False)
    # daily 任務才有值；story／resonance_gated 為 NULL。
    issued_date = Column(Date, nullable=True)
    status = Column(String(16), nullable=False, default="in_progress")
    progress_value = Column(Integer, nullable=False, default=0)
    attempts_today = Column(Integer, nullable=False, default=0)
    # 這一天是以 Asia/Taipei 計的日期（SDD 第7節決策8）。存 DATE 而不是
    # timestamp，是因為「哪一天」才是語意本身，時分秒沒有意義。
    attempts_date = Column(Date, nullable=False)
    current_token_issued_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "uq_quest_progress_onetime",
            "player_id",
            "quest_id",
            unique=True,
            postgresql_where=text("issued_date IS NULL"),
        ),
        Index(
            "uq_quest_progress_daily",
            "player_id",
            "quest_id",
            "issued_date",
            unique=True,
            postgresql_where=text("issued_date IS NOT NULL"),
        ),
    )


class Resonance(Base):
    """
    S5．玩家與單一城市靈魂之間的關係進度（SDD 第3.1節）。

    **階段不存欄位**，由 `resonance.stage_for_value()` 從 `resonance_value`
    運行時算出（門檻 10/40/100，AC5.3）。0003 之前有一個 `stage` 欄位，但
    `stage_for_value()` 每次都重算、從不讀它——一個永遠不被信任的快取欄位，
    存在的唯一效果是讓下一個人誤用它。

    `resonance_value` 本身也是可重算的：事實是 `resonance_events` 那本流水帳，
    這裡只是加總結果。
    """

    __tablename__ = "resonance"

    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), primary_key=True)
    spirit_id = Column(String(64), ForeignKey("spirits.spirit_id"), primary_key=True)
    resonance_value = Column(Integer, nullable=False, default=0)
    last_updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ResonanceEvent(Base):
    """
    共鳴事件帳本：確保單一收藏或任務不重複加值（SDD 第3.1／7.5節）。

    `UNIQUE(player_id, source_type, source_id)` 是防重複入帳的**唯一**依靠——
    不是先查再寫的應用層檢查。兩個並行請求都查到「還沒入過帳」然後都寫入，
    這種競態只有資料庫約束擋得住。所以入帳流程是「先寫帳本、撞到約束就當作
    重複」，不是「先查帳本、沒有才寫」。

    注意這個 UNIQUE **不含 `spirit_id`**：SDD schema 就是這樣定義的，同一個
    player 的同一個 source_id 全域只能入帳一次，跨靈魂也不行。
    """

    __tablename__ = "resonance_events"

    resonance_event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), nullable=False)
    spirit_id = Column(String(64), ForeignKey("spirits.spirit_id"), nullable=False)
    source_type = Column(String(32), nullable=False)  # 'encounter_collection' / 'quest'
    source_id = Column(String(128), nullable=False)
    amount = Column(Integer, nullable=False)  # encounter_collection=10；quest=20
    awarded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "player_id", "source_type", "source_id", name="uq_resonance_events_source"
        ),
    )
