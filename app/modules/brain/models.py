"""
腦袋模組資料表：靈魂三層（city / landmark / character）＋記憶。

刻意放在 Postgres 的 `brain` schema（不是 public），對應 WBS-API 決策4：
身體的結構化表跟腦袋的語意/人格表分開管理，兩邊不互相唯讀存取對方的表，
只靠 player_id／spirit_id 這種值做邏輯關聯，不建跨 schema 的外鍵約束。

短期記憶不落地在 PostgreSQL，走 Redis（B7）——這裡不會出現對應的表。
"""
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.sql import func

from app.core.database import Base

# Vertex AI text-embedding 系列與多數常見模型的輸出維度。SDD 第10節記載
# embedding 模型本身還沒拍板，但維度先固定成 768（schema 已這樣定義）；
# 真的換成不同維度的模型時，這是一次需要 migration 的破壞性變更，不是改個常數就好。
EMBEDDING_DIM = 768


class CitySoul(Base):
    """
    City 層：全城共用的基調。

    不做向量檢索——資料量有界，整段注入 prompt 更穩定也更省成本。RAG 是給
    `MemoryEmbedding` 那種會無限成長的東西用的。
    """

    __tablename__ = "city_souls"
    __table_args__ = {"schema": "brain"}

    city_id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    macro_history_summary = Column(Text, nullable=False)
    core_tone_descriptors = Column(ARRAY(Text), nullable=True)
    shared_values = Column(ARRAY(Text), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LandmarkSoul(Base):
    """
    Landmark 層：史實資料，決定角色「知道什麼」。

    跟人格層刻意分離：史實決定知道什麼，人格決定怎麼說話。混在一起會在生成時
    互相干擾。
    """

    __tablename__ = "landmark_souls"
    __table_args__ = {"schema": "brain"}

    landmark_id = Column(String(64), primary_key=True)
    city_id = Column(String(64), ForeignKey("brain.city_souls.city_id"), nullable=False)
    name = Column(String(128), nullable=False)
    founding_facts = Column(JSONB, nullable=False)  # [{year, event, detail}, ...]
    key_events = Column(JSONB, nullable=True)
    cultural_significance = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Character(Base):
    """
    角色身分層。

    只有識別與歸屬，沒有內容。存在的理由是人格有版本（主鍵是
    `(character_id, version)`），而 `spirits`、`story_beats` 這些表要指的是
    「這個角色」而不是「這個角色的第 3 版」，需要一個穩定的單欄位主鍵。
    """

    __tablename__ = "characters"
    __table_args__ = {"schema": "brain"}

    character_id = Column(String(64), primary_key=True)
    landmark_id = Column(
        String(64), ForeignKey("brain.landmark_souls.landmark_id"), nullable=False
    )


class CharacterPersona(Base):
    """
    Character 人格層：決定角色「怎麼說話」，優先於 city／landmark 基調。

    **改人格是 append 一筆新 version，不是就地覆寫。** 舊版留著，審核通過才把
    `active` 換過去。沒有歷史與審核人記錄的話，出事時無法回溯是誰在什麼時候
    改的——那不是稽核的方便，是人格內容能不能上線的前提。

    🔒 `active` 只能由人工審核流程 flip。CONTEXT.md 對「人格卡」的定義邊界，
    不是技術限制：**這份程式碼裡沒有任何路徑會把它設成 true。**

    「一個角色同時只能有一個生效版本」由 partial unique index 保證，不是靠
    寫入時自己記得先把舊版關掉。
    """

    __tablename__ = "character_personas"

    character_id = Column(
        String(64), ForeignKey("brain.characters.character_id"), primary_key=True
    )
    version = Column(Integer, primary_key=True)
    archetype = Column(Text, nullable=False)
    speech_style = Column(Text, nullable=False)
    personality_traits = Column(ARRAY(Text), nullable=True)
    values = Column(ARRAY(Text), nullable=True)
    # ⚠️ 安全下限。宗教場域的禁忌（不代神明發言、不預測吉凶）敘事審查只能
    # 往上加，不能移除既有條目；新版本少了既有 taboo 應視為審核不通過。
    taboos = Column(ARRAY(Text), nullable=True)
    # 負面人格聲明。跟 taboos 是兩件事：taboos 說「不談什麼」，這裡說「不是誰」。
    not_this_character = Column(Text, nullable=True)
    # 虛構授權：神祕感的來源，以及不能宣稱什麼。
    imagination_license = Column(Text, nullable=True)
    quest_themes = Column(ARRAY(Text), nullable=True)
    tone_override = Column(Text, nullable=True)
    reviewed_by = Column(String(128), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, nullable=False, server_default="false", default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index(
            "uq_character_personas_active",
            "character_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        {"schema": "brain"},
    )


class CannedGreeting(Base):
    """
    B12．快速問候的預寫台詞（AC7.1）。

    獨立成表而不是人格卡裡的一個 JSONB 欄位，因為它本來就是一對多。附帶的
    好處是 `response_text NOT NULL` 與 `trigger_phrases` 的型別讓「某一筆
    格式打錯」在資料庫層級就不可能存在——`greetings.py` 因此不需要防禦性解析。

    外鍵指向 `(character_id, version)` 而不是只有 character_id：預寫台詞是人工
    審核過的內容，改台詞就是改人格內容，要走「新版本 → 重新審核」的同一條路。
    """

    __tablename__ = "canned_greetings"

    greeting_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False)
    trigger_phrases = Column(ARRAY(Text), nullable=False)
    response_text = Column(Text, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["character_id", "version"],
            ["brain.character_personas.character_id", "brain.character_personas.version"],
            name="canned_greetings_persona_fkey",
        ),
        # NOT NULL 擋不住空字串與空陣列；這兩條才擋得住。
        CheckConstraint("response_text <> ''", name="ck_canned_greetings_response"),
        CheckConstraint("cardinality(trigger_phrases) > 0", name="ck_canned_greetings_triggers"),
        Index("ix_canned_greetings_persona", "character_id", "version"),
        {"schema": "brain"},
    )


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
        # 檢索函式的正確性測試不能依賴它——見 memory.py 裡關於 seq scan 的說明。
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
