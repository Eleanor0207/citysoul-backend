"""
收藏（圖鑑）：格子定義的推導，與解鎖時的發放。

收藏視窗有兩種格子，來源不同但在同一個清單裡：

- **初次相遇圖**：每個啟用中的地標一張，`/summon` 成功那一刻解鎖
- **劇本完成圖**：每條 arc 一張，該條劇本走完時解鎖

模組名字帶 `_service` 是為了不遮蔽標準函式庫的 `collections`——`router.py` 目前
沒用到它，但 `from app.modules.body import collections` 會讓那一天變成一個很難查的
匯入錯誤。命名慣例跟 `daily_event_service.py` 一致。

## 格數不寫死，從既有的表推導

相遇格 = `spirits` 裡 `is_active=true` 的列；劇本格 = `brain.story_arcs` 的列。
沒有另建定義表，因為那兩張表本來就是真相，多一張就多一個要同步的地方。

順帶處理掉霞海城隍廟：它 `is_active=false`（08-16 起，唯一沒有近景文件的地標），
`/summon` 對它回 404，所以它天然不出現在清單裡，也拿不到圖——不需要任何特例。
之後補上近景文件、`is_active` 轉回 true，那一格就自己長出來。

## 🔒 未解鎖的格子不回傳任何內容

`title` 與 `image_url` 在未解鎖時一律是 `None`。這不只是「客戶端不要顯示」——
**圖根本不會離開伺服器**。A.L. 定的介面是空欄位（不是灰階剪影），如果 API 照樣
把 URL 送出去，任何人抓一次封包就看完了整本圖鑑，那條設計等於沒有生效。

## 圖不存在玩家的列上

`player_inventory.illustration_asset_id` 對收藏格**刻意留 NULL**，顯示用的圖在讀取
時從定義解析（`media_assets.spirit_id` / `story_arcs.completion_asset_id`）。

兩個理由：

1. **換圖只要改一列。** 存在玩家列上的話，換一張圖要 UPDATE 每一個已解鎖的玩家。
2. **圖還沒畫好也能先上線。** 玩家照樣解鎖，只是 `image_url` 是 None；美術之後把
   `media_assets` 補進去，所有既有玩家下一次開收藏就看得到——不用回填任何資料。

`illustration_asset_id` 這個欄位本身沒有錯，它是給主線道具那種「這一份是玩家的」
素材用的。收藏格的圖屬於定義，不屬於玩家。
"""
import logging
import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.body import models
from app.modules.brain.models import StoryArc

logger = logging.getLogger(__name__)

# `player_inventory.item_type` 的既有列舉值（0013）之一。收藏格是 collectible，
# 不是 badge——badge 留給拍照徽章那條路，A.L. 2026-08-18 決定不做，但名字先別佔用。
ITEM_TYPE_COLLECTIBLE = "collectible"

# `media_assets.asset_type`。0013 的註解列了六種既有值，相遇圖是新的第七種。
ASSET_TYPE_ENCOUNTER = "encounter_illustration"

SOURCE_ENCOUNTER = "encounter"
SOURCE_ARC_COMPLETION = "arc_completion"

# `item_id` 用冒號前綴分命名空間，跟既有的 `{spirit_id}:daily` 同一個風格。
_ENCOUNTER_PREFIX = "encounter:"
_ARC_PREFIX = "arc_complete:"


def encounter_item_id(spirit_id: str) -> str:
    return f"{_ENCOUNTER_PREFIX}{spirit_id}"


def arc_completion_item_id(arc_id: str) -> str:
    return f"{_ARC_PREFIX}{arc_id}"


def grant_encounter_collectible(
    db: Session, *, player_id: uuid.UUID | str, spirit_id: str
) -> bool:
    """
    發放某個地標的初次相遇圖。**冪等**，回傳這次是否真的寫進去。

    去重靠 `uq_inventory_player_item` 唯一索引，用 `ON CONFLICT DO NOTHING`——
    不是先查再寫。這是 0013 的 docstring 明文立的規矩：那個索引「是防重複發放的
    機制本身，不是效能索引」，應用層先查再寫在併發下會漏（玩家連點兩次召喚就夠了）。

    **不加共鳴值。** 共鳴的來源只有任務完成（+20）與地標拍照（+10）兩種，召喚不在
    其中。送圖是紀念，不是計分。

    回傳值只給測試與 log 用；呼叫端不該依它改變行為——「本來就有了」跟「剛剛才給」
    對玩家是同一件事。
    """
    item_id = encounter_item_id(spirit_id)
    granted = _insert_if_absent(db, player_id=player_id, item_id=item_id)
    if granted:
        logger.info("collection granted: player=%s item=%s", player_id, item_id)
    return granted


def grant_arc_completion_collectible(
    db: Session, *, player_id: uuid.UUID | str, arc_id: str
) -> bool:
    """
    發放某條 arc 的劇本完成圖。與 `grant_encounter_collectible()` 同一套語意。

    ⚠️ **還沒有呼叫端。** 萬華 arc 的 `beat_finale` 要等 Lead 改完劇本機制才定案
    （2026-08-18 狀態），所以這裡先把發放路徑備好、由測試涵蓋，接上去時只要在
    beat 完成的交易裡叫這一支。先寫是為了讓兩種格子走完全同一條路，不會日後長成
    兩套語意不同的發放邏輯。
    """
    return _insert_if_absent(db, player_id=player_id, item_id=arc_completion_item_id(arc_id))


def _insert_if_absent(db: Session, *, player_id: uuid.UUID | str, item_id: str) -> bool:
    """
    冪等寫入一列收藏，回傳「這次是否真的插進去」。

    ## 🔴 用 RETURNING 判斷，**不要用 `result.rowcount`**

    2026-08-18 踩到：`rowcount` 對這種語句回傳 **`-1`**（psycopg3 表示「不知道」），
    而 `bool(-1)` 是 `True`——所以「有沒有插進去」永遠回 True，重複發放看起來像
    成功發放。**去重本身是好的**（唯一索引照樣擋住，資料只有一列），壞掉的只有回傳值，
    所以它不會讓玩家多拿東西，只會讓 log 與測試說謊。

    `ON CONFLICT DO NOTHING ... RETURNING` 在跳過時回傳 **0 列**，`first()` 就是
    `None`——這是明確的訊號，不依賴驅動的 rowcount 語意。

    `illustration_asset_id` 刻意留 NULL，見模組 docstring：圖屬於定義，不屬於玩家。
    """
    stmt = (
        pg_insert(models.PlayerInventory.__table__)
        .values(
            player_id=player_id,
            item_type=ITEM_TYPE_COLLECTIBLE,
            item_id=item_id,
            illustration_asset_id=None,
        )
        .on_conflict_do_nothing(index_elements=["player_id", "item_id"])
        .returning(models.PlayerInventory.__table__.c.inventory_id)
    )
    inserted = db.execute(stmt).first()
    db.commit()
    return inserted is not None


def list_collections(db: Session, *, player_id: uuid.UUID | str) -> list[dict]:
    """
    回傳完整的收藏清單：所有格子的定義，加上該玩家的解鎖狀態。

    順序是穩定的：先全部相遇格（依 `spirit_id`），再全部劇本格（依 `arc_id`）。
    客戶端靠 `source_type` 分成 3×3 方陣與下方的劇本區，不需要自己排序或分類。

    ⚠️ **排序目前是按 id 字典序**，不是人為安排的順序。3×3 方陣裡哪一格排哪裡
    因此是任意的（穩定，但任意）。要指定順序的話，`spirits` 得加一個 `sort_order`
    欄位——那是一次獨立的決定，沒有跟這批一起做。
    """
    unlocked = {
        item_id: acquired_at
        for item_id, acquired_at in db.query(
            models.PlayerInventory.item_id, models.PlayerInventory.acquired_at
        )
        .filter(models.PlayerInventory.player_id == player_id)
        .filter(models.PlayerInventory.item_type == ITEM_TYPE_COLLECTIBLE)
        .all()
    }

    entries: list[dict] = []

    # ── 相遇格：啟用中的地標，各一張 ────────────────────────────────
    encounter_rows = (
        db.query(models.Spirit, models.MediaAsset.cdn_url)
        .outerjoin(
            models.MediaAsset,
            (models.MediaAsset.spirit_id == models.Spirit.spirit_id)
            & (models.MediaAsset.asset_type == ASSET_TYPE_ENCOUNTER),
        )
        .filter(models.Spirit.is_active.is_(True))
        .order_by(models.Spirit.spirit_id)
        .all()
    )
    for spirit, cdn_url in encounter_rows:
        entries.append(
            _entry(
                collection_id=encounter_item_id(spirit.spirit_id),
                source_type=SOURCE_ENCOUNTER,
                title=spirit.display_name,
                image_url=cdn_url,
                acquired_at=unlocked.get(encounter_item_id(spirit.spirit_id)),
                is_unlocked=encounter_item_id(spirit.spirit_id) in unlocked,
            )
        )

    # ── 劇本格：每條 arc 一張 ──────────────────────────────────────
    arc_rows = (
        db.query(StoryArc, models.MediaAsset.cdn_url)
        .outerjoin(models.MediaAsset, models.MediaAsset.asset_id == StoryArc.completion_asset_id)
        .order_by(StoryArc.arc_id)
        .all()
    )
    for arc, cdn_url in arc_rows:
        entries.append(
            _entry(
                collection_id=arc_completion_item_id(arc.arc_id),
                source_type=SOURCE_ARC_COMPLETION,
                title=arc.title,
                image_url=cdn_url,
                acquired_at=unlocked.get(arc_completion_item_id(arc.arc_id)),
                is_unlocked=arc_completion_item_id(arc.arc_id) in unlocked,
            )
        )

    return entries


def _entry(
    *,
    collection_id: str,
    source_type: str,
    title: str,
    image_url: str | None,
    acquired_at,
    is_unlocked: bool,
) -> dict:
    """
    🔒 未解鎖時把 `title` 與 `image_url` 抹成 None。

    收斂在同一個地方，是為了不讓「哪些欄位算內容」這件事散落在兩段迴圈裡——
    之後加第三種來源時，漏掉遮蔽的機會就少一次。
    """
    if not is_unlocked:
        return {
            "collection_id": collection_id,
            "source_type": source_type,
            "unlocked": False,
            "title": None,
            "image_url": None,
            "acquired_at": None,
        }

    return {
        "collection_id": collection_id,
        "source_type": source_type,
        "unlocked": True,
        "title": title,
        "image_url": image_url,
        "acquired_at": acquired_at,
    }
