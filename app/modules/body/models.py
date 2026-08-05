"""
身體模組資料表：players、spirits。

resonance／daily_event_cache／push_subscriptions 排在後面的 Sprint
（對應 S5/S10/S11），現在先不建，避免一次生太多還沒用到的表。

刻意不存在的表：任何形式的「玩家移動軌跡 / 位置歷史」表。
這是 CONTEXT.md「在場紀錄」與「前景即時情境反應」兩條定義疊加後的硬限制，
寫在這裡提醒未來加欄位時不要不小心違反。
"""
import uuid

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class Player(Base):
    """S6．匿名玩家身分系統。"""

    __tablename__ = "players"

    player_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(String(128), nullable=False, unique=True)  # 裝置本地身分
    account_id = Column(String(128), nullable=True)  # 升級綁定帳號後才有值
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Spirit(Base):
    """
    地標／召喚點基本資料。

    封閉測試垂直切片階段（CONTEXT.md）只會有天文館這一筆，
    seed script 也只塞這一筆，不要一次把十個首發靈魂都建進來。
    """

    __tablename__ = "spirits"

    place_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    summon_radius_m = Column(Integer, nullable=False, default=50)
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
    """

    __tablename__ = "quest_progress"

    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), primary_key=True)
    quest_id = Column(String(64), primary_key=True)
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
