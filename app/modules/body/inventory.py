"""
道具查詢（待辦 P3 第 21 項）。

## 為什麼需要這支

`player_inventory` 在這之前是**只寫不讀**的：`/districts/check-entry` 走進萬華
會發一封信、`story_beats.required_item_ids` 拿它當解鎖條件，但沒有任何端點讓
玩家看得到自己有什麼。信發了，玩家從頭到尾不知道。

## 內容從 `brain.story_strings` 來，不另建一張表

那封信的正文（`wanhua.prologue.letter_body`）早就在 story_strings 裡，是走
`scripts/import_story_strings.py` 匯入、經人工審核的內容。再開一張道具文案表
會變成同一段文字有兩個真相，而**兩個真相就是遲早會不一致的那種問題**。

## 對照表寫在程式碼裡，跟發放那一份同一個層級

`_ITEM_TEXT_KEYS` 與 `router._DISTRICT_ENTRY_ITEMS` 是一對：一個決定「走進哪一
區發哪個道具」，一個決定「那個道具要顯示哪一段文字」。兩者都是**少數幾筆的
結構性對照**，不是會長大的內容——內容在 story_strings 那邊。

沒有對照的道具不是錯誤：徽章、紀念品本來就可能只有 id 沒有長文。那時候
`story_text` 是 null，客戶端顯示名稱就好。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.body.models import PlayerInventory
from app.modules.brain.models import StoryString

# 道具 → 它的正文在 story_strings 裡的 key。
#
# ⚠️ 加新道具時，這裡與 `router._DISTRICT_ENTRY_ITEMS`（或發放它的那條路徑）
# 要一起改。少了這裡只會讓玩家看到一個沒有內容的項目，不會有任何錯誤浮上來。
_ITEM_TEXT_KEYS = {
    "item_wanhua_letter": "wanhua.prologue.letter_body",
}


@dataclass(frozen=True)
class InventoryItem:
    """玩家持有的一件道具。frozen——組好之後不該在回應前被改寫。"""

    item_id: str
    item_type: str
    acquired_at: object
    source_quest_id: str | None
    story_text: str | None


def list_items(db: Session, player_id) -> list[InventoryItem]:
    """
    這個玩家持有的所有道具，最近取得的排前面。

    玩家從 session token 解出來，**不從參數指定**——沒有任何呼叫方式可以查到
    別人的道具（同 `/profile` 的規則）。

    文字用一次查詢取回，不是每件道具各查一次：道具數量在 MVP 很小，但「迴圈裡
    打資料庫」是那種在資料變多之前都看不出來的東西。
    """
    rows = (
        db.query(PlayerInventory)
        .filter_by(player_id=player_id)
        .order_by(PlayerInventory.acquired_at.desc())
        .all()
    )
    if not rows:
        return []

    wanted = {
        _ITEM_TEXT_KEYS[row.item_id] for row in rows if row.item_id in _ITEM_TEXT_KEYS
    }
    texts: dict[str, str] = {}
    if wanted:
        found = (
            db.query(StoryString)
            .filter(StoryString.text_key.in_(wanted), StoryString.active.is_(True))
            .all()
        )
        texts = {row.text_key: row.text for row in found}

    return [
        InventoryItem(
            item_id=row.item_id,
            item_type=row.item_type,
            acquired_at=row.acquired_at,
            source_quest_id=row.source_quest_id,
            story_text=texts.get(_ITEM_TEXT_KEYS.get(row.item_id, "")),
        )
        for row in rows
    ]
