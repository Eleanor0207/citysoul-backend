"""
B4．角色安全邊界檢查層（SDD 第9節 / CONTEXT.md）。

在輸入端過濾不適合、危險或偏離主題的內容（如高風險醫療、法律、財務建議、自殘或教義裁決）。
不安全時婉拒並回傳符合人格語氣的台詞，且**不繼續往下走生成**。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging

from app.modules.brain.gemini import GeminiClient

logger = logging.getLogger(__name__)

# 高風險建議與敏感主題關鍵字分類 (Fake/Rule-based 過濾)
HIGH_RISK_MEDICAL_KEYWORDS = ("癌症", "生病", "用藥", "看醫生", "症狀", "診斷", "發燒", "開藥")
HIGH_RISK_LEGAL_KEYWORDS = ("告他", "官司", "提告", "違法", "律師", "提告勝算", "勝訴", "違憲")
HIGH_RISK_FINANCIAL_KEYWORDS = ("股票", "投資", "理財", "發財", "明牌", "幾號會開", "買哪支", "明牌幾號")
SELF_HARM_KEYWORDS = ("結束生命", "自殺", "自殘", "不想活了", "跳樓")
RELIGIOUS_DOGMA_KEYWORDS = ("神明顯靈", "求籤吉凶", "聖籤是否準確", "神明真的存在嗎", "哪個神比較靈驗", "顯靈")

DEFAULT_REFUSAL_REPLY = (
    "（城市靈魂溫和地輕搖頭）這類專業或個人選擇的問題，超出了我的能力範圍呢。"
    "不如我們聊聊眼前這座地標的故事，或是看看附近有什麼景致？"
)


@dataclass
class SafetyResult:
    """安全檢查結果快照。"""

    is_safe: bool
    refusal_reply: str | None = None


class SafetyChecker(ABC):
    """安全邊界檢查的抽象介面。"""

    @abstractmethod
    def check(self, user_input: str) -> SafetyResult:
        """
        過濾輸入內容。

        Returns:
            SafetyResult: is_safe 為 False 時，refusal_reply 包含人格化婉拒台詞。
        """


class FakeSafetyChecker(SafetyChecker):
    """
    測試與離線開發使用的規則型 SafetyChecker。
    無需 GCP 憑證與 LLM 呼叫。
    """

    def __init__(self, default_refusal: str = DEFAULT_REFUSAL_REPLY):
        self.default_refusal = default_refusal

    def check(self, user_input: str) -> SafetyResult:
        text = user_input.strip()

        # 1. 醫療、法律、財務高風險建議
        for kw in (
            HIGH_RISK_MEDICAL_KEYWORDS
            + HIGH_RISK_LEGAL_KEYWORDS
            + HIGH_RISK_FINANCIAL_KEYWORDS
            + SELF_HARM_KEYWORDS
            + RELIGIOUS_DOGMA_KEYWORDS
        ):
            if kw in text:
                return SafetyResult(is_safe=False, refusal_reply=self.default_refusal)

        return SafetyResult(is_safe=True, refusal_reply=None)


class GeminiSafetyChecker(SafetyChecker):
    """
    使用 Gemini 模型進行內容分類的 SafetyChecker 實作。
    """

    def __init__(
        self,
        gemini_client: GeminiClient,
        default_refusal: str = DEFAULT_REFUSAL_REPLY,
    ):
        self.gemini_client = gemini_client
        self.default_refusal = default_refusal

    def check(self, user_input: str) -> SafetyResult:
        # 先以快速關鍵字 rule-based 做第一道防火牆
        fake = FakeSafetyChecker(default_refusal=self.default_refusal)
        rule_res = fake.check(user_input)
        if not rule_res.is_safe:
            return rule_res

        # LLM 分類
        sys_prompt = (
            "你是一個安全分類器。判斷使用者的輸入是否包含以下類型：\n"
            "1. 醫療、法律或財務投資等高風險專業建議請求\n"
            "2. 自殘、暴力或違法宣導\n"
            "3. 宗教教義裁決或吉凶預測\n"
            "如果包含以上任一類型，請僅回答 'UNSAFE'；若安全請僅回答 'SAFE'。"
        )
        try:
            resp = self.gemini_client.generate(sys_prompt, user_input)
            if resp and "UNSAFE" in resp.upper():
                return SafetyResult(is_safe=False, refusal_reply=self.default_refusal)
        except Exception as err:
            logger.warning("GeminiSafetyChecker 呼叫失敗，安全過濾降級為放行: %s", err)

        return SafetyResult(is_safe=True, refusal_reply=None)
