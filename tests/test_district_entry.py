"""Administrative district geofence entry and one-time arc item grant."""

import uuid
from concurrent.futures import ThreadPoolExecutor

from app.core.database import SessionLocal
from app.modules.body import models
from app.modules.body.districts import check_player_in_district
from app.modules.body.router import _grant_district_entry_item


_INSIDE = {"latitude": 25.0375, "longitude": 121.4998}
_OUTSIDE = {"latitude": 25.0, "longitude": 121.55}
_BOUNDARY = {"latitude": 25.0482879, "longitude": 121.5089926}


def _new_player(client):
    response = client.post(
        "/api/v1/players", json={"device_id": f"district-test-{uuid.uuid4()}"}
    )
    body = response.json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def test_check_player_in_district_inside(db_session):
    assert check_player_in_district(db_session, **_INSIDE) == "wanhua"


def test_check_player_in_district_outside_returns_none(db_session):
    assert check_player_in_district(db_session, **_OUTSIDE) is None


def test_check_player_in_district_on_boundary_returns_none(db_session):
    assert check_player_in_district(db_session, **_BOUNDARY) is None


def test_check_entry_returns_200_and_grants_wanhua_letter_once(client, db_session):
    player_id, token = _new_player(client)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post("/api/v1/districts/check-entry", json=_INSIDE, headers=headers)
    second = client.post("/api/v1/districts/check-entry", json=_INSIDE, headers=headers)

    assert first.status_code == 200
    assert first.json() == {
        "district_id": "wanhua",
        "entry_granted": True,
        "arc_id": None,
    }
    assert second.status_code == 200
    assert second.json() == {
        "district_id": "wanhua",
        "entry_granted": False,
        "arc_id": None,
    }
    assert (
        db_session.query(models.PlayerInventory)
        .filter_by(player_id=player_id, item_id="item_wanhua_letter")
        .count()
        == 1
    )


def test_check_entry_outside_is_a_success_with_null_district(client):
    _, token = _new_player(client)

    response = client.post(
        "/api/v1/districts/check-entry",
        json=_OUTSIDE,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "district_id": None,
        "entry_granted": False,
        "arc_id": None,
    }


def test_concurrent_entry_grants_insert_at_most_one_inventory_item(db_session, client):
    player_id, _ = _new_player(client)

    def attempt():
        session = SessionLocal()
        try:
            return _grant_district_entry_item(
                session, player_id=player_id, district_id="wanhua"
            )
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))

    assert sorted(results) == [False, True]
    assert (
        db_session.query(models.PlayerInventory)
        .filter_by(player_id=player_id, item_id="item_wanhua_letter")
        .count()
        == 1
    )
