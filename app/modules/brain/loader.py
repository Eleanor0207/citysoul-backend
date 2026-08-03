"""
B3．人格卡載入與版本管理。

CONTEXT.md 定義：人格卡是「經人工審核的角色定義」，LLM 只能協助草擬，
所以這裡完全沒有「LLM 生成人格卡」的路徑——is_active 只能由人工審核流程
（未來的後台工具，不在這支程式範圍）去 flip，這支函式只負責讀。
"""
from sqlalchemy.orm import Session

from app.modules.brain.models import PersonaCard


def load_active_persona_card(db: Session, spirit_id: str) -> PersonaCard | None:
    """
    載入某個城市靈魂目前生效的人格卡版本。
    找不到就回傳 None——呼叫端（未來的 generate_dialogue_response）要處理
    「沒有生效人格卡」的狀況，不能假設一定存在。
    """
    return (
        db.query(PersonaCard)
        .filter_by(spirit_id=spirit_id, is_active=True)
        .order_by(PersonaCard.version.desc())
        .first()
    )
