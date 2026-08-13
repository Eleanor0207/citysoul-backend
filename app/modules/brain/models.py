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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.types import UserDefinedType
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
    # 敘事分組標籤，可為 NULL（0014）。`city → landmark` 的垂直關係才是主結構，
    # 分區是額外掛上去的一層，不是每個地標都得屬於某個區。
    district_id = Column(String(64), ForeignKey("brain.districts.district_id"), nullable=True)
    name = Column(String(128), nullable=False)
    founding_facts = Column(JSONB, nullable=False)  # [{year, event, detail}, ...]
    key_events = Column(JSONB, nullable=True)
    cultural_significance = Column(Text, nullable=True)
    # 廣為流傳但經查證為錯的說法，[{misconception, correction, say_instead, source}, ...]
    #
    # ⚠️ **不要為了少一個欄位就併進 `key_events`。** 三層設定是整段注入 prompt 的，
    # `key_events` 裡的每一條模型都會當成可以直接講的事實。誤解是負面內容——即使
    # 旁邊註明它是錯的，模型也沒有可靠的訊號知道要否定它，很可能就照著講了。分開
    # 存放，prompt 才能把它渲染成「這些是常見誤解，被問到時這樣講」。
    #
    # 跟 `CharacterPersona.taboos` 也是兩件事：taboos 說「不談什麼」，這裡說「談的
    # 時候不能講錯」。因此它在史實層而不是有版本的人格層——事實更正不隨人格改版
    # 而變，放進人格表等於每改一版都要複製一次。
    common_misconceptions = Column(JSONB, nullable=True)
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


class Geography(UserDefinedType):
    """
    PostGIS `geography(Polygon,4326)`，手寫成 5 行而不是引入 geoalchemy2。

    專案裡所有 PostGIS 運算都走 raw SQL（見 `app/modules/body/districts.py` 的
    說明），ORM 這邊只需要「這個欄位存在、型別是什麼」，用不到 geoalchemy2 的
    比較運算子與函式包裝。為一個欄位多背一個相依不划算。
    """

    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "geography(Polygon,4326)"


class District(Base):
    """
    行政區地理圍欄（0014）。`citysoul_data_schema.md` §9。

    ⚠️ **`boundary` 是唯一的判斷依據。** `center_lat` / `center_lng` /
    `radius_meters` 只給地圖 UI 畫概略圓形。真實行政區界不是圓的——用圓形判斷
    會同時產生誤觸發（把隔壁區的玩家算進來）與漏觸發（把區內邊角的玩家排除），
    而且這兩種錯誤沒辦法靠調整半徑同時變小。

    `districts` 是**敘事分組標籤，不是遊戲地理的主結構**：`city → landmark`
    的垂直關係不變，不是每個地標都得屬於某個區，所以 `landmark_souls.district_id`
    可為 NULL。

    ⚠️ 圍欄判斷**不留下任何紀錄**。`check_player_in_district()` 收到座標、算完、
    回傳布林值，座標即丟。「玩家曾在某時刻進入某區域」只以 `player_inventory`
    裡多了一件 arc 信件的形式存在——那是遊戲進度，不是位置紀錄。
    """

    __tablename__ = "districts"
    __table_args__ = {"schema": "brain"}

    district_id = Column(String(64), primary_key=True)
    city_id = Column(String(64), ForeignKey("brain.city_souls.city_id"), nullable=True)
    name = Column(String(128), nullable=False)
    # 以下三欄僅供地圖 UI，見上方說明。
    center_lat = Column(Numeric(9, 6), nullable=True)
    center_lng = Column(Numeric(9, 6), nullable=True)
    radius_meters = Column(Integer, nullable=True)
    boundary = Column(Geography, nullable=True)


class StoryArc(Base):
    """
    一條主線案件（0015）。

    `intro_document_content` 放的是**信件全文**，不是敘事指令——它是玩家原樣讀到
    的東西，跟 `canned_greetings.response_text` 同一類，不經過 LLM。
    """

    __tablename__ = "story_arcs"
    __table_args__ = {"schema": "brain"}

    arc_id = Column(String(64), primary_key=True)
    title = Column(Text, nullable=False)
    district_id = Column(String(64), ForeignKey("brain.districts.district_id"), nullable=True)
    intro_document_title = Column(Text, nullable=True)
    intro_document_content = Column(Text, nullable=True)
    # 值關聯 → public.media_assets.asset_id，不建跨 schema 外鍵（WBS-API 決策4）。
    intro_document_asset_id = Column(UUID(as_uuid=True), nullable=True)
    summary = Column(Text, nullable=True)


class StoryBeat(Base):
    """
    劇情節點（0015）。

    ## 資料庫擋不住 prerequisite_beat_ids 的錯

    `TEXT[]` 沒辦法宣告「每個元素都必須是存在的 beat_id」——Postgres 沒有陣列
    元素外鍵。所以三件事只能由匯入器檢查：**懸空引用**（打錯字 → 那個 beat 永遠
    解不開）、**環**（A 需要 B、B 需要 A → 兩個都觸發不了）、**孤島**（走不到的
    節點）。三種壞法都是靜默的：玩家卡住，log 乾淨，測試也不會紅。

    ## trigger_condition 只能是可判定的條件

    描述性文字，實際判斷在 body 狀態機，而狀態機只能查資料庫狀態——它讀不出
    玩家在對話裡說了什麼。需要理解自然語言才判斷得出來的條件不能寫進這一欄，
    那種需求應該改寫成前置 beat。

    ## sequence_order 是單一角色內的排序

    唯一性是 `(arc_id, character_id, sequence_order)`。同一條 arc 裡三個地標的
    第一個 beat 都是 1，這是對的；寫成 `(arc_id, sequence_order)` 唯一會把正確的
    劇本擋掉。跨角色的先後由 `prerequisite_beat_ids` 表達。
    """

    __tablename__ = "story_beats"

    beat_id = Column(String(64), primary_key=True)
    arc_id = Column(String(64), ForeignKey("brain.story_arcs.arc_id"), nullable=True)
    character_id = Column(
        String(64), ForeignKey("brain.characters.character_id"), nullable=True
    )
    sequence_order = Column(Integer, nullable=False)
    trigger_condition = Column(Text, nullable=False)
    # 敘事指令，不是台詞。腦袋據此組 prompt 生成實際字句。
    narrative_directive = Column(Text, nullable=False)
    prerequisite_beat_ids = Column(ARRAY(Text), nullable=True)
    # 值關聯 → public.player_inventory.item_id。
    required_item_ids = Column(ARRAY(Text), nullable=True)
    contingency_notes = Column(Text, nullable=True)
    one_time = Column(Boolean, nullable=False, server_default="true", default=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_story_beats_arc", "arc_id"),
        {"schema": "brain"},
    )


class ResonanceUnlockable(Base):
    """
    共鳴值解鎖的記憶片段（0015）。

    跟 `StoryBeat` 是**兩條分開的線**：beat 是事件驅動（做了什麼才觸發），
    這裡是數值驅動（共鳴值到門檻就開放）。兩者互不取代，也不做雙重門檻——
    主線進度完全獨立於 resonance 機制。

    ⚠️ **`content` 放的是敘事指令，不是成品台詞。** 欄位名很容易誤解：它跟
    `StoryBeat.narrative_directive` 是同一類東西，由 B11 據此生成字句。寫成成品
    台詞會讓三次解鎖聽起來像罐頭，而且繞過語氣層與 relationship_stage 的深度限制。

    `min_resonance` 只能是固定的 10 / 40 / 100（AC5.3），不為個別角色另訂數值。
    """

    __tablename__ = "resonance_unlockables"

    unlock_id = Column(String(64), primary_key=True)
    character_id = Column(
        String(64), ForeignKey("brain.characters.character_id"), nullable=True
    )
    min_resonance = Column(Integer, nullable=False)
    unlock_type = Column(String(32), nullable=False)  # 'memory_fragment' / 'secret_story'
    content = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "character_id", "min_resonance", "unlock_type", name="uq_resonance_unlockables"
        ),
        {"schema": "brain"},
    )
