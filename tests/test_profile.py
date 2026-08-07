"""
Ticket #36．GET /profile 玩家彙總查詢（v2.1 §10.3 漏列，補回）。

驗收標準對照見 GitHub issue #36。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.resonance import apply_resonance


@pytest.fixture
def spirits(db_session):
    rows = [
        models.Spirit(
            spirit_id=f"test-spirit-a-{uuid.uuid4()}", display_name="A",
            latitude=25.0, longitude=121.5, summon_radius_meters=50, is_active=True,
        ),
        models.Spirit(
            spirit_id=f"test-spirit-b-{uuid.uuid4()}", display_name="B",
            latitude=25.0, longitude=121.5, summon_radius_meters=50, is_active=True,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    yield rows
    for row in rows:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=row.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=row.spirit_id).delete()
    db_session.commit()
    for row in rows:
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


def _summon(client, token, spirit):
    return client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit.spirit_id, "latitude": spirit.latitude, "longitude": spirit.longitude},
        headers={"Authorization": f"Bearer {token}"},
    )


def _profile(client, token):
    return client.get("/api/v1/profile", headers={"Authorization": f"Bearer {token}"})


# ── 憑證（issue #36 AC1）─────────────────────────────────────────────────

def test_no_auth_header_returns_401(client):
    assert client.get("/api/v1/profile").status_code == 401


def test_garbage_bearer_returns_401(client):
    assert client.get("/api/v1/profile", headers={"Authorization": "Bearer garbage"}).status_code == 401


# ── 彙總（issue #36 AC2）─────────────────────────────────────────────────

def test_returns_all_quests_and_all_resonance(client, db_session, spirits, player):
    spirit_a, spirit_b = spirits
    pid, token = player
    _summon(client, token, spirit_a)
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit_a.spirit_id,
        source_type="quest", source_id="src-a", amount=30,
    )
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit_b.spirit_id,
        source_type="quest", source_id="src-b", amount=100,
    )

    body = _profile(client, token).json()

    assert len(body["quests"]) == 1
    assert body["quests"][0]["spirit_id"] == spirit_a.spirit_id

    resonance_by_spirit = {r["spirit_id"]: r for r in body["resonance"]}
    assert resonance_by_spirit[spirit_a.spirit_id] == {
        "spirit_id": spirit_a.spirit_id, "resonance_value": 30, "stage": 1,
    }
    assert resonance_by_spirit[spirit_b.spirit_id] == {
        "spirit_id": spirit_b.spirit_id, "resonance_value": 100, "stage": 3,
    }


# ── 冷啟動（issue #36 AC3）───────────────────────────────────────────────

def test_new_player_returns_two_empty_arrays(client, player):
    _, token = player
    body = _profile(client, token).json()
    assert body == {"quests": [], "resonance": []}


# ── 與 /resonance/{spiritId} 一致（issue #36 AC4）───────────────────────

def test_stage_matches_the_resonance_endpoint(client, db_session, spirits, player):
    spirit_a, _ = spirits
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit_a.spirit_id,
        source_type="quest", source_id="src-a", amount=40,
    )

    profile_stage = _profile(client, token).json()["resonance"][0]["stage"]
    resonance_stage = client.get(
        f"/api/v1/resonance/{spirit_a.spirit_id}", headers={"Authorization": f"Bearer {token}"}
    ).json()["stage"]

    assert profile_stage == resonance_stage == 2


# ── 隔離（issue #36 AC5）─────────────────────────────────────────────────

def test_only_returns_own_data(client, db_session, spirits, player):
    spirit_a, _ = spirits
    pid, token = player
    _summon(client, token, spirit_a)
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit_a.spirit_id,
        source_type="quest", source_id="src-p", amount=30,
    )

    other_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    other_pid = uuid.UUID(other_body["player_id"])
    _summon(client, other_body["session_token"], spirit_a)
    apply_resonance(
        db_session, player_id=other_pid, spirit_id=spirit_a.spirit_id,
        source_type="quest", source_id="src-q", amount=100,
    )

    body = _profile(client, token).json()
    assert len(body["quests"]) == 1
    assert len(body["resonance"]) == 1
    assert body["resonance"][0]["resonance_value"] == 30

    db_session.query(models.QuestProgress).filter_by(player_id=other_pid).delete()
    db_session.commit()


# ── 不呼叫腦袋（issue #36 AC6）───────────────────────────────────────────

def test_does_not_call_any_brain_module(client, db_session, spirits, player, monkeypatch):
    """
    純身體自己的表直查——用 monkeypatch 把 GeminiClient 換成一呼叫就炸的
    版本，證明這支端點的程式碼路徑上根本沒有機會碰到它。
    """
    from app.modules.brain.gemini import VertexAIGeminiClient

    def _boom(self, prompt):
        raise AssertionError("GET /profile 不該呼叫任何腦袋模組")

    monkeypatch.setattr(VertexAIGeminiClient, "generate", _boom)

    spirit_a, _ = spirits
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit_a.spirit_id,
        source_type="quest", source_id="src-a", amount=10,
    )

    assert _profile(client, token).status_code == 200
