"""
B3．人格載入與版本管理。

CONTEXT.md 定義：人格是「經人工審核的角色定義」，LLM 只能協助草擬，
所以這裡完全沒有「LLM 生成人格」的路徑——`active` 只能由人工審核流程
（未來的後台工具，不在這支程式範圍）去 flip，這支模組只負責讀。

0005 之前這裡讀的是 `brain.persona_cards` 那張 JSONB 卡片；現在讀的是三層裡
的 `character_personas`，經由 `spirits.character_id` 找到角色。
"""
from sqlalchemy.orm import Session

from app.modules.body.models import Spirit
from app.modules.brain.models import CannedGreeting, CharacterPersona


def character_id_for_spirit(db: Session, spirit_id: str) -> str | None:
    """
    地標靈魂 → 角色 id。

    `spirits.character_id` 是**值關聯**，不是外鍵（WBS-API 決策4），所以這裡
    是兩次查詢而不是一個 join——身體的表與腦袋的表不在同一個關聯圖上，
    這是刻意的成本。
    """
    spirit = db.query(Spirit).filter_by(spirit_id=spirit_id).first()
    return spirit.character_id if spirit is not None else None


def load_active_persona(db: Session, spirit_id: str) -> CharacterPersona | None:
    """
    載入某個城市靈魂目前生效的人格版本。

    找不到就回傳 None——呼叫端要處理「沒有生效人格」的狀況，不能假設一定存在。
    這在封閉測試期是常態而不是例外：人格草稿寫好之後，在人工審核通過之前
    一直都是 `active=False`。

    「一個角色只有一個生效版本」由 partial unique index 保證，所以這裡不需要
    `order_by(version.desc())` 去挑一個——挑的動作本身就意味著可能有多筆，
    而那在資料庫層級已經不可能了。
    """
    character_id = character_id_for_spirit(db, spirit_id)
    if character_id is None:
        return None

    return (
        db.query(CharacterPersona).filter_by(character_id=character_id, active=True).first()
    )


def load_canned_greetings(db: Session, persona: CharacterPersona) -> list[CannedGreeting]:
    """某個人格版本底下的預寫台詞。台詞跟著版本走，不是跟著角色走。"""
    return (
        db.query(CannedGreeting)
        .filter_by(character_id=persona.character_id, version=persona.version)
        .all()
    )
