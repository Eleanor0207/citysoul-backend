"""
Ticket #1．匿名玩家身分建立 API（含 Session Token 核發）。

驗收標準對照見 GitHub issue #1。
"""
import uuid

import jwt
import pytest
from sqlalchemy.exc import IntegrityError

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


# ── 0003：匿名保持可能，綁定資訊受約束 ─────────────────────────────────


def test_many_anonymous_players_can_coexist(client, db_session):
    """
    匿名是預設，不是例外。多個玩家的 `auth_provider` 全是 NULL，必須都能存在。

    這條會壞的寫法是把唯一性寫成表級 `UNIQUE(auth_provider, auth_provider_id)`
    ——那在 Postgres 上其實**不會**擋下重複的 NULL，所以症狀不是這個測試變紅，
    而是「約束看起來存在但什麼也沒保護」。真正會擋掉匿名玩家的是把
    `auth_provider` 設成 NOT NULL，那時這個測試會變紅。
    """
    ids = {
        client.post("/api/v1/players", json={"device_id": f"anon-{i}-{uuid.uuid4()}"})
        .json()["player_id"]
        for i in range(3)
    }

    assert len(ids) == 3
    for player_id in ids:
        row = db_session.query(models.Player).filter_by(player_id=player_id).one()
        assert row.auth_provider is None
        assert row.auth_provider_id is None


def test_half_bound_account_is_rejected(db_session, unique_device_id):
    """
    「有 provider 沒有 id」在應用層沒有意義，由資料庫的 CHECK 擋掉，
    而不是靠每個寫入點自己記得檢查。
    """
    db_session.add(
        models.Player(device_id=unique_device_id, auth_provider="google", auth_provider_id=None)
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_provider_account_cannot_bind_twice(db_session):
    """
    同一個 Google 帳號不能綁到兩個玩家身上。partial unique index 只約束
    已綁定的列，所以它擋得住這個，同時放行上面那一堆匿名玩家。
    """
    provider_id = f"google-{uuid.uuid4()}"
    for i in range(2):
        db_session.add(
            models.Player(
                device_id=f"dev-{i}-{uuid.uuid4()}",
                auth_provider="google",
                auth_provider_id=provider_id,
            )
        )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
