"""
Ticket #20．當日情境內容生成（B9）。

驗收標準對照見 GitHub issue #20。跟其他 B-系列模組同一種風格：真實 SDK
從來不載入，測試注入 `FakeGeminiClient`，完全不需要 GCP 憑證。
"""
import ast
from datetime import date
from pathlib import Path

from app.modules.brain.daily_event_content import (
    DailyEventContent,
    generate_daily_event_content,
)
from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient

_MODULE_PATH = Path(
    __import__(
        "app.modules.brain.daily_event_content", fromlist=["x"]
    ).__file__
)


# ── 介面（AC1） ──────────────────────────────────────────────────────────


def test_returns_daily_event_content_with_narrative_text():
    fake = FakeGeminiClient(response="今天廟埕特別熱鬧，有陣頭表演。")

    result = generate_daily_event_content(
        "taipei_longshan",
        date(2026, 8, 6),
        official_events=["陣頭表演"],
        gemini_client=fake,
    )

    assert isinstance(result, DailyEventContent)
    assert result.place_id == "taipei_longshan"
    assert result.event_date == date(2026, 8, 6)
    assert result.narrative_text == "今天廟埕特別熱鬧，有陣頭表演。"
    assert result.source == "generated"


# ── 無合格輸入時回退，不呼叫 Gemini（AC3） ────────────────────────────────


def test_no_qualifying_input_returns_fallback_without_calling_gemini():
    fake = FakeGeminiClient()

    result = generate_daily_event_content("taipei_longshan", date(2026, 8, 6), gemini_client=fake)

    assert result.source == "fallback"
    assert result.narrative_text  # 非空、非 None
    assert fake.call_count == 0  # 沒有合格輸入時，一次都不呼叫 Gemini


def test_fallback_text_is_never_empty_or_blank():
    fake = FakeGeminiClient()

    result = generate_daily_event_content("taipei_longshan", date(2026, 8, 6), gemini_client=fake)

    assert result.narrative_text.strip() != ""


def test_festival_alone_counts_as_qualifying_input():
    fake = FakeGeminiClient(response="今天是中元節，廟裡格外莊嚴。")

    result = generate_daily_event_content(
        "taipei_longshan", date(2026, 8, 6), festival_name="中元節", gemini_client=fake
    )

    assert result.source == "generated"
    assert fake.call_count == 1


def test_reviewed_material_alone_counts_as_qualifying_input():
    fake = FakeGeminiClient(response="這裡最近修復了一面古老的石碑。")

    result = generate_daily_event_content(
        "taipei_longshan",
        date(2026, 8, 6),
        reviewed_material=["石碑修復紀事"],
        gemini_client=fake,
    )

    assert result.source == "generated"
    assert fake.call_count == 1


# ── Gemini 呼叫失敗／逾時同樣回退（AC4） ──────────────────────────────────


def test_gemini_failure_still_produces_non_empty_content_without_raising():
    """
    `GeminiClient.generate()` 自己保證永遠不拋例外、永遠回傳非空字串（見
    `gemini.py`）——「模型失敗」與「模型回了這句話」在型別上無法區分，
    所以這裡驗證的是「B1 的回退字串會原樣流過來」，不是 B9 自己另外接了
    一層 try/except（它沒有，也不需要）。
    """
    fake = FakeGeminiClient(response=FALLBACK_REPLY)

    result = generate_daily_event_content(
        "taipei_longshan",
        date(2026, 8, 6),
        official_events=["某活動"],
        gemini_client=fake,
    )

    assert result.narrative_text == FALLBACK_REPLY
    assert result.narrative_text.strip() != ""
    # source 仍是 "generated"：B9 沒有辦法知道 B1 內部是不是失敗了，這是
    # 架構上刻意的取捨，不是漏判。
    assert result.source == "generated"


# ── 輸入來源限定（AC2） ──────────────────────────────────────────────────


def test_no_weather_or_social_media_identifiers_in_implementation():
    """
    grep 不到天氣／社群 API 呼叫——用 AST 只掃**識別碼**（函式／類別／變數／
    屬性名稱），不掃字串常數與註解，模組 docstring 裡「早期原型把天氣餵進
    prompt」這種說明文字本來就該留著。
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"), filename=str(_MODULE_PATH))
    forbidden = ["weather", "temperature", "air_quality", "social", "instagram", "twitter"]

    offenders = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.Attribute):
            name = node.attr

        if name and any(f in name.lower() for f in forbidden):
            offenders.append(name)

    assert offenders == [], f"發現天氣／社群相關識別碼，超出 SDD 允許的輸入清單：{offenders}"


# ── 不含排程／快取／資料庫寫入（AC5） ─────────────────────────────────────


def test_function_signature_has_no_database_session_parameter():
    import inspect

    sig = inspect.signature(generate_daily_event_content)
    assert "db" not in sig.parameters
    assert "session" not in sig.parameters


def test_no_persistence_or_scheduling_identifiers_in_implementation():
    """
    v2.1 §6.4 的模組邊界：內容生成（腦袋）跟排程／快取（身體，屬 #26）
    分開。同樣用 AST 掃識別碼，不誤判 docstring 裡提到 `daily_event_cache`
    這個名詞的說明文字。
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"), filename=str(_MODULE_PATH))
    forbidden = ["sessionlocal", "session", "commit", "redis", "cache", "schedule"]

    offenders = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.Attribute):
            name = node.attr

        if name and any(f in name.lower() for f in forbidden):
            offenders.append(name)

    assert offenders == [], f"發現排程／快取／資料庫相關識別碼，超出這張票的範圍：{offenders}"
