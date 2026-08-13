"""
身體模組資料表：players、spirits。

`daily_event_cache` 已於 0008 建立（S10／#26）。`push_subscriptions` 仍未建，
排在 S11（#39）——不要因為「順手」提早建一張還沒有人寫入的表。

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
    Float,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # 刻意沒有 server_default：「新玩家用哪個 tier」的答案是
    # `usage_tiers.is_default`，在欄位上再放一個預設值等於同一件事有兩個真相。
    usage_tier_id = Column(String(32), ForeignKey("usage_tiers.tier_id"), nullable=False)
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
    # 值關聯到 brain.characters / brain.landmark_souls，**刻意不建外鍵**
    # （WBS-API 決策4：身體與腦袋的表不建跨 schema 外鍵）。
    # UNIQUE 強制一個地標靈魂只有一種人格；要支援人格變體時移除它、改中介表。
    character_id = Column(String(64), nullable=True)
    landmark_id = Column(String(64), nullable=True)
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

    # 靈魂方位（SDD v2.1 §10.2），供客戶端 S14 的 3DoF 定向使用。
    # `bearing_deg` 是相對召喚點的方位角（真北 0°、順時針），
    # `height_offset_m` 是相對玩家視線高度的垂直偏移。
    #
    # 用浮點數而非隔壁經緯度的 NUMERIC 是刻意的：經緯度是遊戲規則的輸入
    # （50m 內才算在場），不該帶浮點誤差；方位只是渲染參數，差 0.0001 度
    # 沒有玩家察覺得到，也不改變任何判定結果。
    bearing_deg = Column(Float, nullable=False, default=0.0, server_default="0")
    height_offset_m = Column(Float, nullable=False, default=0.0, server_default="0")

    __table_args__ = (
        UniqueConstraint("character_id", name="uq_spirits_character_id"),
    )


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


class UsageTier(Base):
    """
    配額分級（AC8／#32）。

    `is_default` 的「只能有一筆 TRUE」由 partial unique index 保證，不是靠
    寫入時自己記得檢查。`BOOLEAN default=False` 擋不住兩筆都是 TRUE。
    """

    __tablename__ = "usage_tiers"

    tier_id = Column(String(32), primary_key=True)
    display_name = Column(String(64), nullable=False)
    is_default = Column(Boolean, nullable=False, server_default="false", default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index(
            "uq_usage_tiers_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class UsageTierLimit(Base):
    """
    每個分級底下各種資源的上限。

    **一個 tier 對多筆 limit 的正規化設計**，不是每種資源開一個欄位。加一種新
    配額只是 INSERT 一筆，不用 ALTER TABLE、不用重新部署。

    計數器本身在 Redis（key 含 Asia/Taipei 日期），不落地 Postgres，所以沒有
    對應的 usage 表。
    """

    __tablename__ = "usage_tier_limits"

    tier_id = Column(String(32), ForeignKey("usage_tiers.tier_id"), primary_key=True)
    resource_type = Column(String(32), primary_key=True)
    limit_value = Column(Integer, nullable=False)


class EncounterCollection(Base):
    """
    地標相遇收藏（AC5.2）。

    `UNIQUE(player_id, place_id)` 是「每個地標只加一次共鳴值」的依靠，跟
    `ResonanceEvent` 同一個道理：先寫、撞到約束才知道重複，不是先查再寫。

    ⚠️ **不存原始照片，也不存 GPS 座標。** CONTEXT.md 的定義是「玩家、地標、
    收藏時間、本機辨識結果與共鳴貢獻」；`recognized_label` 是裝置端辨識出來的
    標籤，不是位置。加座標欄位進來，這張表就變成移動軌跡了。
    """

    __tablename__ = "encounter_collections"

    collection_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), nullable=False)
    place_id = Column(String(64), ForeignKey("spirits.spirit_id"), nullable=False)
    # 裝置端本機辨識出來的標籤字串（0004 的設計），不是位置。
    recognized_label = Column(String(128), nullable=True)
    # 雲端 B13（#22）的判定結果，決定要不要給特別徽章。
    landmark_recognized = Column(Boolean, nullable=False, server_default="false", default=False)
    # 這次收藏**有沒有真的入帳**共鳴值。
    #
    # UNIQUE(player_id, place_id) 保證一個地標只加一次，所以「有這一列」不等於
    # 「這次加了值」——重複收藏時列還在，但沒有入帳。少了這個欄位，就沒辦法從
    # 資料本身分辨那兩種情況。
    resonance_awarded = Column(Boolean, nullable=False, server_default="false", default=False)
    collected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("player_id", "place_id", name="uq_encounter_collections"),
    )


class DailyEventCache(Base):
    """
    S10．當日情境快取（SDD §3.1／#26）。

    內容由 B9 生成（#20），這張表只管「什麼時候生成的、放在哪」——生成與快取
    刻意分屬不同模組（v2.1 §6.4）。

    PK `(place_id, event_date)` 保證「一個地標一天一筆」。排程重複觸發是正常的
    （重試、多實例、手動補跑），所以去重在資料庫層級，不靠排程自己記得。
    """

    __tablename__ = "daily_event_cache"

    place_id = Column(String(64), ForeignKey("spirits.spirit_id"), primary_key=True)
    # 台北日期。存 DATE 而不是帶時區的時間點——後者會逼每個讀取端自己再算一次
    # 「這是台北的哪一天」，而那正是 #15 踩過的坑。
    event_date = Column(Date, primary_key=True)
    content = Column(JSONB, nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # 給清理工作用的訊號，**不是讀取時的過濾條件**：保底策略是「今天沒有就回
    # 昨天」，所以過期的內容仍然有用——它比空畫面好。
    expires_at = Column(DateTime(timezone=True), nullable=True)


class AvatarAsset(Base):
    """
    Unity Addressables 的 remote catalog／bundle 位置與版本（#38）。

    ## 版本是這張表存在的理由

    客戶端要能問「我快取的這版還是最新的嗎」，而不是每次啟動都重抓整包。
    所以 `version` 必須**改了要變、沒改要穩定不變**——它是一個明確寫入的欄位，
    刻意**不從** `updated_at` 或內容 hash 算出來：那樣的話一次無關的資料列更新
    就會讓所有客戶端重抓。

    catalog 與 bundle 分成兩個欄位，因為 Addressables 的索引與實際內容可能放在
    不同路徑甚至不同 bucket。合成一個欄位的話，之後要分開就是破壞性變更。
    """

    __tablename__ = "avatar_assets"

    avatar_id = Column(String(64), primary_key=True)
    catalog_url = Column(Text, nullable=False)
    bundle_url = Column(Text, nullable=False)
    version = Column(String(64), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PushSubscription(Base):
    """
    S11．推播訂閱（SDD v1 §3.1／#39）。

    主鍵是 `player_id`——換手機、token 輪替時是**覆蓋**而不是累積。用代理鍵的話，
    一個玩家會慢慢長出十幾列失效的 token，而群發時得自己挑「最新的那個」，
    那個判斷遲早會出錯然後推播送到別人的舊裝置上。

    退訂用 `is_subscribed=false` 而不是刪列：token 還有用，玩家重新訂閱時不需要
    重新註冊裝置。

    ⚠️ **沒有任何位置欄位。** 推播是通知不是內容——玩家點進來後才呼叫既有端點
    取內容，所以這張表不需要知道他在哪裡。
    """

    __tablename__ = "push_subscriptions"

    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), primary_key=True)
    push_token = Column(String(256), nullable=False)
    is_subscribed = Column(Boolean, nullable=False, server_default="true", default=True)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MediaAsset(Base):
    """
    媒體資產（0013）。圖檔本身在 Cloud Storage，這裡只存路徑與 metadata。

    ⚠️ **沒有 EXIF 欄位，也不要加。** 手機照片的 EXIF 帶著拍攝座標與時間，
    原樣保存等同建立位置紀錄——那是 CONTEXT.md「不存移動軌跡」擋的東西，
    只是換個地方存。上傳流程必須在寫進 Cloud Storage 之前就把 EXIF 剝掉。

    `spirit_id` 對系統素材（主線信件、道具插圖）是 NULL：那些屬於整條 arc，
    不專屬單一靈魂。
    """

    __tablename__ = "media_assets"

    asset_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_type = Column(Text, nullable=False)
    owner_type = Column(Text, nullable=False)  # 'system' / 'player'
    owner_id = Column(Text, nullable=True)
    spirit_id = Column(Text, ForeignKey("spirits.spirit_id"), nullable=True)
    gcs_path = Column(Text, nullable=False)
    cdn_url = Column(Text, nullable=False)
    content_type = Column(Text, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("owner_type IN ('system', 'player')", name="ck_media_assets_owner_type"),
    )


class Quest(Base):
    """
    人工撰寫的任務定義（0013）。

    ⚠️ **每日任務不在這張表裡。** `quests.py` 的 `quest_id_for_spirit()` 是
    推導出 `f"{spirit_id}:daily"`，從來不寫進任何表——所以 `quest_progress`
    的每日列引用的 quest_id 在這裡找不到對應。這也是 `quest_progress.quest_id`
    刻意不加外鍵的原因（見 0013 的 docstring）。

    這張表放的是 story 類型與主線的拍照任務，也就是有人真的寫過內容的那些。
    """

    __tablename__ = "quests"

    quest_id = Column(Text, primary_key=True)
    spirit_id = Column(Text, ForeignKey("spirits.spirit_id"), nullable=True)
    title = Column(Text, nullable=False)
    quest_type = Column(Text, nullable=False)  # 'daily' / 'story' / 'resonance_gated'
    min_resonance = Column(Integer, nullable=False, server_default="0", default=0)
    # 值關聯 → brain.story_beats，不建跨 schema 外鍵。僅 story 類型有值。
    story_beat_id = Column(Text, nullable=True)
    steps = Column(JSONB, nullable=False, server_default="[]")
    reward_type = Column(Text, nullable=True)  # 'resonance' / 'item'
    reward_value = Column(JSONB, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true", default=True)

    __table_args__ = (
        CheckConstraint(
            "quest_type IN ('daily', 'story', 'resonance_gated')", name="ck_quests_type"
        ),
    )


class PlayerInventory(Base):
    """
    玩家持有的道具（0013）。`story_beats.required_item_ids` 查的就是這張表。

    `uq_inventory_player_item` **是防重複發放的機制本身**，不是效能索引。
    玩家在區域圍欄邊緣來回走動會重複觸發 `grant_arc_intro_document()`，
    靠應用層「先查有沒有再寫」在併發下會漏掉——唯一索引才擋得住。
    """

    __tablename__ = "player_inventory"

    inventory_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), nullable=False)
    # 'badge' / 'collectible' / 'story_memento' / 'story_key_item' / 'story_document'
    item_type = Column(Text, nullable=False)
    item_id = Column(Text, nullable=False)
    source_quest_id = Column(Text, ForeignKey("quests.quest_id"), nullable=True)
    illustration_asset_id = Column(
        UUID(as_uuid=True), ForeignKey("media_assets.asset_id"), nullable=True
    )
    acquired_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_inventory_player", "player_id"),
        Index("uq_inventory_player_item", "player_id", "item_id", unique=True),
    )


class DialogueTurn(Base):
    """
    對話日誌（0013）。也是 B6 長期記憶排程萃取的資料來源。

    ⚠️ **不存座標。** 哪一次對話發生在哪裡由 `spirit_id` 表達——靈魂本身就是
    地點。這張表帶 `player_id` 與時間，再加座標就是移動軌跡，只是叫別的名字。

    `story_beat_id` 是**日誌上的註記，不是進度的依據**。「這個 beat 觸發了沒」
    永遠查 `PlayersStoryProgress`，兩邊對不上時不需要猜該信哪一個。
    """

    __tablename__ = "dialogue_turns"

    turn_id = Column(BigInteger, Identity(always=False), primary_key=True)
    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), nullable=False)
    spirit_id = Column(Text, ForeignKey("spirits.spirit_id"), nullable=False)
    role = Column(Text, nullable=False)  # 'player' / 'spirit'
    content = Column(Text, nullable=False)
    resonance_value_at_time = Column(Integer, nullable=True)
    # 值關聯 → brain.story_beats，不建跨 schema 外鍵。
    story_beat_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("role IN ('player', 'spirit')", name="ck_dialogue_turns_role"),
        Index("idx_dialogue_player_spirit_time", "player_id", "spirit_id", "created_at"),
    )


class PlayersStoryProgress(Base):
    """
    玩家踩過哪些劇情節點（0013）。**主線進度的唯一真相來源。**

    `check_beat_unlockable()` 判斷 `prerequisite_beat_ids` 是否都滿足，查的就是
    這張表。主鍵 `(player_id, beat_id)` 讓同一個 beat 天然只能記錄一次，寫入端
    用 `ON CONFLICT DO NOTHING` 就夠，不需要先查再寫。

    也因為只能記一次，`story_beats.one_time = false` 在這裡沒有對應的表達方式
    ——「觸發了幾次」這件事無處可存。需要重複出現的東西應該是語氣狀態
    （`contingency_notes`），不是劇情節點。
    """

    __tablename__ = "players_story_progress"

    player_id = Column(UUID(as_uuid=True), ForeignKey("players.player_id"), primary_key=True)
    # 值關聯 → brain.story_beats，不建跨 schema 外鍵。
    beat_id = Column(Text, primary_key=True)
    triggered_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
