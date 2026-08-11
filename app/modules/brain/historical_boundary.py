"""
B5．史實邊界規則注入（issue #19；CONTEXT.md「史實邊界」）。

供 B2 Prompt 組裝引擎（issue #12）注入 System Instruction 的第 2 順位：一段
「不對敏感歷史武斷定論」的通則文字，跟人格卡裡屬於單一角色的補充規則疊加。

刻意設計成獨立的規則文字提供者，不綁 Gemini、不綁 B2：規則文字本身是常數，
不是生成出來的；`get_historical_boundary_rules()` 是純函式，兩次呼叫回傳
完全相同的字串，測試才能直接斷言內容片段而不用碰資料庫或 GCP 憑證。

## 跟人格卡個別史實規則的關係——`imagination_license` 而不是 `factual_boundary`

issue #19 原文寫的是跟人格卡的 `factual_boundary` 欄位疊加，但那是照著三層
schema 重構（`Replace persona_cards with the three brain layers`）**之前**的
單一 JSONB 人格卡寫的。`CharacterPersona` 現在沒有 `factual_boundary` 這個
欄位；最接近的是 `imagination_license`（「虛構授權：神祕感的來源，以及不能
宣稱什麼」）——同樣是在講這個角色對「什麼算史實、什麼是想像」的個別立場，
用途重疊。`combine_with_persona_boundary()` 疊加的就是這個欄位。

**設計上刻意讓這個欄位只出現在這裡（第 2 順位），不再重複出現在 B2 組裝
人格卡本體（第 1 順位）的區塊裡**——跟 B4 的安全規則「不重複塞進 system
instruction」是同一個理由：同一段文字出現在兩個位置，只會多花 token、
稀釋權重，不會讓角色更遵守規則。B2 組裝第 1 順位時，`imagination_license`
要跳過，理由寫在 `prompt_builder.py`。
"""

_GENERAL_RULES = (
    "談到歷史事件時，遵守以下三條：\n"
    "1. 不對有爭議的歷史事件下武斷結論——承認「說法不只一種」比給出唯一答案更符合事實。\n"
    "2. 不替爭議性議題選邊——不表態誰對誰錯、誰該負責。\n"
    "3. 不編造未經證實的細節——不知道的部分就說不知道，不要為了讓故事完整而虛構年份、"
    "人名或事件經過。"
)


def get_historical_boundary_rules() -> str:
    """
    全靈魂共用的史實邊界通則（issue #19 AC1／AC2）。

    純函式、內容是模組層級常數，不含時間戳或隨機成分，兩次呼叫保證回傳同一個
    字串物件。文案本身標記為待審核（AC7）：集中在 `_GENERAL_RULES` 這一處，
    敘事／史實負責人要改文案只動這裡，`get_historical_boundary_rules()` 與
    `combine_with_persona_boundary()` 的呼叫方式都不需要跟著變。
    """
    return _GENERAL_RULES


def combine_with_persona_boundary(persona_imagination_license: str | None) -> str:
    """
    通則跟人格卡個別的史實／想像邊界疊加（issue #19 AC3／AC4）。

    `persona_imagination_license` 對應 `CharacterPersona.imagination_license`
    （見模組開頭說明），由呼叫端（B2）傳入——這支模組不碰資料庫，不知道
    `CharacterPersona` 這個型別存不存在，只認得一個可能是 None 的字串。

    通則永遠在前、個別規則永遠在後，且兩者都完整保留、互不覆蓋：疊加不是
    「有個別規則就用個別規則」，兩者要解決的是不同層次的問題（全靈魂共用的
    行為準則 vs. 這個角色自己的史實立場），拿掉任何一邊都會漏掉一種情境。

    `persona_imagination_license` 為 None 或空字串（欄位不存在、或人格卡還
    沒填這段）時只回傳通則，不拋例外——人格卡是人工編輯的內容，缺欄位不該讓
    對話端點掛掉（同 B12 `find_canned_response` 的處理原則）。
    """
    general = get_historical_boundary_rules()
    if not persona_imagination_license:
        return general
    return f"{general}\n\n{persona_imagination_license}"
