"""
Ticket #36．GET /profile 玩家個人頁彙總查詢整合與雙端點一致性測試。
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.body.tokens import issue_session_token


@pytest.fixture
def test_client():
    return TestClient(app)


def test_missing_session_token_returns_401(test_client):
    """驗證未帶或無效 session token 回傳 401。"""
    resp = test_client.get("/api/v1/profile")
    assert resp.status_code == 401

    resp_invalid = test_client.get("/api/v1/profile", headers={"Authorization": "Bearer invalid_token"})
    assert resp_invalid.status_code == 401


def test_cold_start_returns_empty_arrays(test_client):
    """驗證全新玩家冷啟動回傳空陣列 {"quests": [], "resonance": []} (HTTP 200)。"""
    pid = str(uuid.uuid4())
    token = issue_session_token(pid)

    resp = test_client.get(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"quests": [], "resonance": []}
