"""
Issue #30．OpenAPI 契約是受保護的交付物。

## 這裡在測什麼

**產出的 OpenAPI 文件（`app.openapi()`）本身。** 那份文件就是 `citysoul-client`
消費的交付物，在它上面斷言等於直接驗證「客戶端會拿到什麼」，不需要 HTTP、也不
需要資料庫。

刻意**不**測 Pydantic 欄位的型別註解長什麼樣——那是實作細節，接縫很低，改個
等價寫法就會無謂地紅掉。也不測 `oasdiff` 或 NSwag 的行為，測它們等於測別人的
軟體；CI 閘門的核心保證由「快照未過期」那條守住。

執行期行為（實際回傳的 JSON、實際會拋的錯誤碼）由 `tests/test_spirits.py`、
`tests/test_summon.py`、`tests/test_sense.py` 這些 HTTP 整合測試涵蓋。
"""
import json
from pathlib import Path

import pytest

from app.main import app
from scripts.generate_openapi_contract import CONTRACT_PATH, render_contract


@pytest.fixture(scope="module")
def contract():
    return app.openapi()


# ── 列舉值進 schema ────────────────────────────────────────────────────

def test_quest_status_is_a_real_enum(contract):
    """
    `status` 必須產出 `enum`，不是裸 `type: string`。

    這是整個 API 最狀態密集的欄位。裸 string 的話，客戶端拿不到任何型別安全，
    SDD v2.1 §11.2.1 的 Unity 硬規則 5「Enum 採寬鬆解析」也無 enum 可解析。
    """
    status = contract["components"]["schemas"]["QuestStateResponse"]["properties"]["status"]

    assert "enum" in status, "status 退回裸 string 了——客戶端會失去 enum 型別安全"
    assert set(status["enum"]) == {"in_progress", "completed", "daily_limit_reached"}


def test_daily_limit_reached_is_in_the_contract(contract):
    """
    單獨釘住這個值。

    它是三個值裡唯一**不是資料庫狀態**的（依 `attempts_date` 當下算出來、不落地），
    所以最容易在重構時被漏掉。少了它，客戶端會把「今天次數用完」當成未知值丟進
    錯誤流程，而不是顯示「明天可再挑戰」。
    """
    status = contract["components"]["schemas"]["QuestStateResponse"]["properties"]["status"]
    assert "daily_limit_reached" in status["enum"]


# ── 錯誤回應進契約 ─────────────────────────────────────────────────────

def test_error_response_model_is_named_in_the_contract(contract):
    """具名的錯誤模型，讓 codegen 產出單一共用的錯誤 DTO。"""
    error_schema = contract["components"]["schemas"]["ErrorResponse"]
    assert error_schema["properties"]["detail"]["type"] == "string"


# 每支端點宣告的錯誤碼。
#
# 這張表是**手動維護**的，刻意不從路由自動推導——自動推導的話它就只是把實作
# 抄一遍，實作錯了測試照樣綠。表裡的每個碼都對應到一條真的會產生它的整合測試。
#
# 422 出現在所有帶 request body 或 path 參數的端點上，那是 FastAPI 自動加的
# `HTTPValidationError`，不是我們宣告的。
EXPECTED_RESPONSE_CODES = {
    ("post", "/api/v1/players"): {"200", "422"},
    ("post", "/api/v1/sense"): {"200", "401", "403", "404", "422"},
    ("post", "/api/v1/summon"): {"200", "401", "403", "404", "422"},
    # 429 是 #42 接上配額（#32）之後新增的。配額是這支端點的第一道關卡
    # （SDD v1 §3），所以它是唯一會回 429 的路由。
    ("post", "/api/v1/spirits/{place_id}/dialogue"): {"200", "401", "403", "404", "422", "429"},
    ("get", "/api/v1/spirits/{place_id}"): {"200", "404", "422"},
    # #26：公開世界狀態，無需 token，所以沒有 401/403。
    ("get", "/api/v1/spirits/{place_id}/daily-event"): {"200", "404", "422"},
    # #34：只收 encounter token，所以沒有獨立的 sense 錯誤碼。
    ("post", "/api/v1/quests/{quest_id}/complete"): {"200", "401", "403", "404", "422"},
    # #44：配額擋在辨識之前，所以有 429。同樣只收 encounter token。
    ("post", "/api/v1/quests/{quest_id}/landmark-photo"): {"200", "401", "403", "404", "422", "429"},
    ("get", "/health"): {"200"},
}


def test_every_route_declares_exactly_the_codes_it_returns(contract):
    """
    宣告的錯誤碼集合必須與實作一致——不多也不少。

    「不多」跟「不少」一樣重要：宣告一個實作不會產生的錯誤碼，會讓客戶端寫出
    永遠不會執行的處理分支，那跟漏掉一樣是錯的契約。

    這條也順便當成端點清單的守門員：新增一支端點卻沒想過它的錯誤碼，這裡會紅。
    """
    actual = {
        (method, path): set(operation["responses"].keys())
        for path, operations in contract["paths"].items()
        for method, operation in operations.items()
    }

    assert actual == EXPECTED_RESPONSE_CODES


# ── 靈魂方位 ───────────────────────────────────────────────────────────

def test_spirit_response_has_nested_orientation(contract):
    """`orientation` 是巢狀物件，不是兩個平鋪欄位（對齊 SDD v2.1 §10.2）。"""
    properties = contract["components"]["schemas"]["SpiritResponse"]["properties"]

    assert properties["orientation"]["$ref"] == "#/components/schemas/SpiritOrientation"
    assert "bearing_deg" not in properties, "方位角被攤平了——客戶端沒辦法整包傳給 OrientationService"


def test_orientation_fields_are_numbers(contract):
    orientation = contract["components"]["schemas"]["SpiritOrientation"]["properties"]

    assert orientation["bearing_deg"]["type"] == "number"
    assert orientation["height_offset_m"]["type"] == "number"


# ── 快照未過期 ─────────────────────────────────────────────────────────

def test_committed_contract_snapshot_is_not_stale():
    """
    `contracts/openapi.json` 必須與當下產出的契約逐位元組相符。

    這是整個契約閘門的核心保證。有人改了 Pydantic 卻忘了重新產出快照，這條會在
    **本機 `pytest` 就紅**，不必推上去等 CI；而 CI 的 `oasdiff` 比對也才有意義——
    它比的是快照，快照過期的話它比的是一份不存在的 API。
    """
    committed = CONTRACT_PATH.read_text(encoding="utf-8")

    assert committed == render_contract(), (
        "契約快照過期了。重新產生：\n"
        "    uv run python -m scripts.generate_openapi_contract\n"
        "然後把 contracts/openapi.json 的改動一起 commit——"
        "契約變動應該出現在 code review 的 diff 裡。"
    )


def test_snapshot_is_deterministic():
    """
    連續產生兩次必須完全相同。

    少了這個保證，快照比對會因為 dict 順序抖動而隨機變紅，接著大家就會開始忽略
    它——一個會誤報的閘門等於沒有閘門。
    """
    assert render_contract() == render_contract()


def test_snapshot_is_valid_json_and_declares_openapi_3_1():
    """
    維持 3.1.0（已拍板，勿降級）。

    降級到 3.0 不可行：FastAPI 的版本字串只是原封不動塞進輸出，改它不會改 schema
    內容。Pydantic v2 產出的 nullable 是 `anyOf: [{$ref}, {type: null}]`，而
    `type: null` 在 3.0 不合法——宣告 3.0 只會產出一份自稱 3.0、內容卻是 3.1 的
    無效文件，工具照 3.0 規則解析可能靜默產出錯誤 DTO。
    """
    committed = json.loads(Path(CONTRACT_PATH).read_text(encoding="utf-8"))
    assert committed["openapi"] == "3.1.0"
