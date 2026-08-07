"""
Ticket #30 AC（issue #30 Testing Decisions）：`GET /spirits/{placeId}` 目前
沒有專屬測試檔，只被間接涵蓋（`test_sense.py` 測了欄位脫鉤，但那不是這支
端點本身的回歸保護）。這裡補上，並涵蓋這次新增的 `orientation`（SDD v2.1
§10.2）執行期行為——契約層的形狀檢查在 `tests/test_api_contract.py`。
"""
import uuid

import pytest

from app.modules.body import models


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id, display_name="測試地標",
        latitude=25.0367, longitude=121.4998, summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


def test_returns_basic_fields(client, spirit):
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert body["place_id"] == spirit.spirit_id
    assert body["name"] == "測試地標"
    assert body["latitude"] == pytest.approx(25.0367)
    assert body["longitude"] == pytest.approx(121.4998)
    assert body["summon_radius_m"] == 50
    assert body["is_active"] is True


def test_nonexistent_spirit_returns_404(client):
    response = client.get(f"/api/v1/spirits/no-such-spirit-{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "spirit not found"}


def test_inactive_spirit_returns_404(client, db_session, spirit):
    spirit.is_active = False
    db_session.commit()
    assert client.get(f"/api/v1/spirits/{spirit.spirit_id}").status_code == 404


# ── orientation（issue #30，SDD v2.1 §10.2）───────────────────────────────

def test_orientation_defaults_to_zero_when_unset(client, spirit):
    """新建的靈魂沒有特別設定過方位——預設 0，不是缺欄位或 null。"""
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert body["orientation"] == {"bearing_deg": 0.0, "height_offset_m": 0.0}


def test_orientation_reflects_configured_values(client, db_session, spirit):
    spirit.bearing_deg = 137.5
    spirit.height_offset_m = 1.8
    db_session.commit()

    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert body["orientation"] == {"bearing_deg": 137.5, "height_offset_m": 1.8}
