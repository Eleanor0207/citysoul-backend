"""
B9．當日情境內容生成（issue #20）。

純單元測試，模型以 fake 注入——**不需要 GCP 憑證、不需要資料庫**。
「不需要資料庫」本身就是被測試的性質之一（見模組邊界那組）。
"""
import ast
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.db.seed import LONGSHAN_SPIRIT_ID
from app.modules.brain.daily_event import (
    CONTENT_REVIEW_STATUS,
    _DEFAULT_FALLBACK_NARRATIVE,
    _FALLBACK_NARRATIVES,
    DailyEventContent,
    DailyEventInputs,
    build_daily_event_prompt,
    generate_daily_event_content,
)
from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient
from scripts.import_spirits import SPIRITS_YAML

_PLACE = "taipei_longshan"
_DATE = date(2026, 8, 6)
_GENERATED = "今天廟埕比平常安靜，只有幾個老人坐在樹下。香還是點著的。"

_SOURCE_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "modules" / "brain" / "daily_event.py"
)


def _inputs(**kwargs) -> DailyEventInputs:
    return DailyEventInputs(**kwargs)


# ── 介面 ───────────────────────────────────────────────────────────────

def test_returns_daily_event_content():
    result = generate_daily_event_content(
        FakeGeminiClient(response=_GENERATED),
        place_id=_PLACE,
        event_date=_DATE,
        inputs=_inputs(festival="中元節"),
    )

    assert isinstance(result, DailyEventContent)
    assert result.place_id == _PLACE
    assert result.event_date == _DATE
    assert result.narrative_text == _GENERATED
    assert result.is_fallback is False


def test_sources_record_which_inputs_were_used():
    """
    `sources` 存在的理由是**可稽核**：哪天有人懷疑內容摻了不該有的來源，
    這裡看得出來。
    """
    result = generate_daily_event_content(
        FakeGeminiClient(response=_GENERATED),
        place_id=_PLACE,
        event_date=_DATE,
        inputs=_inputs(festival="中元節", curated_notes=["廟方今年重修了偏殿"]),
    )

    assert set(result.sources) == {"festival", "curated_notes"}


# ── 輸入來源白名單 ─────────────────────────────────────────────────────

def test_only_the_three_allowed_input_kinds_exist():
    """
    SDD 允許的輸入只有三類：日期／節日、地標官方公開活動、人工審核素材。

    做成明確的資料結構而不是散落的參數，是為了讓「允許哪些輸入」在型別上就
    看得見——要加第四類就得改 `DailyEventInputs`，而那會出現在 code review 的
    diff 裡，不像多塞一個字串進 prompt 那樣容易溜過去。
    """
    fields = set(DailyEventInputs.__dataclass_fields__)

    assert fields == {"festival", "official_events", "curated_notes"}


def test_module_never_fetches_external_data():
    """
    🔒 AC：grep 不到任何天氣 API、社群 API 的呼叫。

    ⚠️ 這不是假設性的規則。曾有原型（`docs/aistudio-ar`）把天氣／氣溫／空氣
    品質餵進 prompt，那超出 SDD 允許的清單。要納入天氣得**先修改 SDD**，
    不是在這裡偷渡。

    用 ast 掃 import 與呼叫，抓的是程式碼裡存在的抓取路徑——包含只在某個分支
    才會走到、測試沒觸發的那些。
    """
    tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))

    forbidden_modules = {"requests", "httpx", "urllib", "aiohttp", "socket"}
    forbidden_calls = {"get", "post", "urlopen", "fetch"}
    offenders = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden_modules:
                    offenders.append(f"import {alias.name}（第 {node.lineno} 行）")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in forbidden_modules:
                offenders.append(f"from {node.module}（第 {node.lineno} 行）")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in forbidden_calls and isinstance(node.func.value, ast.Name):
                if node.func.value.id in forbidden_modules:
                    offenders.append(f"{node.func.value.id}.{node.func.attr}（第 {node.lineno} 行）")

    assert not offenders, f"當日情境生成去抓了外部資料：{offenders}"


@pytest.mark.parametrize("banned", ["weather", "temperature", "aqi", "air_quality", "twitter", "instagram"])
def test_source_mentions_no_banned_data_sources(banned):
    """
    連識別碼層級都不該出現這些字。註解可以提到它們（說明為什麼不用），
    所以這裡用 ast 掃識別碼而不是逐行 grep。
    """
    tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))

    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            identifiers.add(node.name.lower())

    assert not any(banned in ident for ident in identifiers)


def test_prompt_contains_only_the_supplied_inputs():
    prompt = build_daily_event_prompt(
        _PLACE, _DATE, _inputs(festival="中元節", official_events=["普度法會"])
    )

    assert "中元節" in prompt
    assert "普度法會" in prompt
    assert _PLACE in prompt
    assert _DATE.isoformat() in prompt


def test_prompt_carries_the_historical_boundary_rules():
    """當日情境同樣會講到這座地標，沒有理由讓它比一般對話寬鬆。"""
    from app.modules.brain.historical_boundary import get_historical_boundary_rules

    prompt = build_daily_event_prompt(_PLACE, _DATE, _inputs(festival="中元節"))

    assert get_historical_boundary_rules() in prompt


# ── 無合格輸入 ─────────────────────────────────────────────────────────

def test_no_qualifying_input_falls_back_without_calling_the_model():
    """
    AC：無合格輸入時回退人工預寫台詞，不拋例外、不產生空白內容。

    大多數日子既不是節日、也沒有官方活動、也沒有人工素材——那是**常態**，
    不是缺漏。也因為是常態，不該為它白呼叫一次模型。
    """
    client = FakeGeminiClient(response=_GENERATED)

    result = generate_daily_event_content(
        client, place_id=_PLACE, event_date=_DATE, inputs=_inputs()
    )

    assert result.is_fallback is True
    assert result.narrative_text.strip()
    assert client.call_count == 0


def test_inputs_default_to_empty():
    """完全不傳 inputs 也不該炸。"""
    result = generate_daily_event_content(
        FakeGeminiClient(), place_id=_PLACE, event_date=_DATE
    )

    assert result.is_fallback is True


def test_fallback_text_is_never_blank():
    """
    玩家每天打開都該看到東西，「今天沒素材」不是可接受的畫面。
    """
    result = generate_daily_event_content(
        FakeGeminiClient(), place_id=_PLACE, event_date=_DATE, inputs=_inputs()
    )

    assert len(result.narrative_text.strip()) > 10


def test_fallback_does_not_sound_like_a_system_message():
    """
    回退台詞寫得像「今天很平常」而不是「系統沒有資料」——大多數日子本來就很
    平常，玩家不需要知道我們的內容管線今天是空的。
    """
    text = generate_daily_event_content(
        FakeGeminiClient(), place_id=_PLACE, event_date=_DATE, inputs=_inputs()
    ).narrative_text

    for tell in ["系統", "資料", "無法", "錯誤", "尚未"]:
        assert tell not in text


# ── 回退台詞分地標 ─────────────────────────────────────────────────────
#
# 回退是**大多數日子**都會走到的路徑，不是稀有的例外。原本十個地標共用一句
# 「香火照舊」，天文館與美術館講出來明顯不對，而玩家看得到。

def _known_spirit_ids() -> set[str]:
    """
    實際會上線的 spirit_id：`content/spirits.yaml` 九個 ＋ seed 建的龍山寺。

    刻意不在測試裡抄一份清單——抄的那份不會跟著 `content/` 一起改，而它一旦
    過期，這組測試就變成在驗證自己的假設。
    """
    data = yaml.safe_load(SPIRITS_YAML.read_text(encoding="utf-8"))
    return {entry["spirit_id"] for entry in data["spirits"]} | {LONGSHAN_SPIRIT_ID}


def test_every_fallback_key_is_a_real_spirit():
    """
    打錯一個字不會噴錯，只會靜默退回中性台詞——那正是沒人會發現的失敗方式。
    """
    unknown = set(_FALLBACK_NARRATIVES) - _known_spirit_ids()

    assert not unknown, f"回退台詞掛在不存在的 spirit_id 上（打錯字？）：{sorted(unknown)}"


def test_every_spirit_has_its_own_fallback():
    """
    新地標上線時這條會亮紅燈，那是刻意的：中性台詞是安全網，不是交付標準。
    """
    missing = _known_spirit_ids() - set(_FALLBACK_NARRATIVES)

    assert not missing, (
        f"這些地標還在用中性回退台詞，請到 daily_event.py 的 _FALLBACK_NARRATIVES "
        f"補上專屬台詞：{sorted(missing)}"
    )


def test_temple_wording_does_not_reach_non_temples():
    """
    「香火」只對廟成立。這條測試是這次改動的起因，留著擋下一次複製貼上。
    """
    temples = {"longshan_temple", "xiahai_city_god_temple"}

    for place_id, text in _FALLBACK_NARRATIVES.items():
        if place_id in temples:
            continue
        assert "香" not in text, f"{place_id} 的回退台詞出現廟宇用語：{text}"

    assert "香" not in _DEFAULT_FALLBACK_NARRATIVE


@pytest.mark.parametrize("place_id", sorted(_FALLBACK_NARRATIVES))
def test_each_fallback_is_first_person_and_not_a_system_message(place_id):
    """每一句都要通過「不像系統訊息」那一關，不是只有預設那句。"""
    text = _FALLBACK_NARRATIVES[place_id]

    assert len(text.strip()) > 10
    for tell in ["系統", "資料", "無法", "錯誤", "尚未"]:
        assert tell not in text, f"{place_id}：{text}"


def test_fallback_differs_between_places():
    common = dict(event_date=_DATE, inputs=_inputs())

    temple = generate_daily_event_content(
        FakeGeminiClient(), place_id="longshan_temple", **common
    )
    museum = generate_daily_event_content(
        FakeGeminiClient(), place_id="taipei_astronomical_museum", **common
    )

    assert temple.is_fallback is True and museum.is_fallback is True
    assert temple.narrative_text != museum.narrative_text


def test_unknown_place_falls_back_to_neutral_text():
    """
    沒寫過台詞的地標退化成一句平淡但講得通的話，不是讓端點掛掉——少寫台詞
    由上面那條測試守門，不該由玩家的畫面來承擔。
    """
    result = generate_daily_event_content(
        FakeGeminiClient(),
        place_id="a_place_nobody_wrote_lines_for",
        event_date=_DATE,
        inputs=_inputs(),
    )

    assert result.narrative_text == _DEFAULT_FALLBACK_NARRATIVE


# ── 生成失敗 ───────────────────────────────────────────────────────────

def test_model_failure_falls_back():
    """AC：Gemini 失敗／逾時同樣回退。"""
    result = generate_daily_event_content(
        FakeGeminiClient(response=FALLBACK_REPLY),
        place_id=_PLACE,
        event_date=_DATE,
        inputs=_inputs(festival="中元節"),
    )

    assert result.is_fallback is True
    assert result.narrative_text.strip()


def test_empty_model_response_falls_back():
    result = generate_daily_event_content(
        FakeGeminiClient(response=""),
        place_id=_PLACE,
        event_date=_DATE,
        inputs=_inputs(festival="中元節"),
    )

    assert result.is_fallback is True


def test_fallback_records_no_sources():
    """回退內容不是從輸入生成的，`sources` 應該是空的——否則稽核會誤判。"""
    result = generate_daily_event_content(
        FakeGeminiClient(response=FALLBACK_REPLY),
        place_id=_PLACE,
        event_date=_DATE,
        inputs=_inputs(festival="中元節"),
    )

    assert result.sources == []


# ── 模組邊界：不排程、不快取、不寫 DB ─────────────────────────────────

def test_signature_has_no_db_session():
    """
    🔒 AC：沒有 DB session 參數。

    這條守住 v2.1 §6.4 的模組邊界：內容生成（腦袋）與排程／快取（身體）分開。
    一旦有人在這裡加了 session，兩者就開始溶解成同一團東西。
    """
    import inspect

    params = set(inspect.signature(generate_daily_event_content).parameters)

    assert "db" not in params
    assert "session" not in params


def test_module_does_not_import_the_database_or_cache():
    """
    `daily_event_cache` 的讀寫屬 #26。這裡連 import 都不該有——有 import 就代表
    有人開始在這裡碰資料庫了。
    """
    tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))

    forbidden = {"sqlalchemy", "redis"}
    offenders = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in forbidden:
                offenders.append(node.module)
            # app.core.database / app.core.redis_client 也算。
            if (node.module or "").startswith("app.core.database"):
                offenders.append(node.module)
            if (node.module or "").startswith("app.core.redis"):
                offenders.append(node.module)

    assert not offenders, f"當日情境生成碰了資料庫／快取：{offenders}"


def test_module_has_no_scheduling():
    """排程屬 #26。這裡不該有 cron、sleep、背景執行緒。"""
    tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))

    forbidden = {"sched", "apscheduler", "celery", "threading", "asyncio"}
    offenders = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] in forbidden]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in forbidden:
                offenders.append(node.module)

    assert not offenders, f"當日情境生成含排程：{offenders}"


# ── 文案審核狀態 ───────────────────────────────────────────────────────

def test_content_is_marked_as_pending_review():
    assert CONTENT_REVIEW_STATUS == "PENDING_NARRATIVE_REVIEW"


def test_review_marker_never_reaches_the_player():
    result = generate_daily_event_content(
        FakeGeminiClient(), place_id=_PLACE, event_date=_DATE, inputs=_inputs()
    )

    assert CONTENT_REVIEW_STATUS not in result.narrative_text
