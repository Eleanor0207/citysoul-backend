"""
`GET /api/v1/spirits/{placeId}` 專屬測試（#30 user story 19）。

之前只被 `test_sense.py`／`test_summon.py` 間接涵蓋，這支路由本身沒有自己的
回歸保護。這裡補上 404 與方位設定（bearing_deg／height_offset_m，SDD v2.1
§10.2）的直接測試。
"""
from app.modules.body import models


def test_unknown_spirit_returns_404(client):
    resp = client.get("/api/v1/spirits/no-such-spirit")
    assert resp.status_code == 404


def test_inactive_spirit_is_still_returned_with_is_active_false(client, db_session, unique_spirit_id):
    """
    跟 `/summon`／`/sense` 不同：這支查詢**不**把下架當成不存在。`is_active`
    欄位存在的理由就是讓探索地圖（S7）能畫出「已下架、不能召喚」的狀態，
    如果下架也回 404，這個欄位永遠不會被觀察到 False，等於白放。
    """
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=25.0,
        longitude=121.5,
        is_active=False,
    )
    db_session.add(row)
    db_session.commit()

    try:
        resp = client.get(f"/api/v1/spirits/{unique_spirit_id}")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False
    finally:
        db_session.delete(row)
        db_session.commit()


def test_orientation_defaults_to_zero(client, db_session, unique_spirit_id):
    """
    龍山寺 seed row（以及既有的 migration 0006 之前建的 spirit）沒有特別設定
    方位，`bearing_deg`／`height_offset_m` 的預設值 0 要正確反映在回應裡。
    """
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=25.0,
        longitude=121.5,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    try:
        body = client.get(f"/api/v1/spirits/{unique_spirit_id}").json()
        assert body["orientation"] == {"bearing_deg": 0.0, "height_offset_m": 0.0}
    finally:
        db_session.delete(row)
        db_session.commit()


def test_orientation_reflects_configured_values(client, db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=25.0,
        longitude=121.5,
        is_active=True,
        bearing_deg=45.5,
        height_offset_m=1.2,
    )
    db_session.add(row)
    db_session.commit()

    try:
        body = client.get(f"/api/v1/spirits/{unique_spirit_id}").json()
        assert body["orientation"] == {"bearing_deg": 45.5, "height_offset_m": 1.2}
    finally:
        db_session.delete(row)
        db_session.commit()
