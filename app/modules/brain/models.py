"""
腦袋模組資料表：persona_cards。

刻意放在 Postgres 的 `brain` schema（不是 public），對應 WBS-API 決策4：
身體的結構化表跟腦袋的語意/人格表分開管理，兩邊不互相唯讀存取對方的表，
只靠 player_id／spirit_id 這種值做邏輯關聯，不建跨 schema 的外鍵約束。

memory_embeddings（pgvector 表，B6）排在 Sprint2 才建，Sprint1 先把
人格卡這張做出來，因為 B3「人格卡載入與版本管理」是 Sprint1 範圍。
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.core.database import Base


class PersonaCard(Base):
    __tablename__ = "persona_cards"
    __table_args__ = {"schema": "brain"}

    spirit_id = Column(String(64), primary_key=True)
    version = Column(Integer, primary_key=True)
    content = Column(JSONB, nullable=False)  # 核心性格、語氣、史實邊界、禁忌、任務主題
    reviewed_by = Column(String(128), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
