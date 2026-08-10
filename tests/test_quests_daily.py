"""
Ticket #33．GET /quests/daily 任務列表查詢整合測試。
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.body import models
from app.modules.body.quests import STATUS_COMPLETED, STATUS_DAILY_LIMIT_REACHED, STATUS_IN_PROGRESS


@pytest.fixture
def test_client():
    return TestClient(app)


def test_missing_session_token_returns_401(test_client):
    """驗證未帶或無效 session token 回傳 401。"""
    resp = test_client.get("/api/v1/quests/daily")
    assert resp.status_code == 401

    resp_invalid = test_client.get("/api/v1/quests/daily", headers={"Authorization": "Bearer garbage"})
    assert resp_invalid.status_code == 401


def test_cold_start_returns_empty_list(test_client):
    """驗證全新玩家冷啟動回傳空陣列 (HTTP 200)。"""
    p_resp = test_client.post("/api/v1/players", json={"device_id": f"dev-{uuid.uuid4()}"})
    token = p_resp.json()["session_token"]

    resp = test_client.get("/api/v1/quests/daily", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"quests": []}
