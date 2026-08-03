"""
身體模組資料表：players、spirits。

Sprint1 範圍只做這兩張——quest_progress／resonance／daily_event_cache／
push_subscriptions 排在後面的 Sprint（對應 S4/S5/S10/S11），現在先不建，
避免一次生太多還沒用到的表。

刻意不存在的表：任何形式的「玩家移動軌跡 / 位置歷史」表。
這是 CONTEXT.md「在場紀錄」與「前景即時情境反應」兩條定義疊加後的硬限制，
寫在這裡提醒未來加欄位時不要不小心違反。
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
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
