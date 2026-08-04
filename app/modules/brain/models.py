"""
腦袋模組資料表：persona_cards。

刻意放在 Postgres 的 `brain` schema（不是 public），對應 WBS-API 決策4：
身體的結構化表跟腦袋的語意/人格表分開管理，兩邊不互相唯讀存取對方的表，
只靠 player_id／spirit_id 這種值做邏輯關聯，不建跨 schema 的外鍵約束。

短期記憶不落地在 PostgreSQL，走 Redis（B7）——這裡不會出現對應的表。
"""
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.core.database import Base

# Vertex AI text-embedding 系列與多數常見模型的輸出維度。SDD 第10節記載
# embedding 模型本身還沒拍板，但維度先固定成 768（schema 已這樣定義）；
# 真的換成不同維度的模型時，這是一次需要 migration 的破壞性變更，不是改個常數就好。
EMBEDDING_DIM = 768


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


class MemoryEmbedding(Base):
    """
    B6．長期記憶語意向量。

    `player_id` 是 UUID 但**刻意不建外鍵**指向 public.players——WBS-API 決策4：
    身體與腦袋的表不建跨 schema 實體外鍵，只用 player_id／spirit_id 的值做邏輯
    關聯。`spirit_id` 同理，不指向 public.spirits。想加 ForeignKey 之前先回去讀
    那條決策。
    """

    __tablename__ = "memory_embeddings"

    memory_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), nullable=False)
    spirit_id = Column(String(64), nullable=False)
    summary_text = Column(Text, nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)
    source = Column(String(32), nullable=False)  # 'dialogue_summary' / 'nightly_batch'
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # SDD 第3.2節指定的 ivfflat cosine 索引。
        #
        # ivfflat 是「近似」最近鄰索引：它把向量分成 lists 個群集，查詢時只掃其中
        # 幾群，所以可能漏掉真正的前 K 名。這對記憶檢索是可接受的取捨，但也代表
        # 檢索函式的正確性測試不能依賴它——見 retrieval.py 裡關於 seq scan 的說明。
        Index(
            "ix_memory_embeddings_embedding_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # 檢索一律以 (player_id, spirit_id) 為前置條件，這個索引讓向量比對前
        # 能先把候選列縮到單一玩家對單一靈魂的記憶。
        Index("ix_memory_embeddings_player_spirit", "player_id", "spirit_id"),
        {"schema": "brain"},
    )
