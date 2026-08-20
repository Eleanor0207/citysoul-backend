"""
B12．快速問候比對層（混合式聊天的第一道關卡，SDD 第7.6節）。

玩家輸入先比對該人格版本的預寫台詞：命中就直接回，**不呼叫 Gemini**
（零成本、零延遲）；沒命中才由呼叫端繼續走 B4→B2→B1→B10 完整流程。

## 為什麼是「完全相等」而不是「包含」

驗收標準寫的是「完全命中」。用子字串比對會壞掉：玩家打
「你好，龍山寺是什麼時候蓋的？」時，如果因為裡面含有「你好」就回預寫的招呼語，
那個真正的問題就被吃掉了——玩家會覺得靈魂在敷衍他。招呼語只該在玩家真的只是
打招呼時出現。

## 正規化做到哪裡（2026-08-20 修訂）

做三件事：去頭尾空白、英文大小寫不分、**去掉尾端標點**。
**不**做全形半形轉換——那個仍然是會產生意外命中的啟發式規則。

### 尾端標點原本是不去的，為什麼改

原本的理由是「預寫台詞是人工審核過的內容，什麼時候該出現應該由審核的人決定」，
漏接的處理方式是請敘事負責人把「你好！」也加進 `trigger_phrases`。

實際盤點後推翻：漏接的不是少數幾個變體，而是**組合爆炸**。九張人格卡、每張
三到四組、每組十幾個觸發語，每一個都可能配上 `！？。～…` 的任意組合與重複
（「你好！！」「你好～～」）。要靠人工窮舉，清單會膨脹到沒有人審得動——
那時候「看得見、可審核」就只剩名義上成立。

**去尾端標點不會製造意外命中**，因為比對仍然是完全相等：
「你好，龍山寺是什麼時候蓋的？」正規化之後是「你好，龍山寺是什麼時候蓋的」，
跟「你好」還是不相等。被吃掉真正問題的那個風險，靠的是「完全相等」這件事，
不是靠保留標點。

全形半形轉換維持不做：那個會讓「ＡＢＣ」命中「abc」，屬於真的在猜。

## 0005 之後拿掉的一段防禦性解析

台詞原本住在 `persona_cards.content` 這個 JSONB 裡，任何一筆都可能不是 dict、
`response_text` 可能不是字串，所以這裡有一整段「格式不對就跳過這一筆」的邏輯。
現在台詞是 `brain.canned_greetings` 的資料列，`response_text TEXT NOT NULL`、
`trigger_phrases TEXT[] NOT NULL`——那些壞狀態在資料庫層級就寫不進來，程式碼
不需要再防一次。**用 schema 讓壞狀態無法表示，比在讀取端反覆檢查更可靠。**
"""
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.modules.brain.loader import load_active_persona, load_canned_greetings
from app.modules.brain.models import CannedGreeting


# 只列**句末**會出現的標點與空白。逗號、頓號、分號也在內：它們單獨結尾時
# （「你好，」）跟驚嘆號沒有差別。中間出現的逗號不受影響——這裡只 rstrip。
_TRAILING = "！!？?。．.，,、；;：:～~—–-…‥　 \t"


def _normalize(text: str) -> str:
    """
    去頭尾空白 → 英文大小寫不分 → 去掉尾端標點。

    順序有意義：先 strip 才 rstrip 標點，否則「你好！ 」（標點後還有空白）
    會停在空白上，標點去不掉。
    """
    return text.strip().casefold().rstrip(_TRAILING)


def find_canned_response(
    greetings: Iterable[CannedGreeting], user_input: str
) -> str | None:
    """純比對邏輯，不碰資料庫。命中回傳預寫台詞，未命中回傳 None。"""
    normalized_input = _normalize(user_input)
    if not normalized_input:
        return None

    for greeting in greetings:
        for trigger in greeting.trigger_phrases:
            if _normalize(trigger) == normalized_input:
                return greeting.response_text

    return None


def match_canned_greeting(db: Session, spirit_id: str, user_input: str) -> str | None:
    """
    載入該靈魂目前生效的人格版本，比對玩家輸入。

    沒有生效人格時視為未命中（回 None），不拋例外——呼叫端本來就要處理
    「沒有生效人格」這個情況（見 B3 `load_active_persona` 的 docstring），
    這一層不該是它第一次踩到那件事的地方。
    """
    persona = load_active_persona(db, spirit_id)
    if persona is None:
        return None

    return find_canned_response(load_canned_greetings(db, persona), user_input)
