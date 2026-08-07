"""
Ticket #33．GET /quests/daily 任務列表查詢。

驗收標準對照見 GitHub issue #33。透過 `/summon` 走真實流程建立
`quest_progress` 列，邊界情境（跨日、達上限）直接寫 DB——跟
`test_quest_progress.py` 同一套做法，理由同樣是：沒有別的方法能在測試裡
讓一整天真的過去。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.body import models
from app.modules.body.quests import MAX_DAILY_ATTEMPTS, quest_id_for_spirit, taipei_today

_LAT, _LON = 25.0955, 121.5186


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}", display_name="測試地標",
        latitude=_LAT, longitude=_LON, summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client, db_session):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    player_id = uuid.UUID(body["player_id"])
    yield player_id, body["session_token"]
    db_session.query(models.QuestProgress).filter_by(player_id=player_id).delete()
    db_session.commit()


def _list_quests(client, token):
    return client.get("/api/v1/quests/daily", headers={"Authorization": f"Bearer {token}"})


def _summon(client, token, spirit):
    return client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit.spirit_id, "latitude": spirit.latitude, "longitude": spirit.longitude},
        headers={"Authorization": f"Bearer {token}"},
    )


# ── 憑證（issue #33 AC1）─────────────────────────────────────────────────

def test_no_auth_header_returns_401(client):
    assert client.get("/api/v1/quests/daily").status_code == 401


def test_garbage_bearer_returns_401(client):
    response = client.get("/api/v1/quests/daily", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401


def test_encounter_token_cannot_be_used_as_session_token(client, spirit, player):
    """拿 encounter 金鑰簽的 token 塞進 Authorization 不能過——證明種類不可互換。"""
    import jwt

    from app.core.config import settings

    pid, _ = player
    forged = jwt.encode(
        {"sub": str(pid), "spirit_id": spirit.spirit_id, "purpose": "encounter"},
        settings.encounter_token_secret, algorithm="HS256",
    )
    response = client.get("/api/v1/quests/daily", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


# ── 正常回傳（issue #33 AC2）─────────────────────────────────────────────

def test_returns_progress_with_all_four_fields(client, spirit, player):
    pid, token = player
    _summon(client, token, spirit)

    body = _list_quests(client, token).json()

    assert body == {
        "quests": [
            {
                "quest_id": quest_id_for_spirit(spirit.spirit_id),
                "spirit_id": spirit.spirit_id,
                "status": "in_progress",
                "attempts_today": 0,
            }
        ]
    }


# ── 達上限（issue #33 AC3）───────────────────────────────────────────────

def test_at_daily_limit_reports_daily_limit_reached_without_mutating_db(
    client, spirit, player, db_session
):
    pid, token = player
    _summon(client, token, spirit)
    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=pid, quest_id=quest_id_for_spirit(spirit.spirit_id))
        .first()
    )
    progress.attempts_today = MAX_DAILY_ATTEMPTS
    db_session.commit()

    body = _list_quests(client, token).json()

    assert body["quests"][0]["status"] == "daily_limit_reached"

    db_session.refresh(progress)
    assert progress.status == "in_progress"  # DB 沒有被查詢動過


# ── 跨日重置（issue #33 AC4）─────────────────────────────────────────────

def test_after_taipei_midnight_attempts_today_shows_zero(client, spirit, player, db_session):
    pid, token = player
    _summon(client, token, spirit)
    progress = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=pid, quest_id=quest_id_for_spirit(spirit.spirit_id))
        .first()
    )
    yesterday_taipei = taipei_today(datetime.now(timezone.utc)) - timedelta(days=1)
    progress.attempts_today = MAX_DAILY_ATTEMPTS
    progress.attempts_date = yesterday_taipei
    db_session.commit()

    body = _list_quests(client, token).json()

    assert body["quests"][0]["attempts_today"] == 0
    assert body["quests"][0]["status"] == "in_progress"


# ── 冷啟動（issue #33 AC5）───────────────────────────────────────────────

def test_new_player_returns_empty_list_not_404(client, player):
    _, token = player
    response = _list_quests(client, token)
    assert response.status_code == 200
    assert response.json() == {"quests": []}


# ── 隔離（issue #33 AC6）─────────────────────────────────────────────────

def test_only_returns_own_quests(client, spirit, player):
    pid, token = player
    _summon(client, token, spirit)

    other_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    _summon(client, other_body["session_token"], spirit)

    body = _list_quests(client, token).json()
    assert len(body["quests"]) == 1
