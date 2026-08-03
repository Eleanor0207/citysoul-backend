"""
Ticket #1．匿名玩家身分建立 API（含 Session Token 核發）。

驗收標準對照見 GitHub issue #1。
"""
import jwt
import pytest

from app.core.config import settings
from app.modules.body import models


def test_create_new_player_returns_session_token(client, unique_device_id, db_session):
    resp = client.post("/api/v1/players", json={"device_id": unique_device_id})

    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == unique_device_id
    assert body["account_id"] is None
    assert "player_id" in body
    assert "created_at" in body
    assert "session_token" in body

    # 確認真的寫進了 players 表
    player = db_session.query(models.Player).filter_by(device_id=unique_device_id).first()
    assert player is not None
    assert str(player.player_id) == body["player_id"]


def test_repeated_call_same_device_id_returns_same_player_new_token(client, unique_device_id, db_session):
    first = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    second = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()

    assert first["player_id"] == second["player_id"]

    # 不會重複建立資料列
    count = db_session.query(models.Player).filter_by(device_id=unique_device_id).count()
    assert count == 1

    # 重新核發了一張新的 token（見 SDD 第6節：重複呼叫要重新核發）
    assert first["session_token"] != second["session_token"]


@pytest.mark.parametrize("invalid_device_id", ["", "   "])
def test_empty_or_blank_device_id_returns_422(client, invalid_device_id):
    resp = client.post("/api/v1/players", json={"device_id": invalid_device_id})
    assert resp.status_code == 422


def test_missing_device_id_field_returns_422(client):
    resp = client.post("/api/v1/players", json={})
    assert resp.status_code == 422


def test_session_token_claims(client, unique_device_id):
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    token = body["session_token"]

    decoded = jwt.decode(token, settings.session_token_secret, algorithms=["HS256"])

    assert decoded["sub"] == body["player_id"]
    assert decoded["purpose"] == "session"

    ninety_days_seconds = 90 * 24 * 60 * 60
    assert decoded["exp"] - decoded["iat"] == ninety_days_seconds


def test_session_token_uses_hs256(client, unique_device_id):
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    token = body["session_token"]

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "HS256"
