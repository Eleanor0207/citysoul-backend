"""
B12．快速問候比對層（混合式聊天的第一道關卡，SDD 第7.6節）。

玩家輸入先比對人格卡的 `canned_greetings`：命中就直接回預寫台詞，**不呼叫
Gemini**（零成本、零延遲）；沒命中才由呼叫端繼續走 B4→B2→B1→B10 完整流程。

## 為什麼是「完全相等」而不是「包含」

驗收標準寫的是「完全命中」。用子字串比對會壞掉：玩家打
「你好，天文館是什麼時候蓋的？」時，如果因為裡面含有「你好」就回預寫的招呼語，
那個真正的問題就被吃掉了——玩家會覺得靈魂在敷衍他。招呼語只該在玩家真的只是
打招呼時出現。

## 這裡刻意不做的正規化

只做「去頭尾空白」與「英文大小寫不分」。**不**去標點、**不**做全形半形轉換：
那些都是會產生意外命中的啟發式規則，而預寫台詞是人工審核過的內容，什麼時候
該出現應該由審核的人決定，不是由一層猜測邏輯決定。

代價是「你好！」不會命中「你好」。如果實際運作後發現這種漏接很常見，正確的
處理方式是請敘事負責人把「你好！」也加進 `trigger_phrases`，而不是在這裡加
猜測規則——前者看得見、可審核，後者不行。
"""
from typing import Any

from sqlalchemy.orm import Session

from app.modules.brain.loader import load_active_persona_card


def _normalize(text: str) -> str:
    return text.strip().casefold()


def find_canned_response(canned_greetings: Any, user_input: str) -> str | None:
    """
    純比對邏輯，不碰資料庫。命中回傳預寫台詞，未命中回傳 None。

    對格式異常的資料一律當作「這一筆不算數」跳過，而不是拋例外：
    `canned_greetings` 是人工編輯的 JSONB 內容，某一筆打錯字不該讓整個對話
    端點掛掉——那會把一個內容問題變成一次服務中斷。
    """
    if not isinstance(canned_greetings, list):
        return None

    normalized_input = _normalize(user_input)
    if not normalized_input:
        return None

    for entry in canned_greetings:
        if not isinstance(entry, dict):
            continue

        response_text = entry.get("response_text")
        if not isinstance(response_text, str) or not response_text:
            continue

        triggers = entry.get("trigger_phrases")
        if not isinstance(triggers, list):
            continue

        for trigger in triggers:
            if isinstance(trigger, str) and _normalize(trigger) == normalized_input:
                return response_text

    return None


def match_canned_greeting(db: Session, spirit_id: str, user_input: str) -> str | None:
    """
    載入該靈魂目前生效的人格卡，比對玩家輸入。

    沒有生效人格卡時視為未命中（回 None），不拋例外——呼叫端本來就要處理
    「沒有人格卡」這個情況（見 B3 `load_active_persona_card` 的 docstring），
    這一層不該是它第一次踩到那件事的地方。
    """
    card = load_active_persona_card(db, spirit_id)
    if card is None:
        return None

    content = card.content
    if not isinstance(content, dict):
        return None

    return find_canned_response(content.get("canned_greetings"), user_input)
