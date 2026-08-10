"""
Ticket #34．POST /quests/{questId}/complete 任務完成與共鳴入帳整合測試。
"""
import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.sense_tokens import SENSE_TOKEN_HEADER, issue_sense_token


@pytest.fixture
def test_client():
    return TestClient(app)


def test_missing_tokens_returns_401(test_client):
    """驗證缺憑證回傳 401。"""
    resp = test_client.post("/api/v1/quests/test_spirit:daily/complete", json={})
    assert resp.status_code == 401


def test_sense_token_rejected_returns_401(test_client):
    """驗證持 Sense Token 呼叫 complete 被拒絕。"""
    from app.modules.body.tokens import issue_session_token
    pid = str(uuid.uuid4())
    token = issue_session_token(pid)
    sense_token = issue_sense_token(pid, "test_spirit")

    headers = {
        "Authorization": f"Bearer {token}",
        SENSE_TOKEN_HEADER: sense_token,
    }
    resp = test_client.post(
        "/api/v1/quests/test_spirit:daily/complete", json={}, headers=headers
    )
    assert resp.status_code in (401, 403)
