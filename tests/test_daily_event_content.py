"""
Ticket #20．當日情境內容生成（B9）。

驗收標準對照見 GitHub issue #20。純函式測試，不需要資料庫或 GCP 憑證。
"""
from datetime import date

import pytest

from app.modules.brain.daily_event import (
    DailyEventContent,
    generate_daily_event_content,
    taiwan_holiday_name,
)
from app.modules.brain.gemini import FALLBACK_REPLY as GEMINI_FALLBACK_REPLY
from app.modules.brain.gemini import FakeGeminiClient


# ── 介面與型別（issue #20 AC1）───────────────────────────────────────────

def test_returns_daily_event_content_with_narrative_text():
    fake = FakeGeminiClient(response="今天廟埕比較安靜，香客三三兩兩。")

    result = generate_daily_event_content(
        "taipei_longshan", date(2026, 1, 1), fake,
    )

    assert isinstance(result, DailyEventContent)
    assert result.narrative_text == "今天廟埕比較安靜，香客三三兩兩。"


def test_calls_gemini_with_a_prompt_containing_place_and_holiday():
    fake = FakeGeminiClient()
    generate_daily_event_content("taipei_longshan", date(2026, 10, 10), fake)

    assert len(fake.prompts) == 1
    assert "taipei_longshan" in fake.prompts[0]
    assert "國慶日" in fake.prompts[0]


# ── 輸入來源限定（issue #20 AC2）─────────────────────────────────────────

def test_only_reads_holiday_official_event_and_curated_material():
    """
    這條測試本身就是「grep 不到天氣／社群 API」這件事的程式碼證據：
    模組原始碼裡沒有 import 任何 HTTP client 或第三方 API 套件。
    """
    import inspect

    import app.modules.brain.daily_event as module

    source = inspect.getsource(module)
    for forbidden in ("weather", "temperature", "air_quality", "requests.get", "httpx.get"):
        assert forbidden not in source.lower()


# ── 無合格輸入時回退（issue #20 AC3）─────────────────────────────────────

def test_no_qualifying_input_returns_fallback_without_calling_gemini():
    fake = FakeGeminiClient()

    result = generate_daily_event_content("taipei_longshan", date(2026, 3, 15), fake)

    assert result.narrative_text
    assert result.narrative_text != ""
    assert fake.call_count == 0  # 沒有東西可改寫，不該白白呼叫模型


def test_official_event_alone_is_a_qualifying_input():
    fake = FakeGeminiClient(response="今天有特別的公開活動。")

    result = generate_daily_event_content(
        "taipei_longshan", date(2026, 3, 15), fake, official_event="法會",
    )

    assert result.narrative_text == "今天有特別的公開活動。"
    assert fake.call_count == 1


def test_curated_material_alone_is_a_qualifying_input():
    fake = FakeGeminiClient(response="根據史料改寫的一段話。")

    result = generate_daily_event_content(
        "taipei_longshan", date(2026, 3, 15), fake, curated_material="某段史料",
    )

    assert result.narrative_text == "根據史料改寫的一段話。"
    assert fake.call_count == 1


# ── Gemini 失敗時回退（issue #20 AC4）────────────────────────────────────

def test_gemini_failure_falls_back_to_daily_event_specific_text():
    """
    `GeminiClient.generate()` 失敗時回傳的是**對話**語境的回退句
    （`gemini.FALLBACK_REPLY`）；這支模組要換成自己的、適合敘述語境的
    回退文字，不是原樣照搬。
    """
    fake = FakeGeminiClient(response=GEMINI_FALLBACK_REPLY)

    result = generate_daily_event_content(
        "taipei_longshan", date(2026, 1, 1), fake,
    )

    assert result.narrative_text != GEMINI_FALLBACK_REPLY
    assert result.narrative_text  # 非空


# ── 純函式：不含排程／快取／DB 寫入（issue #20 AC5）──────────────────────

def test_function_signature_has_no_db_session_parameter():
    import inspect

    sig = inspect.signature(generate_daily_event_content)
    assert "db" not in sig.parameters
    assert "session" not in sig.parameters


# ── taiwan_holiday_name（輔助純函式）─────────────────────────────────────

@pytest.mark.parametrize(
    "d,expected",
    [
        (date(2026, 1, 1), "元旦"),
        (date(2026, 10, 10), "國慶日"),
        (date(2026, 3, 15), None),
    ],
)
def test_taiwan_holiday_name(d, expected):
    assert taiwan_holiday_name(d) == expected
