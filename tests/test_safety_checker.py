"""
B4．角色安全邊界檢查層（issue #11）。

純單元測試——用 fake 驗證兩條路徑，**不需要 GCP 憑證、不需要資料庫**。

⚠️ 婉拒文案是待審核草稿（見 `REFUSAL_REVIEW_STATUS`）。這裡守的是**語氣方向
與結構**，不逐字比對文案——同 B5 的處理，逐字比對會讓每次潤稿變成破壞性變更。
"""
import pytest

from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient
from app.modules.brain.safety import (
    REFUSAL_REVIEW_STATUS,
    FakeSafetyChecker,
    GeminiSafetyChecker,
    SafetyCategory,
    SafetyGate,
    SafetyResult,
    refusal_for,
)


def _checker_returning(label: str) -> GeminiSafetyChecker:
    """建一個真實的 checker，但底下的模型是回傳固定標籤的 fake。"""
    return GeminiSafetyChecker(FakeGeminiClient(response=label))


# ── 放行路徑 ───────────────────────────────────────────────────────────

def test_safe_input_passes_through():
    """AC：判定為安全的輸入正常放行，無婉拒文字。"""
    result = _checker_returning("safe").check("這座廟最早是什麼時候蓋的？")

    assert result.is_safe is True
    assert result.refusal_text is None


def test_blank_input_does_not_call_the_model():
    """
    空白輸入不值得花一次模型呼叫。

    它也不危險——`DialogueRequest` 已經擋掉空字串了，這裡只是不要為了一個
    必然被上游擋下的輸入去付一次 API 成本。
    """
    client = FakeGeminiClient(response="safe")
    checker = GeminiSafetyChecker(client)

    assert checker.check("   ").is_safe is True
    assert client.call_count == 0


# ── 攔截路徑 ───────────────────────────────────────────────────────────

def test_self_harm_input_is_refused():
    """AC 明列的輸入。"""
    result = _checker_returning("self_harm").check("我想結束自己的生命")

    assert result.is_safe is False
    assert result.category == SafetyCategory.SELF_HARM
    assert result.refusal_text


# AC：高風險專業建議三類各一則。CONTEXT.md 明訂「不提供高風險專業建議」，
# 這組把它變成可驗證的事實。
_HIGH_RISK = [
    (SafetyCategory.MEDICAL, "我這個症狀是不是癌症"),
    (SafetyCategory.LEGAL, "我可以告他嗎"),
    (SafetyCategory.FINANCIAL, "我該買哪支股票"),
]


@pytest.mark.parametrize("label,user_input", _HIGH_RISK)
def test_high_risk_professional_advice_is_refused(label, user_input):
    result = _checker_returning(label).check(user_input)

    assert result.is_safe is False
    assert result.category == label
    assert result.refusal_text


# AC：依 #41 的敏感情境清單至少取 3 則。
# 對應 docs/content-governance/longshan-temple.md §5 的情境 1、2、5。
_RELIGIOUS = [
    "我抽到下下籤，是不是很慘？",
    "這裡求姻緣真的很靈吧？",
    "佛教跟道教哪個比較好？",
]


@pytest.mark.parametrize("user_input", _RELIGIOUS)
def test_religious_doctrine_questions_are_refused(user_input):
    result = _checker_returning("religious_doctrine").check(user_input)

    assert result.is_safe is False
    assert result.category == SafetyCategory.RELIGIOUS_DOCTRINE
    assert result.refusal_text


def test_religious_refusal_does_not_adjudicate_doctrine():
    """
    AC：婉拒文字本身不得作教義性陳述或裁決（v2.1 §3 新增的 Avoid 條目）。

    這條擋的是一種很自然的寫法——「求籤只是心理安慰」聽起來像在保持中立，
    但它其實是對信仰內容下了判斷，跟「求籤很靈」是同一種錯誤的兩面。
    """
    text = refusal_for(SafetyCategory.RELIGIOUS_DOCTRINE)

    for adjudication in ["只是", "迷信", "不存在", "本來就沒有", "科學"]:
        assert adjudication not in text, f"婉拒文案裡出現了裁決語氣：「{adjudication}」"


# ── 婉拒文案的語氣 ─────────────────────────────────────────────────────

def test_refusals_are_in_character_not_system_messages():
    """
    AC：不是「您的輸入違反使用規範」這種系統訊息口吻。

    玩家站在廟埕前，被一個系統錯誤訊息打斷，比沒有回應更破壞情境。
    """
    system_message_tells = ["違反", "使用規範", "系統", "錯誤", "無法處理", "請重新輸入"]

    for category, text in [(c, refusal_for(c)) for c in _ALL_REFUSED_CATEGORIES]:
        for tell in system_message_tells:
            assert tell not in text, f"{category} 的婉拒文案有系統訊息口吻：「{tell}」"


_ALL_REFUSED_CATEGORIES = [
    SafetyCategory.SELF_HARM,
    SafetyCategory.MEDICAL,
    SafetyCategory.LEGAL,
    SafetyCategory.FINANCIAL,
    SafetyCategory.RELIGIOUS_DOCTRINE,
    SafetyCategory.OTHER,
]


@pytest.mark.parametrize(
    "category",
    [c for c in _ALL_REFUSED_CATEGORIES if c != SafetyCategory.SELF_HARM],
)
def test_refusals_redirect_to_the_landmark(category):
    """
    AC：婉拒文案帶回地標／任務／城市故事語境。

    用「有沒有邀請對方繼續說」當作可辨識的訊號——問句是最穩定的結構特徵，
    潤稿改字不會讓它消失，但改成冷冰冰的拒絕就會。

    ⚠️ 自傷類**刻意排除在外**，理由見下一條測試。
    """
    text = refusal_for(category)

    assert "？" in text or "?" in text, "婉拒沒有把話題接回去，只是拒絕"


def test_self_harm_refusal_does_not_deflect_to_small_talk():
    """
    🔴 自傷類不套用「帶回地標」的模板。

    其他類別把話題轉回城市故事是恰當的；對一個可能正在求助的人這樣做，等於
    忽略他真正說的話。這條測試存在的意義是**防止有人為了讓上面那條測試整齊
    通過，順手把自傷也套進同一個模板**。

    文案要做的是表達在意、指向真實的人，地標語境退到最後。
    """
    text = refusal_for(SafetyCategory.SELF_HARM)

    # 不把話題轉開去問地標。
    assert "想聽嗎" not in text
    assert "這座廟" not in text
    # 指向真實的人，而不是自己接手。
    assert "專業" in text or "朋友" in text or "家人" in text


# ── fail-closed ────────────────────────────────────────────────────────

def test_unparseable_classification_falls_closed():
    """
    分類失敗時判定為**不安全**，不是放行。

    代價不對稱：誤擋一句「這座廟什麼時候蓋的」，玩家看到一句溫和的轉向；
    誤放一句自傷相關的提問，後果不在同一個量級。
    """
    checker = _checker_returning("我覺得這個問題很有意思呢")
    result = checker.check("我這個症狀是不是癌症")

    assert result.is_safe is False
    assert checker.last_failure_reason


def test_b1_fallback_reply_is_treated_as_a_failed_classification():
    """
    B1 失敗時會吐 `FALLBACK_REPLY`，那串文字裡沒有任何標籤。

    這條確認它自然落進 fail-closed，不需要在 B4 裡另外偵測「這是不是 fallback
    文字」——那種比對會在 B1 改文案的那天靜默失效。
    """
    checker = GeminiSafetyChecker(FakeGeminiClient(response=FALLBACK_REPLY))

    assert checker.check("我可以告他嗎").is_safe is False


def test_ambiguous_response_resolves_to_the_stricter_label():
    """模型同時回了兩個標籤時，往嚴格的方向解讀。"""
    result = _checker_returning("safe self_harm").check("...")

    assert result.is_safe is False
    assert result.category == SafetyCategory.SELF_HARM


@pytest.mark.parametrize("raw", ["SAFE", " safe ", "標籤：safe", "safe。"])
def test_label_parsing_is_lenient_about_formatting(raw):
    """
    格式寬鬆比對。嚴格比對只會讓一個無害的格式差異變成一次 fail-closed 誤擋，
    而誤擋是有代價的——玩家問了正常問題卻被轉開話題。
    """
    assert _checker_returning(raw).check("這座廟什麼時候蓋的？").is_safe is True


# ── SafetyGate：不安全時下游一次都不被呼叫 ────────────────────────────

def test_gate_calls_downstream_when_safe():
    downstream = FakeGeminiClient(response="（B2→B1 的回應）")
    gate = SafetyGate(FakeSafetyChecker(SafetyResult(is_safe=True)))

    reply = gate.run("這座廟最早是什麼時候蓋的？", downstream.generate)

    assert reply == "（B2→B1 的回應）"
    assert downstream.call_count == 1


def test_gate_does_not_call_downstream_when_unsafe():
    """
    🔒 **本層存在的意義。**

    只回婉拒但仍然送出生成請求的話，成本與風險都沒有省到。這條是 AC 明列要做
    mutation 驗證的那一條。
    """
    downstream = FakeGeminiClient()
    refusal = refusal_for(SafetyCategory.SELF_HARM)
    gate = SafetyGate(
        FakeSafetyChecker(
            SafetyResult(
                is_safe=False,
                category=SafetyCategory.SELF_HARM,
                refusal_text=refusal,
            )
        )
    )

    reply = gate.run("我想結束自己的生命", downstream.generate)

    assert reply == refusal
    assert downstream.call_count == 0, "不安全的輸入仍然呼叫了下游——這一層等於沒有作用"


def test_gate_still_returns_text_when_refusal_is_missing():
    """
    手工建構的 `SafetyResult(is_safe=False)` 沒有 refusal_text 時，仍然要回一段
    文字給玩家，不能把 None 送出去。

    這種 result 不會由 checker 產生，但會由呼叫端手工建構——防禦的是那條路徑。
    """
    downstream = FakeGeminiClient()
    gate = SafetyGate(FakeSafetyChecker(SafetyResult(is_safe=False)))

    reply = gate.run("...", downstream.generate)

    assert reply
    assert downstream.call_count == 0


# ── 文案審核狀態 ───────────────────────────────────────────────────────

def test_refusals_are_marked_as_pending_review():
    """
    文案來源是 #41 交付物 C，而那份文件本身也還沒過審。

    這條會在有人把標記改成審核者與日期時變紅——那時候紅是對的。
    """
    assert REFUSAL_REVIEW_STATUS == "PENDING_NARRATIVE_REVIEW"


def test_review_marker_never_reaches_the_player():
    for category in _ALL_REFUSED_CATEGORIES:
        assert REFUSAL_REVIEW_STATUS not in refusal_for(category)


def test_unknown_category_still_returns_a_refusal():
    """未知分類回傳通用那則，不拋 KeyError。"""
    assert refusal_for("something_we_have_never_seen")
