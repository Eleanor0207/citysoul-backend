"""
B4．角色安全邊界檢查層（issue #11；SDD 第9節；CONTEXT.md「角色安全邊界」與
「宗教場域邊界」）。

玩家的自由文字輸入先經這一層過濾，這是輸入端的第一道關卡——排在 Prompt 組裝
（B2）之前，判定為不安全就直接回婉拒，完全不繼續走 B2／B1 生成。「不呼叫下游」
是這一層存在的意義：只回婉拒但仍送出生成請求的話，成本與風險都沒省到（見
`enforce_safety_boundary`）。這不是把規則塞進 system instruction 裡指望模型
自己遵守，是在送進模型之前就先擋下來。

## 為什麼分類邏輯呼叫 B1，婉拒文案卻是寫死的模板

真實判定「這句話安不安全」呼叫 B1（GeminiClient，issue #8）分類——這件事需要
語意判斷，規則沒辦法窮舉每一種問法。但婉拒之後回什麼話，不需要每次都現生成：
那是人格、語氣的事，應該經敘事負責人審核過再固定下來，不該讓模型每次臨場發揮
（實際文案待審核，見 `_REFUSAL_TEMPLATES` 開頭的說明；本票先用簡單模板但語氣
方向要對）。分類跟文案分開，也讓「B1 判定不安全但婉拒文字寫錯」跟「B1 判定錯誤」
是兩種完全不同、可以分開除錯的失敗。

## 跟 B1（GeminiClient，issue #8）的關係

`GeminiSafetyChecker` 直接依賴 `app.modules.brain.gemini.GeminiClient`（B1 已
落地，見該模組）。B1 的 `generate()` 有一個關鍵性質：**永遠回傳非空字串、
永遠不拋例外**——呼叫失敗或逾時時它自己回退到 `FALLBACK_REPLY` 那句對話台詞，
不是回傳分類這支模組看得懂的 `SAFE`／`UNSAFE:...`。這裡不需要特別處理那個
情況：`_parse_classification` 對任何看不懂的字串本來就保守判定為不安全，
B1 失敗時自然落在同一條路徑——「分類失敗」跟「分類看不懂」在這支模組眼中
是同一件事，都是「先擋下來比較安全」。

## 跟 #41（龍山寺內容治理）的關係

宗教相關的婉拒規範原本要由 #41 產出正式的敏感情境清單，但 #41 目前仍是
open。這裡先以 CONTEXT.md「宗教場域邊界」已經審核過的四條安全下限為準——
該段明訂「這四條是人格卡的安全下限，敘事審核只能往上加，不能移除」，在 #41
交付更細緻的清單之前引用它是安全的，且 #41 與本票（#11）的驗收標準都直接
點名了同一組範例情境（求籤吉凶、神明是否顯靈、不同信仰比較）。#41 完成後，
`_CLASSIFICATION_GUIDANCE` 與 `_REFUSAL_TEMPLATES` 要換成它的正式產出，
機制本身不需要跟著變。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from app.modules.brain.gemini import GeminiClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyResult:
    """
    issue #11 AC1 訂的回傳形狀：`is_safe` 為 False 時必須帶婉拒文字，
    為 True 時不該帶——用 `__post_init__` 把這條規則變成建構時就會炸的
    不變量，不要留給呼叫端自己記得檢查。
    """

    is_safe: bool
    refusal_text: str | None = None

    def __post_init__(self) -> None:
        if self.is_safe and self.refusal_text is not None:
            raise ValueError("安全的結果不該帶婉拒文字")
        if not self.is_safe and not self.refusal_text:
            raise ValueError("不安全的結果必須帶婉拒文字")


class SafetyChecker(Protocol):
    """
    呼叫端只依賴這個介面（issue #11 AC1）：真實實作（`GeminiSafetyChecker`）
    與測試用 fake 都只要符合這個方法簽名即可，不需要共同繼承同一個基底類別。
    """

    def check(self, user_input: str) -> SafetyResult: ...


class _SafetyCategory(str, Enum):
    """
    決定套用哪個婉拒模板的內部分類。刻意不放進 `SafetyResult`——呼叫端
    （issue #11 AC 描述的範圍）只在乎安不安全跟婉拒文字是什麼，分類是這支
    模組挑模板用的內部細節，不是對外承諾的介面形狀。
    """

    HIGH_RISK_ADVICE = "high_risk_advice"  # 具體醫療／法律／財務建議
    RELIGIOUS_BOUNDARY = "religious_boundary"  # 神明代言／命理預測／信仰比較
    UNSAFE_OR_OFF_TOPIC = "unsafe_or_off_topic"  # 其他不適合、危險或偏離主題的內容


# 婉拒文案（issue #11 AC4：簡單模板，語氣方向要對，實際文案待敘事負責人審核）。
# 每句都刻意把話題帶回地標／任務／城市故事，不是「您的輸入違反使用規範」這種
# 系統訊息口吻。宗教類別的文案額外遵守 CONTEXT.md「宗教場域邊界」：不代替神明
# 給指示、不做吉凶裁決、不比較信仰優劣——所以寫法上刻意不對任何教義或靈驗與否
# 表態，只承認「這不是我能回答的」再帶開話題。
_REFUSAL_TEMPLATES: dict[_SafetyCategory, str] = {
    _SafetyCategory.HIGH_RISK_ADVICE: (
        "這種事我沒辦法替你拿主意，那不是我在這裡陪你的方式。"
        "不過眼前這方寺埕，倒是能陪你走一段——要不要先看看今天的任務？"
    ),
    _SafetyCategory.RELIGIOUS_BOUNDARY: (
        "求籤的結果準不準、心願能不能達成，這些不是我能替你判定的事，"
        "我只是記得住這裡發生過的事情，與來來往往的人心。"
        "你眼前這座廟，倒有些故事可以說給你聽。"
    ),
    _SafetyCategory.UNSAFE_OR_OFF_TOPIC: (
        "這個話題，我這裡接不住。你現在人在這裡，要不要先跟我說說你眼前看到的？"
    ),
}


def _unsafe_result(category: _SafetyCategory) -> SafetyResult:
    return SafetyResult(is_safe=False, refusal_text=_REFUSAL_TEMPLATES[category])


_SAFE_RESULT = SafetyResult(is_safe=True)


# 分類判準：CONTEXT.md「角色安全邊界」（不提供高風險專業建議）與「宗教場域邊界」
# （不代替神明給指示或應許、不預測個人吉凶姻緣財運、不比較宗教或信仰優劣、不提供
# 具體醫療法律投資建議）。範例情境直接取自 issue #11／#41 已經點名的例子，#41
# 交付正式清單後在這裡擴充即可，格式不用變。
_CLASSIFICATION_GUIDANCE = """\
你是「龍山寺城市靈魂」對話系統的安全分類器，不是在跟玩家對話。
判斷下面這句玩家輸入，是否落在以下任一條不安全類別：

- high_risk_advice：要求具體的醫療診斷、法律訴訟建議，或財務／投資建議
  （例如「這是不是癌症」「我可以告他嗎」「該買哪支股票」）
- religious_boundary：要求代替神明給指示或應許、預測個人吉凶姻緣財運、
  或比較不同宗教信仰的優劣（例如求籤吉凶判定、神明是否顯靈、不同信仰比較）
- unsafe_or_off_topic：其他不適合、危險，或明顯偏離地標／任務／城市故事的內容
  （例如自傷、暴力、色情、要求無限制角色扮演）

不屬於以上任何一類，就是安全的。

玩家輸入：{user_input}

只能回覆下面四個字串其中一個，不要加任何其他文字、標點或說明：
SAFE
UNSAFE:high_risk_advice
UNSAFE:religious_boundary
UNSAFE:unsafe_or_off_topic
"""


def _build_classification_prompt(user_input: str) -> str:
    return _CLASSIFICATION_GUIDANCE.format(user_input=user_input)


def _parse_classification(raw: str) -> SafetyResult:
    """
    解析 B1 的分類回應。刻意只認得上面模板列出的四種精確字串——分類本身失敗
    或模型回了看不懂的東西時，一律保守判定為不安全（`UNSAFE_OR_OFF_TOPIC`）：
    安全層「錯放行」的代價遠高於「錯擋下」，預設值必須偏保守，不能是「看不懂
    就當作安全」。
    """
    normalized = raw.strip()

    if normalized == "SAFE":
        return _SAFE_RESULT

    for category in _SafetyCategory:
        if normalized == f"UNSAFE:{category.value}":
            return _unsafe_result(category)

    logger.warning("safety classification 收到無法辨識的回應，保守判定為不安全: %r", raw)
    return _unsafe_result(_SafetyCategory.UNSAFE_OR_OFF_TOPIC)


class GeminiSafetyChecker:
    """
    真實實作（issue #11 AC1）：分類工作交給注入的 B1 `GeminiClient`，這支類別
    本身只負責組 prompt 跟解析回應，不直接依賴 Vertex AI 或任何 GCP SDK——
    那些細節留在 B1 內部。測試注入 B1 的 `FakeGeminiClient`（或任何符合
    `GeminiClient` 介面的 fake）即可，完全不需要真實 GCP 憑證。
    """

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    def check(self, user_input: str) -> SafetyResult:
        raw = self._client.generate(_build_classification_prompt(user_input))
        return _parse_classification(raw)


def enforce_safety_boundary(
    checker: SafetyChecker, user_input: str, generate_reply: Callable[[], str]
) -> str:
    """
    安全邊界守門：不安全就回婉拒文字，安全才呼叫 `generate_reply()`（B2 組裝
    ＋ B1 生成，issue #12／#8）取得真正的回覆。

    `generate_reply` 刻意是無參數 callable（thunk）而不是直接傳生成好的結果：
    如果呼叫端要先把結果算出來才能傳進來，B2／B1 早就跑過了，「不安全時一次
    都不呼叫下游」這件事在呼叫端的寫法上就已經不可能成立（issue #11 AC2）。
    這支函式之後會被完整版對話端點（#42／#45）直接呼叫，取代它們現在各自
    重寫一次「檢查安不安全再決定要不要生成」的邏輯。
    """
    result = checker.check(user_input)
    if not result.is_safe:
        return result.refusal_text
    return generate_reply()
