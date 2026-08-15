"""
`GET /api/v1/spirits/{placeId}` 的執行期行為。

這支端點原本**沒有專屬測試檔**，只被 summon／sense 的測試間接涵蓋。issue #30
順手補上回歸保護——契約說它會回什麼是一回事，它實際回什麼是另一回事，兩者都要
有東西守著。
"""
import pytest

from app.modules.body import models

_LAT = 25.0955
_LON = 121.5186


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
        bearing_deg=137.5,
        height_offset_m=2.25,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def spirit_without_orientation(db_session, unique_spirit_id):
    """不指定方位，驗證 server_default 讓既有資料列拿到合理的 0。"""
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="沒設定方位的地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


def test_returns_spirit_basics(client, spirit):
    resp = client.get(f"/api/v1/spirits/{spirit.spirit_id}")

    assert resp.status_code == 200
    body = resp.json()
    # 對外欄位名跟 DB 欄位名刻意脫鉤，這裡釘住的是**對外**那組。
    assert body["place_id"] == spirit.spirit_id
    assert body["name"] == "測試地標"
    assert body["summon_radius_m"] == 50
    assert body["sense_radius_m"] == 150


def test_returns_orientation_from_database(client, spirit):
    """方位值來自資料庫，不是寫死的預設。"""
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert body["orientation"] == {"bearing_deg": 137.5, "height_offset_m": 2.25}


def test_orientation_defaults_to_zero(client, spirit_without_orientation):
    """
    未設定時回傳 0，不是 null。

    客戶端的 `OrientationService` 拿到 null 就得自己決定要 fallback 成什麼，
    那等於把契約的責任推給每個消費端各猜一次。0° 即正北、無垂直偏移，是明確
    且合理的預設。
    """
    body = client.get(f"/api/v1/spirits/{spirit_without_orientation.spirit_id}").json()

    assert body["orientation"] == {"bearing_deg": 0.0, "height_offset_m": 0.0}


def test_unknown_spirit_returns_404(client):
    assert client.get("/api/v1/spirits/does-not-exist").status_code == 404


def test_inactive_spirit_returns_404(client, db_session, spirit):
    """
    下架的靈魂是 404，跟不存在的一視同仁。

    這條對齊 /sense、/summon、/dialogue 的既有行為——對玩家來說，下架的靈魂
    跟不存在的靈魂沒有差別，不需要區分成兩種錯誤讓人推敲。

    這支端點原本漏了 `is_active` 檢查，是整個 repo 裡唯一會把下架靈魂回給玩家
    的地方。寫這次的契約測試時才發現，已一併修掉。
    """
    spirit.is_active = False
    db_session.commit()

    assert client.get(f"/api/v1/spirits/{spirit.spirit_id}").status_code == 404


# ── GET /api/v1/spirits（清單）──────────────────────────────────────────

def _entry(body, spirit_id):
    """清單裡的那一筆，找不到回 None。"""
    return next((item for item in body if item["place_id"] == spirit_id), None)


def test_list_includes_active_spirit(client, spirit):
    resp = client.get("/api/v1/spirits")

    assert resp.status_code == 200
    entry = _entry(resp.json(), spirit.spirit_id)
    assert entry is not None
    # 地圖畫 pin 只需要這幾個欄位，全部都要在——少一個，客戶端就得再打一次
    # 單筆端點，九個召喚點等於九次往返。
    assert entry["name"] == "測試地標"
    assert entry["latitude"] == pytest.approx(_LAT)
    assert entry["longitude"] == pytest.approx(_LON)
    assert entry["summon_radius_m"] == 50
    assert entry["sense_radius_m"] == 150


def test_list_omits_inactive_spirits(client, db_session, spirit):
    """
    下架的靈魂不進清單，跟 `GET /spirits/{placeId}` 回 404 是同一個判斷。

    畫成 pin 等於邀請玩家走過去撞一個 404——那是最糟的一種失敗，因為玩家已經
    走到現場了才知道沒有東西。

    ⚠️ `/dev/spirits` 刻意相反（下架的也回，帶 `is_active`），因為測試的人需要
    看得到「為什麼這個地標打 404」。兩支的讀者不同，不要把它們對齊。
    """
    spirit.is_active = False
    db_session.commit()

    assert _entry(client.get("/api/v1/spirits").json(), spirit.spirit_id) is None


def test_list_is_sorted_by_id(client, spirit):
    body = client.get("/api/v1/spirits").json()

    ids = [item["place_id"] for item in body]
    assert ids == sorted(ids)


def test_list_needs_no_token(client, spirit):
    """
    清單不帶 session token 也拿得到。

    它就是一份公開的地標座標表，跟站在路口看見廟在哪個方向是同一件事。要求登入
    才能看地圖，等於在玩家還沒有理由註冊之前先擋住他。
    """
    assert client.get("/api/v1/spirits").status_code == 200
