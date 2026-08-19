"""
B4．角色安全邊界檢查層（issue #11）。

對應 CONTEXT.md「角色安全邊界」：遇到不適合、危險或偏離主題的內容，城市靈魂
以**符合人格的方式婉拒並帶回地標、任務或城市故事**，不提供高風險專業建議。

## 位置：輸入端，排在 B2 之前

這一層在 Dialogue 流程裡排在 Prompt 組裝（B2）**之前**（SDD 第9節）。它是
輸入端先過濾，不是把「請不要回答醫療問題」塞進 system instruction 裡。

差別在於**下游有沒有被呼叫**。塞進 system instruction 的話，每一次危險提問
仍然要跑完整的 B2 組裝與 B1 生成——成本照付、風險照擔，只是多了一句請求模型
自律。而自律是機率性的。

`SafetyGate` 就是為了讓「不安全時下游一次都不會被呼叫」這件事**可被測試**而
存在的。少了它，這個保證只活在呼叫端的 if 判斷裡，沒有東西守著。

## 失敗時往嚴格的方向倒（fail-closed）

分類本身要呼叫 B1，而 B1 會失敗。失敗時 `GeminiSafetyChecker` 判定為**不安全**，
不是放行。

理由是代價不對稱：誤擋一句「這座廟什麼時候蓋的」，玩家看到一句溫和的轉向；
誤放一句自傷相關的提問，後果不在同一個量級。

這跟 B1「失敗不得中斷召喚流程」不衝突——婉拒文案本身就是人工預寫台詞，玩家
仍然拿得到一句符合人格的回應，流程沒有中斷，只是這段期間角色會比較保守。

⚠️ **代價要講清楚**：Gemini 全面中斷時，所有對話都會變成婉拒。這是刻意的
取捨，不是 bug。要改成 fail-open 是團隊決策，改 `_FAIL_CLOSED_RESULT` 一處即可。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from app.modules.brain.gemini import GeminiClient

logger = logging.getLogger(__name__)


# 🔒 婉拒文案審核狀態。與 B5 同一個慣例（historical_boundary.RULES_REVIEW_STATUS）。
#
# backend #41 已由 Lead 決定關閉（not planned），MVP 先暫時沿用這批婉拒文案。
# 這不代表完成正式敘事審核；狀態值與 personas 表的 MVP_NO_REVIEW 決策一致，
# 也不再把文案標成等待一張已關閉的票。
REFUSAL_REVIEW_STATUS = "MVP_NO_REVIEW"


class SafetyCategory:
    """
    分類標籤。用字串常數而不是 Enum，因為它要能寬鬆地接住模型回傳的文字——
    模型偶爾會回小寫、回多餘空白，Enum 在那種時候只會拋例外。
    """

    SAFE = "safe"
    SELF_HARM = "self_harm"
    MEDICAL = "medical"
    LEGAL = "legal"
    FINANCIAL = "financial"
    RELIGIOUS_DOCTRINE = "religious_doctrine"
    # 政治立場。跟 RELIGIOUS_DOCTRINE 同一個道理：不是把「請保持中立」寫進
    # system instruction 請模型自律，而是在生成之前就攔下來。
    #
    # 加這一類的理由是**故宮**（見 citysoul-doc 的 landmark/national_palace_museum.md
    # §6）：文物遷臺的史觀定性、歸還爭議、機構名稱與去中國化討論，都是玩家
    # **會主動反覆問**的方向。人格卡的 taboos 擋得住偶發的滑坡，擋不住有人一直問。
    #
    # 風險量級也不同：宗教講錯冒犯信眾，政治講錯是一張截圖變成新聞，而且會被
    # 當成整個專案的立場。
    POLITICAL_STANCE = "political_stance"
    OTHER = "other"


@dataclass(frozen=True)
class SafetyResult:
    """
    `is_safe` 為 True 時 `refusal_text` 必定是 None，反之必定有值。

    frozen 是刻意的：這是一個判定結果，不該在傳遞過程中被改寫。
    """

    is_safe: bool
    category: str = SafetyCategory.SAFE
    refusal_text: str | None = None


# 婉拒文案（#41 交付物 C）。
#
# ⚠️ 語氣方向是對的，但**文案本身待敘事負責人審核**。這裡示範的是結構：
#     拒絕 → 帶回地標／人 → 邀請繼續說
#
# 刻意**不是**「您的輸入違反使用規範」那種系統訊息口吻。玩家站在廟埕前，
# 被一個系統錯誤訊息打斷，比沒有回應更破壞情境。
_REFUSALS = {
    # ⚠️ 自傷類**不套用「帶回地標」的模板**。
    #
    # 其他類別把話題轉回城市故事是恰當的；對一個可能正在求助的人這樣做，
    # 等於忽略他真正說的話。這裡優先表達在意並指向真實的人，地標語境退到最後。
    #
    # 🔴 **待人工補上經查證的求助專線。** 這裡刻意沒有寫任何電話號碼——
    # 寫錯一個號碼的傷害遠大於沒有寫。見 #11 留言與內容治理文件。
    SafetyCategory.SELF_HARM: (
        "你說的這件事，我沒有辦法輕輕帶過。"
        "我只是這條街的記憶，幫不上真正的忙——但請你找一個活著的人說說，"
        "家人、朋友，或是專業的協助者都好。"
        "我會在這裡，等你願意的時候再來。"
    ),
    SafetyCategory.MEDICAL: (
        "身體的事我不敢亂說，那得問醫生才算數。"
        "倒是這廟埕上來來去去的人，什麼樣的心事都帶過——"
        "你今天走這一趟，是為了什麼呢？"
    ),
    SafetyCategory.LEGAL: (
        "這種是非對錯我判斷不了，也不該由我判斷，找律師問會比較實在。"
        "不過人跟人之間的糾葛，這條街看了兩百多年了。要不要說說你的事？"
    ),
    SafetyCategory.FINANCIAL: (
        "錢的事我給不了建議，說錯了是要害人的。"
        "艋舺這地方倒是做了兩百年生意——起起落落的故事我知道不少，想聽嗎？"
    ),
    SafetyCategory.RELIGIOUS_DOCTRINE: (
        "這我不敢替誰回答，也不該替誰回答——廟裡有師父，說了才算數。"
        "我能說的是這座廟一路走來的樣子，還有來這裡的人。你想從哪裡聽起？"
    ),
    # ⚠️ 這一則刻意**不迴避問題本身**。
    #
    # 只說「我不談政治」會讓角色顯得心虛，而心虛看起來就像有立場只是不敢講。
    # 所以先承認問題存在，再把話題錨定在祂實際在做的事——那不是轉移，
    # 那真的是一個城市靈魂唯一有資格談的東西。
    #
    # 措辭上不出現任何政治共同體的名稱，包括用來否定的那種（「不是中國的也不是
    # 台灣的」同樣是在那個座標系裡回答）。
    SafetyCategory.POLITICAL_STANCE: (
        "這個問題我沒有立場，也不該有。我照看的是這些東西——誰做的、怎麼做的、"
        "經過了哪些人的手。至於它們該屬於誰，那是人要決定的事。"
        "你想聽聽其中一件的來歷嗎？"
    ),
    SafetyCategory.OTHER: (
        "這個我接不上話。"
        "不過你既然站在這裡了，要不要問問這座廟的事？我記得的比你想的多。"
    ),
}

# fail-closed 時用的結果。分類失敗不知道是哪一類，所以用最通用的那則。
_FAIL_CLOSED_RESULT = SafetyResult(
    is_safe=False,
    category=SafetyCategory.OTHER,
    refusal_text=_REFUSALS[SafetyCategory.OTHER],
)


def refusal_for(category: str) -> str:
    """取得某一類的婉拒文案。未知分類回傳通用那則，不拋例外。"""
    return _REFUSALS.get(category, _REFUSALS[SafetyCategory.OTHER])


class SafetyChecker(ABC):
    """
    抽象介面。呼叫端只依賴這個，所以測試注入 fake 就能跑，**不需要 GCP 憑證**。
    """

    @abstractmethod
    def check(self, user_input: str) -> SafetyResult:
        """判定輸入是否安全。不拋例外。"""


# 分類用的 prompt。
#
# 刻意要求模型只回一個標籤而不是自然語言解釋：這一層要的是可判定的結果，
# 不是可讀的說明。回應愈短，被截斷或漂移的空間愈小，成本也愈低。
_CLASSIFY_PROMPT = """你是一個輸入分類器。判斷以下玩家輸入屬於哪一類，只回答一個標籤，不要解釋。

標籤：
- safe：一般對話，包含詢問地標歷史、文化、任務、閒聊
- self_harm：自傷、輕生、傷害他人
- medical：詢問病症、診斷、治療、用藥
- legal：詢問法律責任、訴訟、權利義務
- financial：詢問投資、理財、金錢決策
- religious_doctrine：詢問教義解釋、神祇位階、是否靈驗、占卜結果、宗教優劣比較
- political_stance：詢問政治立場、國族或政治共同體歸屬、兩岸關係、文物或土地的歸屬爭議、政黨或政治人物評價、機構更名等政治爭議
- other：其他不適合或明顯偏離主題的內容

玩家輸入：
{user_input}

標籤："""


class GeminiSafetyChecker(SafetyChecker):
    """
    真實實作。用 B1 的 `GeminiClient` 做分類。

    注意它**收一個 `GeminiClient` 而不是自己建一個**：測試注入 `FakeGeminiClient`
    就能驗證解析與 fail-closed 行為，完全不碰 GCP。
    """

    def __init__(self, client: GeminiClient):
        self._client = client
        self.last_failure_reason: str | None = None

    def check(self, user_input: str) -> SafetyResult:
        self.last_failure_reason = None

        # 空白輸入不值得花一次模型呼叫。它也不危險，交給下游的驗證層處理
        # （`DialogueRequest` 已經擋掉空字串）。
        if not user_input or not user_input.strip():
            return SafetyResult(is_safe=True)

        raw = self._client.generate(_CLASSIFY_PROMPT.format(user_input=user_input.strip()))
        category = self._parse(raw)

        if category is None:
            # 解析不出標籤 = 分類失敗。包含 B1 回退時吐出 FALLBACK_REPLY 的情況——
            # 那串文字裡沒有任何標籤，所以自然落到這裡，不需要另外偵測。
            self.last_failure_reason = f"無法從模型回應解析分類標籤：{raw[:80]!r}"
            logger.warning("B4 分類失敗，往嚴格方向倒：%s", self.last_failure_reason)
            return _FAIL_CLOSED_RESULT

        if category == SafetyCategory.SAFE:
            return SafetyResult(is_safe=True)

        return SafetyResult(
            is_safe=False, category=category, refusal_text=refusal_for(category)
        )

    @staticmethod
    def _parse(raw: str) -> str | None:
        """
        從模型回應中找出標籤。

        寬鬆比對：模型會回 `safe`、`Safe`、`標籤：safe`、加句號等各種形狀。
        嚴格比對只會讓一個無害的格式差異變成一次 fail-closed 誤擋。

        比對順序刻意讓 `safe` 最後檢查——`self_harm` 之外的標籤都不含 "safe"
        子字串，但先檢查危險類別可以確保萬一模型回了兩個標籤時，往嚴格的
        方向解讀。
        """
        text = (raw or "").strip().lower()
        if not text:
            return None

        dangerous = [
            SafetyCategory.SELF_HARM,
            SafetyCategory.MEDICAL,
            SafetyCategory.LEGAL,
            SafetyCategory.FINANCIAL,
            SafetyCategory.RELIGIOUS_DOCTRINE,
            SafetyCategory.POLITICAL_STANCE,
            # `other` 放最後：它是最寬鬆的一類，而其他標籤都不含 "other"
            # 子字串，先比它會讓具體分類永遠比不到。
            SafetyCategory.OTHER,
        ]
        for category in dangerous:
            if category in text:
                return category

        if SafetyCategory.SAFE in text:
            return SafetyCategory.SAFE

        return None


class FakeSafetyChecker(SafetyChecker):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同 `FakeGeminiClient`：
    B2、對話端點的測試都會用到，放 tests/ 會變成跨測試檔 import。
    """

    def __init__(self, result: SafetyResult | None = None):
        self.result = result or SafetyResult(is_safe=True)
        self.checked_inputs: list[str] = []

    def check(self, user_input: str) -> SafetyResult:
        self.checked_inputs.append(user_input)
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.checked_inputs)


class SafetyGate:
    """
    把「檢查」與「不安全就不呼叫下游」綁在一起。

    這個類別存在的唯一理由，是讓 B4 的核心保證**可被測試**：

        gate = SafetyGate(checker)
        reply = gate.run(user_input, downstream)   # 不安全時 downstream 不被呼叫

    少了它，「不安全時不呼叫 B2／B1」這條保證只活在呼叫端某個 if 裡，沒有任何
    東西守著它——而那正是這一層存在的意義。只回婉拒但仍然送出生成請求的話，
    成本與風險都沒有省到。

    `downstream` 是一個 callable 而不是具體的 B2 型別：B4 不需要知道下游是什麼，
    也不該因為 #12 的介面調整而跟著改。
    """

    def __init__(self, checker: SafetyChecker):
        self._checker = checker

    def run(self, user_input: str, downstream: Callable[[str], str]) -> str:
        """
        安全則呼叫 `downstream(user_input)` 並回傳其結果；不安全則直接回婉拒文字，
        **`downstream` 一次都不會被呼叫**。
        """
        result = self._checker.check(user_input)

        if not result.is_safe:
            # refusal_text 依 SafetyResult 的約定必定有值；用 or 兜底是為了讓
            # 手工建構的 SafetyResult(is_safe=False) 也不會把 None 送給玩家。
            return result.refusal_text or refusal_for(result.category)

        return downstream(user_input)
