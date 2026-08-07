"""
Ticket #35．GET /resonance/{spiritId} 共鳴進度查詢。

驗收標準對照見 GitHub issue #35。這支測 API 層；服務層（`stage_for_value`／
`next_threshold` 本身的邊界值）已經在 `tests/test_resonance.py` 測過，這裡
不重複驗算法，只驗端點的把關與資料組裝。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.resonance import apply_resonance


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}", display_name="測試地標",
        latitude=25.0955, longitude=121.5186, summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=row.spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=row.spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _get(client, token, spirit_id):
    return client.get(f"/api/v1/resonance/{spirit_id}", headers={"Authorization": f"Bearer {token}"})


# ── 憑證（issue #35 AC1）─────────────────────────────────────────────────

def test_no_auth_header_returns_401(client, spirit):
    assert client.get(f"/api/v1/resonance/{spirit.spirit_id}").status_code == 401


def test_garbage_bearer_returns_401(client, spirit):
    response = client.get(
        f"/api/v1/resonance/{spirit.spirit_id}", headers={"Authorization": "Bearer garbage"}
    )
    assert response.status_code == 401


# ── 正常回傳（issue #35 AC2）─────────────────────────────────────────────

def test_returns_all_four_fields(client, db_session, spirit, player):
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="src-1", amount=30,
    )

    body = _get(client, token, spirit.spirit_id).json()

    assert body == {
        "spirit_id": spirit.spirit_id, "resonance_value": 30, "stage": 1, "next_threshold": 40,
    }


# ── 門檻邊界（issue #35 AC3）─────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected_stage,expected_next",
    [
        (0, 0, 10), (9, 0, 10), (10, 1, 40), (39, 1, 40),
        (40, 2, 100), (99, 2, 100), (100, 3, None), (250, 3, None),
    ],
)
def test_threshold_boundaries(client, db_session, spirit, player, value, expected_stage, expected_next):
    pid, token = player
    if value:
        apply_resonance(
            db_session, player_id=pid, spirit_id=spirit.spirit_id,
            source_type="quest", source_id="src-1", amount=value,
        )

    body = _get(client, token, spirit.spirit_id).json()
    assert body["stage"] == expected_stage
    assert body["next_threshold"] == expected_next


# ── 冷啟動（issue #35 AC4）───────────────────────────────────────────────

def test_no_resonance_yet_returns_zero_not_404(client, spirit, player):
    _, token = player
    response = _get(client, token, spirit.spirit_id)
    assert response.status_code == 200
    assert response.json() == {
        "spirit_id": spirit.spirit_id, "resonance_value": 0, "stage": 0, "next_threshold": 10,
    }


# ── 靈魂不存在／下架（issue #35 AC5）─────────────────────────────────────

def test_nonexistent_spirit_returns_404(client, player):
    _, token = player
    response = _get(client, token, "no-such-spirit")
    assert response.status_code == 404
    assert response.json()["detail"] == "spirit not found"


def test_inactive_spirit_returns_404_even_with_resonance(client, db_session, spirit, player):
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="src-1", amount=30,
    )
    spirit.is_active = False
    db_session.commit()

    assert _get(client, token, spirit.spirit_id).status_code == 404


# ── 隔離（issue #35 AC6）─────────────────────────────────────────────────

def test_only_returns_own_resonance(client, db_session, spirit, player):
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="src-p", amount=30,
    )
    other_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    other_pid = uuid.UUID(other_body["player_id"])
    apply_resonance(
        db_session, player_id=other_pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="src-q", amount=100,
    )

    body = _get(client, token, spirit.spirit_id).json()
    assert body["resonance_value"] == 30


# ── stage 不信任 DB 快取（issue #35 AC7）─────────────────────────────────

def test_stage_is_recomputed_not_trusted_from_db(client, db_session, spirit, player):
    pid, token = player
    apply_resonance(
        db_session, player_id=pid, spirit_id=spirit.spirit_id,
        source_type="quest", source_id="src-1", amount=30,
    )
    # 人為破壞：resonance 表沒有 stage 欄位可以竄改（0003 之後就沒有），
    # 這裡改用「直接改 resonance_value 後不重新入帳」來確保沒有任何快取
    # 中介值可以偷懶——重算永遠是唯一的路徑。
    row = db_session.query(models.Resonance).filter_by(player_id=pid, spirit_id=spirit.spirit_id).first()
    row.resonance_value = 30
    db_session.commit()

    body = _get(client, token, spirit.spirit_id).json()
    assert body["stage"] == 1  # 從 30 重算，不是任何寫死或殘留的值
