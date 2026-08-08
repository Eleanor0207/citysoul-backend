"""
API 契約測試（#30）。

主要接縫是 `app.openapi()` 產出的文件本身——這份文件就是 `citysoul-client`
會拿去 codegen 的交付物，斷言它等於直接驗證「客戶端會拿到什麼」，不需要
HTTP、不需要資料庫。**不測實作細節**：不逐一斷言某個 Pydantic 欄位的型別
註解長什麼樣，只測外部可觀察的契約形狀。
"""
import json

from scripts.export_openapi import _CONTRACT_PATH, render_contract


def _openapi() -> dict:
    return json.loads(render_contract())


def test_quest_status_is_a_real_enum_not_a_bare_string():
    """
    `QuestStateResponse.status` 要產出 `enum` 且成員恰為三個合法值，不是裸
    `string`——少了這個，Unity 硬規則5「Enum 採寬鬆解析」無 enum 可解析。
    """
    schema = _openapi()["components"]["schemas"]["QuestStateResponse"]
    status_schema = schema["properties"]["status"]

    assert status_schema["type"] == "string"
    assert set(status_schema["enum"]) == {"in_progress", "completed", "daily_limit_reached"}


# 每支路由「宣告」的錯誤碼集合，逐支對齊 router.py 的實作現況。422 不列在
# 這裡——那是 FastAPI 對 body/path 驗證自動加的，形狀跟 ErrorResponse 不同，
# 不該被這個測試當成「我們宣告的」錯誤碼。
_EXPECTED_DECLARED_ERROR_CODES = {
    ("post", "/api/v1/players"): set(),
    ("post", "/api/v1/sense"): {"401", "403", "404"},
    ("post", "/api/v1/summon"): {"401", "403", "404"},
    ("post", "/api/v1/spirits/{place_id}/dialogue"): {"401", "403", "404"},
    ("get", "/api/v1/spirits/{place_id}"): {"404"},
    ("post", "/api/v1/quests/{quest_id}/complete"): {"401", "403", "404"},
    # 公開世界狀態，不需要 token——所以沒有 401／403。地標不存在才 404；
    # 地標存在但沒有內容會走保底鏈路回 200，不是錯誤。
    ("get", "/api/v1/spirits/{place_id}/daily-event"): {"404"},
}


def test_every_documented_route_is_covered_by_the_error_code_table():
    """
    守門測試：新增端點時如果忘記把它加進 `_EXPECTED_DECLARED_ERROR_CODES`，
    上面那條逐支比對的測試會**靜默地跳過它**——它只走表裡有的項目。這條讓
    「漏加」變成一個看得見的失敗。

    `/health` 不在 `/api/v1` 底下，不是對客戶端的契約端點，排除。
    """
    documented = {
        (method, path)
        for path, operations in _openapi()["paths"].items()
        for method in operations
        if path.startswith("/api/v1")
    }

    assert documented == set(_EXPECTED_DECLARED_ERROR_CODES)


def test_routes_declare_the_error_codes_they_actually_raise():
    """
    每支路由宣告的錯誤碼集合，要跟 `_EXPECTED_DECLARED_ERROR_CODES`（對齊
    router.py 實作現況）一致——不多宣告理論上不會發生的碼，也不少宣告
    實際會拋的碼。
    """
    paths = _openapi()["paths"]

    for (method, path), expected in _EXPECTED_DECLARED_ERROR_CODES.items():
        declared = {
            code for code in paths[path][method]["responses"] if code not in {"200", "422"}
        }
        assert declared == expected, f"{method.upper()} {path}: {declared} != {expected}"


def test_declared_error_responses_use_the_unified_error_model():
    """凡是我們自己宣告的錯誤碼，形狀都必須是 `ErrorResponse`，不是隨便一個 model。"""
    paths = _openapi()["paths"]

    for (method, path), expected in _EXPECTED_DECLARED_ERROR_CODES.items():
        responses = paths[path][method]["responses"]
        for code in expected:
            ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
            assert ref == "#/components/schemas/ErrorResponse"


def test_committed_snapshot_matches_freshly_generated_contract():
    """
    契約快照漂移偵測（#30 user story 10）：改了 Pydantic model 卻忘記重新
    產生 `contracts/openapi.json` 時，這個測試在本機 `pytest` 就會紅，
    不用等 CI 才被打回。
    """
    committed = _CONTRACT_PATH.read_text()
    fresh = render_contract()

    assert committed == fresh, (
        "contracts/openapi.json 跟目前程式碼產出的契約不一致。"
        "改了 API 之後要重新產生並一起 commit：\n"
        "    uv run python -m scripts.export_openapi"
    )
