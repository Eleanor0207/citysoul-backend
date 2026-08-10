"""
Ticket #37．GET /players/me/memory-summary 記憶摘要 API 測試。
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
    """驗證缺少 session token 回傳 401。"""
    resp = test_client.get("/api/v1/players/me/memory-summary")
    assert resp.status_code == 401


def test_cold_start_returns_empty_array(test_client):
    """驗證全新玩家冷啟動回傳空陣列 {"memories": []} (HTTP 200)。"""
    pid = str(uuid.uuid4())
    token = issue_session_token(pid)

    resp = test_client.get(
        "/api/v1/players/me/memory-summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"memories": []}
