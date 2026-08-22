"""
Ticket #8．B1 Vertex AI Gemini 串接與失敗回退。

驗收標準對照見 GitHub issue #8。

這個檔案**完全不需要 GCP 憑證**：真實 SDK 從來沒被載入，模型物件由
`client_factory` 注入。唯一會碰到真實服務的是最後那個測試，沒有憑證時自動 skip。
"""
import os
import threading
import time

import pytest

from app.core.config import settings
from app.modules.brain.gemini import (
    FALLBACK_REPLY,
    FakeGeminiClient,
    GeminiClient,
    VertexAIGeminiClient,
)


class _StubCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _StubResponse:
    def __init__(self, text, finish_reason="FinishReason.STOP"):
        self.text = text
        self.candidates = [_StubCandidate(finish_reason)]


class _StubModels:
    """對應 google-genai 的 `client.models`。記下呼叫參數，或依設定拋例外。"""

    def __init__(self, *, text, finish_reason, raises):
        self._text = text
        self._finish_reason = finish_reason
        self._raises = raises
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})

        if self._raises is not None:
            raise self._raises

        return _StubResponse(self._text, self._finish_reason)


class _StubClient:
    def __init__(
        self,
        *,
        text="今夜的香火比平常更盛一些。",
        finish_reason="FinishReason.STOP",
        raises=None,
    ):
        self.models = _StubModels(text=text, finish_reason=finish_reason, raises=raises)

    @property
    def calls(self):
        return self.models.calls


class _HangingModels:
    def __init__(self, released):
        self._released = released
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        self._released.wait(timeout=10)
        return _StubResponse("太遲了")


class _HangingClient:
    """永遠不回應的 client，用來驗證逾時。10 秒後自行放行，不留卡死的執行緒。"""

    def __init__(self):
        self.released = threading.Event()
        self.models = _HangingModels(self.released)


def _client(stub, **kwargs):
    return VertexAIGeminiClient(client_factory=lambda: stub, **kwargs)


# ── 介面 ──────────────────────────────────────────────────────────────

def test_both_implementations_satisfy_the_interface():
    """
    呼叫端只依賴抽象介面，所以測試才能注入 fake 而不需要 GCP 憑證。
    """
    assert issubclass(VertexAIGeminiClient, GeminiClient)
    assert issubclass(FakeGeminiClient, GeminiClient)


def test_abstract_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        GeminiClient()


# ── 成功路徑 ──────────────────────────────────────────────────────────

def test_successful_call_returns_the_model_text_unchanged():
    model = _StubClient(text="今夜的香火比平常更盛一些。")

    result = _client(model).generate("你是誰？")

    assert result == "今夜的香火比平常更盛一些。"


def test_surrounding_whitespace_is_trimmed():
    """模型常在前後帶換行。那不是內容，直接顯示會讓對話框莫名多出空行。"""
    result = _client(_StubClient(text="\n  你來了。  \n")).generate("你好")

    assert result == "你來了。"


def test_the_prompt_reaches_the_model_unmodified():
    model = _StubClient()

    _client(model).generate("這座廟最早是什麼時候蓋的？")

    assert model.calls[0]["contents"] == "這座廟最早是什麼時候蓋的？"


# ── 失敗回退：本模組最重要的性質 ──────────────────────────────────────

@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("connection reset by peer"),
        TimeoutError("deadline exceeded"),
        RuntimeError("503 Service Unavailable"),
    ],
    ids=["連線錯誤", "逾時", "5xx"],
)
def test_failures_fall_back_without_raising(failure):
    """
    三種失敗都回傳 fallback 台詞，且**不拋例外**。

    這是整個模組最重要的性質：模型暫時不可用不得中斷召喚流程。玩家已經走到
    廟埕了，他不該因為我們的雲端服務打嗝而看到錯誤畫面。
    """
    result = _client(_StubClient(raises=failure)).generate("你好")

    assert result == FALLBACK_REPLY


def test_an_unexpected_exception_type_also_falls_back():
    """
    這裡刻意不維護「哪些例外算預期」的清單——那種清單一定會漏，而漏掉的那個
    會在最糟的時機（正式環境、玩家在現場）變成 500。
    """
    class SomethingNobodyAnticipated(BaseException):
        pass

    # BaseException 不被 `except Exception` 攔截，所以用它的子類別驗證會**穿透**
    # ——這條記錄的是真實邊界，不是宣稱「什麼都攔得住」。
    with pytest.raises(SomethingNobodyAnticipated):
        _client(_StubClient(raises=SomethingNobodyAnticipated())).generate("你好")


def test_model_construction_failure_also_falls_back():
    """
    連模型都建不起來（憑證錯誤、專案不存在）也要回退。這條路徑跟「呼叫失敗」
    不同——它發生在 generate 的第一行之前。
    """
    def explode():
        raise RuntimeError("could not find default credentials")

    client = VertexAIGeminiClient(client_factory=explode)

    assert client.generate("你好") == FALLBACK_REPLY


def test_empty_response_is_treated_as_a_failure():
    """
    空字串在型別上是「成功」，但對玩家而言跟失敗沒有差別——對話框會是空的。
    常見原因是被安全過濾器擋下，那正是該回退的情況。
    """
    assert _client(_StubClient(text="")).generate("你好") == FALLBACK_REPLY
    assert _client(_StubClient(text="   \n ")).generate("你好") == FALLBACK_REPLY


def test_fallback_is_never_empty():
    """
    呼叫端會直接把回傳值塞進對話框。回傳空字串的話玩家看到的是「靈魂沉默了」
    ——那是一個看起來像功能、實際上是錯誤的畫面。
    """
    assert FALLBACK_REPLY
    assert FALLBACK_REPLY.strip()


def test_failure_reason_is_recorded_for_observability():
    """
    「模型失敗」與「模型回了這句話」在回傳值上無法區分，那是刻意的取捨。
    但要能觀察成功率，所以原因留在這裡而不是只丟進 log。
    """
    client = _client(_StubClient(raises=TimeoutError("deadline exceeded")))
    client.generate("你好")

    assert client.last_failure_reason is not None
    assert "TimeoutError" in client.last_failure_reason


def test_failure_reason_is_cleared_on_a_later_success():
    """殘留的失敗原因會讓觀測數據長期偏高，比沒有數據更糟。"""
    model = _StubClient(raises=TimeoutError("x"))
    client = _client(model)
    client.generate("你好")

    model.models._raises = None
    client.generate("你好")

    assert client.last_failure_reason is None


# ── 成本與延遲的上限 ──────────────────────────────────────────────────

def test_output_length_is_capped_at_the_call_parameter_level():
    """
    長度上限必須在呼叫參數，不是靠 prompt 請模型「簡短回答」——後者沒有保證，
    而這個值直接決定單次呼叫的成本上限（🔴 高風險「AI 對話成本與延遲」）。
    """
    model = _StubClient()

    _client(model, max_output_tokens=128).generate("你好")

    assert model.calls[0]["config"].max_output_tokens == 128


def test_output_cap_defaults_to_the_setting():
    model = _StubClient()

    _client(model).generate("你好")

    assert model.calls[0]["config"].max_output_tokens == settings.gemini_max_output_tokens


def test_thinking_level_follows_the_setting():
    """
    `thinking_level` 只有 gemini-3.x 系列接受；2.5 系列傳了會直接回
    `400 INVALID_ARGUMENT`。所以這個值必須跟 `gemini_model` 綁在一起改，
    2026-08-22 兩者一起換到 3.5-flash-lite ＋ LOW。
    """
    model = _StubClient()

    _client(model).generate("你好")

    sent = model.calls[0]["config"].thinking_config
    if settings.gemini_thinking_level is None:
        assert sent is None
    else:
        assert sent.thinking_level == settings.gemini_thinking_level


def test_thinking_config_is_omitted_when_not_configured():
    model = _StubClient()

    _client(model, thinking_level=None).generate("你好")

    # thinking_level=None 明確傳入時要覆寫設定值——2.5 系列非這樣不可。
    assert model.calls[0]["config"].thinking_config is None


def test_thinking_config_is_sent_when_configured():
    model = _StubClient()

    _client(model, thinking_level="LOW").generate("你好")

    assert model.calls[0]["config"].thinking_config.thinking_level == "LOW"


def test_truncated_response_is_flagged_but_still_returned():
    """
    實測 gemini-3.5-flash 在 512 tokens 下回「那是在清乾隆三年（西元1738年），
    先民們懷抱」——非空、看起來成功，但斷在句子中間（thinking 先吃掉了約 500）。

    半句話通常還是比通用回退台詞有用，所以照樣回傳；但要留下觀測旗標，因為這個
    訊號的正確處置是調高預算或換模型，不是在程式裡加修剪 heuristic 把它藏起來。
    """
    model = _StubClient(
        text="那是在清乾隆三年（西元1738年），先民們懷抱",
        finish_reason="FinishReason.MAX_TOKENS",
    )
    client = _client(model)

    result = client.generate("你好")

    assert result == "那是在清乾隆三年（西元1738年），先民們懷抱"
    assert client.last_truncated is True


def test_untruncated_response_is_not_flagged():
    client = _client(_StubClient())
    client.generate("你好")

    assert client.last_truncated is False


def test_a_hanging_call_falls_back_instead_of_blocking_forever():
    """
    沒有逾時上限的話，一次卡住的呼叫會讓玩家站在廟埕前無限期等待。

    這條驗的是**行為**，不是「有沒有把 timeout 參數傳下去」。前一版就是那樣寫的，
    結果 stub 照單全收、測試全綠，直到真實呼叫才發現 Vertex AI SDK 的
    `generate_content()` 根本沒有 timeout 參數。
    """
    started = time.monotonic()

    result = _client(_HangingClient(), timeout_seconds=0.3).generate("你好")

    assert result == FALLBACK_REPLY
    assert time.monotonic() - started < 2.0, "應該在逾時後就放棄，不是等模型回來"


def test_timeout_reason_is_recorded():
    client = _client(_HangingClient(), timeout_seconds=0.3)
    client.generate("你好")

    assert "秒未回應" in client.last_failure_reason


def test_a_model_raising_TimeoutError_is_not_reported_as_our_own_timeout():
    """
    Python 3.11 起 `concurrent.futures.TimeoutError` **就是**內建的 `TimeoutError`。

    如果用 `future.result(timeout=...)` 加 `except TimeoutError`，模型自己拋的
    TimeoutError 會被當成「我們主動放棄」。回退行為一樣，但 last_failure_reason
    會說謊——而那個欄位存在的唯一理由就是觀測。
    """
    client = _client(_StubClient(raises=TimeoutError("deadline exceeded")))
    client.generate("你好")

    assert "TimeoutError" in client.last_failure_reason
    assert "秒未回應" not in client.last_failure_reason


# ── fake ─────────────────────────────────────────────────────────────

def test_fake_returns_the_configured_response_and_records_prompts():
    fake = FakeGeminiClient(response="（測試）")

    assert fake.generate("第一句") == "（測試）"
    assert fake.generate("第二句") == "（測試）"

    assert fake.call_count == 2
    assert fake.prompts == ["第一句", "第二句"]


# ── 真實呼叫：沒有憑證時自動 skip ─────────────────────────────────────

def _has_adc() -> bool:
    """
    ADC 是否可用（ADR-0003）。這裡不讀任何金鑰檔——沒有金鑰檔可讀。
    """
    try:
        import google.auth

        google.auth.default()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    os.getenv("CITYSOUL_RUN_LIVE_GEMINI") != "1",
    reason="真實呼叫要花錢也要網路。設 CITYSOUL_RUN_LIVE_GEMINI=1 才跑。",
)
def test_live_vertex_ai_call():
    """
    手動執行的真實驗證。

        CITYSOUL_RUN_LIVE_GEMINI=1 uv run python -m pytest tests/test_gemini_client.py -k live

    ⚠️ 失敗時先確認是不是環境問題再看程式碼：
    - `could not find default credentials` → 跑 `gcloud auth application-default login`
    - 憑證驗證錯誤 → 本機 Avast 的 HTTPS 掃描攔截 TLS（見 issue #22）
    - `model not found` → `settings.gemini_model` 的字串沒對照過真正可用的模型清單
    """
    if not _has_adc():
        pytest.skip("找不到 ADC，請先跑 gcloud auth application-default login")

    result = VertexAIGeminiClient().generate("用一句話說明你是誰。")

    assert result
    assert result != FALLBACK_REPLY, (
        "拿到 fallback 代表真實呼叫失敗了。"
        "檢查 last_failure_reason 找出原因，不要當成測試通過。"
    )


# ── 英文漏出（2026-08-22）────────────────────────────────────────────
#
# prompt 已經明說「整段回話裡不出現任何英文字母」，但那是請求不是保證。線上
# 實測兩種漏法：夾一個詞（「stories 說也說不完」），以及整段翻成英文。


class _SequenceModels:
    """每次呼叫依序回下一段文字，用完之後固定回最後一段。"""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        return _StubResponse(self._texts[index])


class _SequenceClient:
    def __init__(self, texts):
        self.models = _SequenceModels(texts)

    @property
    def calls(self):
        return self.models.calls


def test_a_leaked_english_word_is_regenerated_once():
    model = _SequenceClient(["這地方啊，stories 說也說不完。", "這地方啊，故事說也說不完。"])

    result = _client(model).generate("跟我說說這裡", expect_chinese=True)

    assert result == "這地方啊，故事說也說不完。"
    assert len(model.calls) == 2, "第一次漏出要重生，第二次乾淨就用它。"


def test_two_leaks_in_a_row_fall_back_instead_of_a_third_try():
    # 再試下去是拿玩家的等待時間與帳單去賭一個機率性的結果。
    model = _SequenceClient(["those pillars, still smelling of wood.", "Now they have been touched."])

    client = _client(model)
    result = client.generate("跟我說說這裡", expect_chinese=True)

    assert result == FALLBACK_REPLY
    assert len(model.calls) == 2
    assert client.last_latin_leak, "回退原因要留得下來，否則漏出率量不出來。"


def test_a_clean_reply_is_not_regenerated():
    model = _SequenceClient(["這地方啊，故事說也說不完。"])

    result = _client(model).generate("跟我說說這裡", expect_chinese=True)

    assert result == "這地方啊，故事說也說不完。"
    assert len(model.calls) == 1


def test_landmark_names_in_latin_are_not_treated_as_a_leak():
    # MOCA、IMAX 是地標本身的名字，不是模型漏出來的英文。
    model = _SequenceClient(["MOCA 就在那棟紅磚樓裡。"])

    result = _client(model).generate("那是什麼", expect_chinese=True)

    assert result == "MOCA 就在那棟紅磚樓裡。"
    assert len(model.calls) == 1


def test_classifier_style_calls_are_not_checked_at_all():
    """B4 分類器回 `safe`、導引提問回 JSON——那些是正確輸出，不是漏出。

    2026-08-22 之前這個檢查對每一次呼叫都跑，正式環境的 log 因此被 `['safe']`
    洗掉，真正的玩家可見漏出被埋在裡面。
    """
    model = _SequenceClient(["safe"])

    result = _client(model).generate("分類這句話")

    assert result == "safe"
    assert len(model.calls) == 1, "沒開 expect_chinese 就不該重生。"
