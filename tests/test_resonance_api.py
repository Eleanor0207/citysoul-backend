"""
Ticket #35．GET /resonance/{spiritId} 共鳴進度查詢整合與邊界測試。
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.body import models
from app.modules.body.resonance import next_threshold, stage_for_value


@pytest.fixture
def test_client():
    return TestClient(app)


def test_resonance_threshold_table_logic():
    """驗證 0, 9, 10, 39, 40, 99, 100, 250 邊界階段計算。"""
    test_cases = [
        (0, 0, 10),
        (9, 0, 10),
        (10, 1, 40),
        (39, 1, 40),
        (40, 2, 100),
        (99, 2, 100),
        (100, 3, None),
        (250, 3, None),
    ]
    for val, expected_stage, expected_next in test_cases:
        assert stage_for_value(val) == expected_stage, f"value={val} 階段應為 {expected_stage}"
        assert next_threshold(val) == expected_next, f"value={val} 下一門檻應為 {expected_next}"


def test_missing_session_token_returns_401(test_client):
    """驗證未帶或無效 session token 回傳 401。"""
    resp = test_client.get("/api/v1/resonance/longshan_temple")
    assert resp.status_code == 401


def test_non_existent_spirit_returns_404(test_client):
    """驗證查詢不存在的 spirit_id 回傳 404。"""
    p_resp = test_client.post("/api/v1/players", json={"device_id": f"dev-{uuid.uuid4()}"})
    token = p_resp.json()["session_token"]

    resp = test_client.get(
        "/api/v1/resonance/non_existent_spirit_999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "spirit not found"
