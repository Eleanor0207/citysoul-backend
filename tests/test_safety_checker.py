"""
Ticket #11．角色安全邊界檢查層（B4）。

驗收標準對照見 GitHub issue #11。

## 這裡測什麼、不測什麼

`GeminiSafetyChecker` 的判斷邏輯呼叫 B1（`app.modules.brain.gemini.GeminiClient`）
分類，但「Gemini 對一句話的分類判斷本身準不準」不是這支測試檔案能驗證的事——
那需要真實模型，而 AC 明訂全部測試不需要 GCP 憑證（跟 `tests/test_gemini_client.py`
測 B1 本身的做法一致：測 wrapper 的邏輯，不測模型的判斷力）。

這裡測的是：
1. 純比對／組裝邏輯（`_build_classification_prompt`、`_parse_classification`）
   本身對得上；
2. 給定分類器的判斷結果（用 fake `GeminiClient` 或 fake `SafetyChecker`
   模擬），`GeminiSafetyChecker` 與 `enforce_safety_boundary` 的行為符合
   AC——特別是「不安全時 B2／B1 一次都不被呼叫」這條。
"""
import pytest

from app.modules.brain.gemini import FakeGeminiClient, GeminiClient
from app.modules.brain.safety import (
    GeminiSafetyChecker,
    SafetyResult,
    _build_classification_prompt,
    _parse_classification,
    enforce_safety_boundary,
)


# ── 測試用 fake（issue #11 AC1：測試注入 fake 即可跑，不需 GCP 憑證）──────

class FakeSafetyChecker:
    """固定回傳指定結果的 fake，用來測試「呼叫端」的分支邏輯。"""

    def __init__(self, result: SafetyResult) -> None:
        self._result = result

    def check(self, user_input: str) -> SafetyResult:
        return self._result


class FakeTextGenerator(GeminiClient):
    """
    模擬 B1：依輸入 prompt 回傳預先設好的分類字串。

    繼承真正的 `GeminiClient`（不是自己另外湊一個結構相符的類別）：這樣
    `GeminiSafetyChecker` 的建構參數型別跟測試用的物件是同一份契約，B1 的
    抽象介面之後如果加了新的 abstractmethod，這裡會直接在 import 時炸掉，
    而不是要等到跑 mypy 或接上真實 client 才發現兩邊已經對不上。

    `responses` 用「prompt 裡有沒有出現這個 user_input」比對，而不是要求
    prompt 整段完全相等——這樣測試不用綁死 `_CLASSIFICATION_GUIDANCE` 的
    確切文字，之後調整分類 prompt 的措辭不會連帶弄壞這些測試。
    """

    def __init__(self, responses: dict[str, str], default: str = "SAFE") -> None:
        self._responses = responses
        self._default = default
        self.prompts_seen: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts_seen.append(prompt)
        for user_input, response in self._responses.items():
            if user_input in prompt:
                return response
        return self._default


# ── SafetyResult 的不變量（issue #11 AC1）─────────────────────────────

def test_safe_result_has_no_refusal_text():
    result = SafetyResult(is_safe=True)
    assert result.is_safe is True
    assert result.refusal_text is None


def test_safe_result_cannot_carry_refusal_text():
    with pytest.raises(ValueError):
        SafetyResult(is_safe=True, refusal_text="不應該出現")


def test_unsafe_result_must_carry_refusal_text():
    with pytest.raises(ValueError):
        SafetyResult(is_safe=False)


@pytest.mark.parametrize("blank", ["", None])
def test_unsafe_result_rejects_blank_refusal_text(blank):
    with pytest.raises(ValueError):
        SafetyResult(is_safe=False, refusal_text=blank)


# ── enforce_safety_boundary：不安全時不呼叫下游（issue #11 AC2）──────────

def test_unsafe_input_returns_refusal_and_never_calls_downstream():
    """
    AC2：判定為不安全時回婉拒結果，不繼續往下走生成——B2／B1 一次都不被呼叫。

    這是整支模組最重要的一條：只回婉拒但仍呼叫下游生成的話，成本與風險都
    沒省到。用呼叫次數計數器（等同 spy）驗證下游真的沒被呼叫。
    """
    checker = FakeSafetyChecker(
        SafetyResult(is_safe=False, refusal_text="我想結束自己的生命——這句不安全，婉拒。")
    )
    downstream_calls = []

    def generate_reply() -> str:
        downstream_calls.append(1)
        return "不應該走到這裡"

    reply = enforce_safety_boundary(checker, "我想結束自己的生命", generate_reply)

    assert reply == "我想結束自己的生命——這句不安全，婉拒。"
    assert downstream_calls == []  # B2／B1 一次都沒被呼叫


def test_safe_input_calls_downstream_and_returns_its_result():
    """AC3：判定為安全的輸入正常放行，呼叫端可繼續走 B2→B1→B10。"""
    checker = FakeSafetyChecker(SafetyResult(is_safe=True))

    def generate_reply() -> str:
        return "這座廟最早建於1738年。"

    reply = enforce_safety_boundary(checker, "這座廟最早是什麼時候蓋的？", generate_reply)

    assert reply == "這座廟最早建於1738年。"


# ── 婉拒文案帶回地標／任務／城市故事語境（issue #11 AC4）────────────────

_REFUSAL_TEXTS_BY_SCENARIO = {
    "high_risk_advice": "我這個症狀是不是癌症",
    "religious_boundary": "求這支籤是吉是凶",
    "unsafe_or_off_topic": "隨便一個不適合的話題",
}


@pytest.mark.parametrize("category_hint", list(_REFUSAL_TEXTS_BY_SCENARIO))
def test_refusal_text_is_not_a_system_message(category_hint):
    """
    婉拒文字不該是「您的輸入違反使用規範」這種系統訊息口吻，且不能是空字串。
    三種分類模板都要滿足這個性質。
    """
    generator = FakeTextGenerator({"x": f"UNSAFE:{category_hint}"}, default=f"UNSAFE:{category_hint}")
    result = GeminiSafetyChecker(generator).check("任意輸入")

    assert result.is_safe is False
    assert result.refusal_text
    assert "違反使用規範" not in result.refusal_text
    assert "系統" not in result.refusal_text


# ── 高風險專業建議觸發婉拒（issue #11 AC5）──────────────────────────────

@pytest.mark.parametrize(
    "user_input",
    [
        "我這個症狀是不是癌症",  # 醫療
        "我可以告他嗎",  # 法律
        "我該買哪支股票",  # 財務
    ],
)
def test_high_risk_professional_advice_is_refused(user_input):
    """
    CONTEXT.md「角色安全邊界」明訂不提供高風險專業建議。用 fake B1 模擬
    「分類器判定為 high_risk_advice」，驗證這支模組把它正確轉成婉拒、且
    不含任何實質建議內容（婉拒模板本身不提供建議，見 `_REFUSAL_TEMPLATES`）。
    """
    generator = FakeTextGenerator({user_input: "UNSAFE:high_risk_advice"})
    result = GeminiSafetyChecker(generator).check(user_input)

    assert result.is_safe is False
    assert result.refusal_text
    # 婉拒模板本身沒有醫療／法律／財務術語，這裡順手確認不是複製輸入回去。
    assert user_input not in result.refusal_text


# ── 宗教教義性提問依規範婉拒（issue #11 AC6）────────────────────────────
#
# #41（龍山寺內容治理）尚未產出正式的敏感情境清單，這裡先用 CONTEXT.md
# 「宗教場域邊界」已審核的安全下限，取的三則情境跟 issue #11／#41 body 裡
# 點名的例子一致（求籤吉凶、神明是否顯靈、不同信仰比較）。#41 正式產出後，
# 這組情境要換成它的清單，機制（_parse_classification／模板）不需要跟著變。

@pytest.mark.parametrize(
    "user_input",
    [
        "我求的這支籤是吉是凶？",
        "這尊神明真的有顯靈過嗎？",
        "佛教跟道教比起來哪個比較靈？",
    ],
)
def test_religious_teaching_questions_are_refused_without_doctrinal_claims(user_input):
    generator = FakeTextGenerator({user_input: "UNSAFE:religious_boundary"})
    result = GeminiSafetyChecker(generator).check(user_input)

    assert result.is_safe is False
    assert result.refusal_text
    # 不做教義性陳述或裁決：婉拒文字不能對「靈不靈驗」本身表態。
    for doctrinal_word in ("很靈驗", "不靈驗", "會實現", "不會實現", "比較正統"):
        assert doctrinal_word not in result.refusal_text


# ── 安全的輸入正常放行（issue #11 AC3，走 GeminiSafetyChecker 本體）──────

def test_safe_question_passes_through_gemini_safety_checker():
    generator = FakeTextGenerator({"這座廟最早是什麼時候蓋的？": "SAFE"})
    result = GeminiSafetyChecker(generator).check("這座廟最早是什麼時候蓋的？")

    assert result.is_safe is True
    assert result.refusal_text is None


def test_accepts_b1s_own_fake_gemini_client():
    """
    介面互通性檢查：`GeminiSafetyChecker` 吃的是 B1 真正的 `GeminiClient`，
    B1 自己的測試 fake（`FakeGeminiClient`）不用改造就能直接注入——這才是
    「呼叫端只依賴抽象介面」真正被驗證到，而不是只驗證了我方另外寫的
    `FakeTextGenerator` 湊巧符合預期。
    """
    generator = FakeGeminiClient(response="SAFE")
    result = GeminiSafetyChecker(generator).check("這座廟最早是什麼時候蓋的？")

    assert result.is_safe is True
    assert generator.call_count == 1


# ── 分類 prompt 組裝與回應解析（純函式，內部邏輯）────────────────────────

def test_classification_prompt_includes_user_input():
    prompt = _build_classification_prompt("這座廟幾點開門？")
    assert "這座廟幾點開門？" in prompt


def test_classification_prompt_does_not_leak_across_calls():
    """確保 prompt 是每次重新組裝，不是共用同一個可變模板被意外改到。"""
    first = _build_classification_prompt("第一句")
    second = _build_classification_prompt("第二句")
    assert "第一句" not in second
    assert "第二句" not in first


@pytest.mark.parametrize(
    "raw,expected_is_safe",
    [
        ("SAFE", True),
        ("UNSAFE:high_risk_advice", False),
        ("UNSAFE:religious_boundary", False),
        ("UNSAFE:unsafe_or_off_topic", False),
    ],
)
def test_parse_classification_recognized_responses(raw, expected_is_safe):
    assert _parse_classification(raw).is_safe is expected_is_safe


@pytest.mark.parametrize("raw", [" SAFE\n", "\tSAFE  "])
def test_parse_classification_trims_whitespace(raw):
    """B1 的原始輸出可能帶多餘的空白或換行，不該因此被誤判為不安全。"""
    assert _parse_classification(raw).is_safe is True


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "safe",  # 大小寫不符，刻意不做寬鬆比對——見模組說明的保守原則
        "UNSAFE",
        "UNSAFE:not_a_real_category",
        "這句話是安全的",
        "SAFE and also UNSAFE:high_risk_advice",
    ],
)
def test_unrecognized_classification_response_defaults_to_unsafe(raw):
    """
    mutation 驗證的另一半：分類器回應看不懂時，預設值必須是不安全，不能是
    安全——「錯放行」的代價遠高於「錯擋下」。
    """
    result = _parse_classification(raw)
    assert result.is_safe is False
    assert result.refusal_text
