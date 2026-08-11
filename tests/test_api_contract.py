"""
Ticket #30．API 契約單一真相來源：enum、錯誤回應、靈魂方位與 CI 閘門。

驗收標準對照見 GitHub issue #30。主要接縫是 `app.openapi()`——這份文件就是
`citysoul-client` 消費的交付物，斷言在它上面等於直接驗證客戶端會拿到什麼，
不需要 HTTP、不需要資料庫。少數執行期行為（`orientation` 真的回傳正確的值）
走 `TestClient`，見 `tests/test_spirits.py`。
"""
from app.main import app
from scripts.generate_openapi_contract import _OUTPUT_PATH, generate


def _schema() -> dict:
    return app.openapi()


# ── status 是真正的 enum（issue #30 AC，SDD v2.1 §11.2.1 硬規則5）───────

def test_quest_status_is_an_enum_with_exactly_three_members():
    status_schema = _schema()["components"]["schemas"]["QuestStateResponse"]["properties"]["status"]

    assert status_schema.get("enum") is not None, "status 是裸 string，不是 enum"
    assert set(status_schema["enum"]) == {"in_progress", "completed", "daily_limit_reached"}


def test_quest_list_item_status_uses_the_same_enum():
    """`GET /quests/daily`／`GET /profile` 的 status 跟 `QuestStateResponse` 同一組值。"""
    status_schema = _schema()["components"]["schemas"]["QuestListItem"]["properties"]["status"]
    assert set(status_schema.get("enum", [])) == {"in_progress", "completed", "daily_limit_reached"}


# ── 錯誤碼宣告與實作一致（issue #30 AC）─────────────────────────────────
#
# 逐支路由對照現況（issue #30 body 已明列前三支；其餘依 router.py 實際的
# HTTPException／Depends 組合列出）。mutation 驗證：把任一路由的 responses
# 拿掉一個實際會拋的碼，這裡就會紅（下面的 test_declared_error_codes_match
# 直接比對這份表跟契約，改壞其中一邊、不改另一邊就會示範這件事）。

_EXPECTED_ERROR_CODES: dict[tuple[str, str], set[str]] = {
    ("post", "/api/v1/players"): {"422"},
    ("post", "/api/v1/sense"): {"401", "403", "404", "422"},
    ("post", "/api/v1/summon"): {"401", "403", "404", "422"},
    ("get", "/api/v1/quests/daily"): {"401"},
    ("post", "/api/v1/quests/{quest_id}/complete"): {"401", "403", "404", "422"},
    ("get", "/api/v1/resonance/{spirit_id}"): {"401", "404", "422"},
    ("get", "/api/v1/profile"): {"401"},
    ("get", "/api/v1/players/me/memory-summary"): {"401"},
    ("post", "/api/v1/spirits/{place_id}/dialogue"): {"401", "403", "404", "422", "429"},
    ("get", "/api/v1/spirits/{place_id}"): {"404", "422"},
    ("get", "/api/v1/assets/{avatar_id}"): {"404", "422"},
}

# FastAPI 自動幫任何有路徑參數的端點加上 422（路徑參數本身也走 pydantic
# 驗證，即使只是宣告成 `str`）——這是框架行為，不是這支路由手動 raise 的，
# 上面的表格已經照實際產出調整過，不是憑印象寫的。`/quests/daily`／
# `/profile`／`/players/me/memory-summary` 沒有路徑參數，所以沒有這個碼。


def test_declared_error_codes_match_the_curated_expectation():
    """
    先確認契約宣告的錯誤碼集合跟人工整理的現況表一致——這份表格本身是
    對照 router.py 實作讀出來的（見上方註解），不是憑空寫的。
    """
    paths = _schema()["paths"]

    for (method, path), expected in _EXPECTED_ERROR_CODES.items():
        declared = set(paths[path][method]["responses"].keys()) - {"200"}
        assert declared == expected, f"{method.upper()} {path}：契約宣告 {declared}，預期 {expected}"


def test_every_declared_error_uses_the_shared_error_model():
    """統一錯誤回應模型（issue #30）：每個非 200/422 的回應都指向同一個 schema。"""
    paths = _schema()["paths"]

    for (method, path), codes in _EXPECTED_ERROR_CODES.items():
        for code in codes - {"422"}:  # 422 是 FastAPI 內建的 HTTPValidationError，不歸這裡管
            response = paths[path][method]["responses"][code]
            ref = response["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorResponse"), f"{method.upper()} {path} {code} 沒有用 ErrorResponse"


# ── orientation 出現在契約裡（issue #30）────────────────────────────────

def test_spirit_response_has_orientation_object():
    spirit_schema = _schema()["components"]["schemas"]["SpiritResponse"]
    assert "orientation" in spirit_schema["properties"]

    orientation_ref = spirit_schema["properties"]["orientation"]["$ref"]
    orientation_schema = _schema()["components"]["schemas"][orientation_ref.rsplit("/", 1)[-1]]

    assert orientation_schema["properties"]["bearing_deg"]["type"] == "number"
    assert orientation_schema["properties"]["height_offset_m"]["type"] == "number"
    assert set(orientation_schema["required"]) == {"bearing_deg", "height_offset_m"}


# ── 快照沒有過期（issue #30 AC，本機 pytest 就抓得到，不必等 CI）────────

def test_committed_snapshot_matches_freshly_generated_contract():
    """
    有人改了 Pydantic 模型卻忘記重新產出快照時，這條要紅。

    mutation 驗證：暫時改一個欄位型別再跑這條測試，會失敗；跑
    `uv run python -m scripts.generate_openapi_contract` 重新產出後再跑，
    會變綠——這就是這條測試存在的完整迴路。
    """
    assert _OUTPUT_PATH.exists(), (
        f"{_OUTPUT_PATH} 不存在，先跑一次 `uv run python -m scripts.generate_openapi_contract`"
    )

    committed = _OUTPUT_PATH.read_text(encoding="utf-8")
    freshly_generated = generate()

    assert committed == freshly_generated, (
        "contracts/openapi.json 已經過期。"
        "跑 `uv run python -m scripts.generate_openapi_contract` 重新產出並一起 commit。"
    )
