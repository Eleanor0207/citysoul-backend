"""
Ticket #18．`/sense` 感應範圍驗證與 Sense Token（S2-new）。

驗收標準對照見 GitHub issue #18。風格比照 `tests/test_summon.py`：
測試自己建 spirit（用 `unique_spirit_id`），不依賴也不污染 seed data。
"""
import math
import uuid

import jwt
import pytest

from app.core.config import settings
from app.modules.body import models
from app.modules.body.geo import EARTH_RADIUS_M
from app.modules.body.sense_tokens import SENSE_TOKEN_EXPIRE_SECONDS, issue_sense_token

# 龍山寺附近的座標。跟 seed data 無關——測試自己建 spirit。
_SPIRIT_LAT = 25.0372
_SPIRIT_LON = 121.4998
_SENSE_RADIUS_M = 150
_SUMMON_RADIUS_M = 50


def _offset_north(lat: float, meters: float) -> float:
    """沿經線往北推 `meters` 公尺後的緯度（同經度時 haversine 距離剛好是 meters）。"""
    return lat + math.degrees(meters / EARTH_RADIUS_M)


def _make_spirit(db_session, spirit_id, *, sense_radius_meters=_SENSE_RADIUS_M, is_active=True):
    row = models.Spirit(
        spirit_id=spirit_id,
        display_name="測試地標",
        latitude=_SPIRIT_LAT,
        longitude=_SPIRIT_LON,
        summon_radius_meters=_SUMMON_RADIUS_M,
        sense_radius_meters=sense_radius_meters,
        is_active=is_active,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = _make_spirit(db_session, unique_spirit_id)
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client, unique_device_id):
    """回傳 (player_id, session_token)。"""
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _sense(client, token, spirit_id, lat, lon):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        "/api/v1/sense",
        json={"spirit_id": spirit_id, "latitude": lat, "longitude": lon},
        headers=headers,
    )


def _decode(token):
    return jwt.decode(token, settings.sense_token_secret, algorithms=["HS256"])


# ── sense_radius_meters 欄位 ────────────────────────────────────────────────

def test_spirit_has_sense_radius_defaulting_to_150(db_session, unique_spirit_id):
    """
    沒有明講 sense_radius_meters 時要是 150，不是 NULL 也不是 0。

    0 特別危險：那會讓「玩家沒站在正中心就感應不到」變成靜默的行為，
    而不是一個看得出來的錯誤。
    """
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_SPIRIT_LAT,
        longitude=_SPIRIT_LON,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    try:
        assert row.sense_radius_meters == 150
    finally:
        db_session.delete(row)
        db_session.commit()


def test_get_spirit_exposes_sense_radius(client, spirit):
    """
    客戶端要靠這個欄位畫三段式標記（S7）。沒有它，客戶端只能把 150 寫死，
    之後調整半徑就得同時改後端與發版客戶端。
    """
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert body["sense_radius_m"] == _SENSE_RADIUS_M
    assert body["summon_radius_m"] == _SUMMON_RADIUS_M


def test_get_spirit_wire_contract_is_independent_of_column_names(client, spirit):
    """
    對外的 JSON key 跟資料庫欄位名脫鉤，而且**必須維持舊名字**。

    0002 把 DB 欄位改成 `spirit_id` / `display_name` / `*_radius_meters`，
    但 Unity client 的 `SpiritDto` 用 `[JsonProperty("place_id")]` 這類屬性
    寫死了 JSON key——後端單方面改名的話，client 拿到的會是 null，而且不會有
    任何錯誤，只是地圖上的靈魂沒有名字。

    這個測試存在的意義是：下次有人「順手」把 schemas 的欄位名也改成跟 DB 一致
    的時候，它要變紅。要改的話，client 的 DTO 必須同一次一起改。
    """
    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}").json()

    assert set(body) == {
        "place_id",
        "name",
        "latitude",
        "longitude",
        "summon_radius_m",
        "sense_radius_m",
        "is_active",
    }
    assert body["place_id"] == spirit.spirit_id
    assert body["name"] == spirit.display_name


def test_sense_radius_is_wider_than_summon_radius(spirit):
    """
    兩個半徑是兩段不同的體驗，不是同一個值。感應範圍必須比召喚範圍大——
    反過來的話「先看到發光、走近才能召喚」這個核心流程根本走不通。
    """
    assert spirit.sense_radius_meters > spirit.summon_radius_meters


# ── 401：Session Token 驗證 ────────────────────────────────────────────

def test_missing_session_token_returns_401(client, spirit):
    resp = _sense(client, None, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_garbage_session_token_returns_401(client, spirit):
    resp = _sense(client, "not-a-jwt", spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_token_signed_with_encounter_secret_is_rejected_as_session(client, spirit):
    """
    用 encounter 金鑰簽一張 purpose="sense" 的 token 冒充 session，必須失敗。
    這證明金鑰確實分開——哪天有人把它們設成同一把，這個測試會紅。
    """
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "purpose": "sense"},
        settings.encounter_token_secret,
        algorithm="HS256",
    )
    resp = _sense(client, forged, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_token_signed_with_sense_secret_cannot_pose_as_session(client, spirit):
    """反向：用 sense 金鑰簽一張 purpose="session" 的 token 去呼叫 /sense。"""
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "purpose": "session"},
        settings.sense_token_secret,
        algorithm="HS256",
    )
    resp = _sense(client, forged, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


# ── 半徑判定 ──────────────────────────────────────────────────────────

def test_within_sense_radius_issues_token(client, spirit, player):
    _, token = player
    lat = _offset_north(_SPIRIT_LAT, 149)

    resp = _sense(client, token, spirit.spirit_id, lat, _SPIRIT_LON)

    assert resp.status_code == 200
    assert set(resp.json()) == {"sense_token", "spirit_id"}
    assert resp.json()["spirit_id"] == spirit.spirit_id


def test_beyond_sense_radius_returns_403_without_token(client, spirit, player):
    _, token = player
    lat = _offset_north(_SPIRIT_LAT, 151)

    resp = _sense(client, token, spirit.spirit_id, lat, _SPIRIT_LON)

    assert resp.status_code == 403
    assert "sense_token" not in resp.json()


def test_boundary_is_inclusive(client, db_session, unique_spirit_id, player):
    """
    `<=` 而不是 `<`。

    浮點數構造不出「距離恰好等於 150」——沿經線推 150 公尺算回來會是
    150.00000000037 之類的值，那證明不了任何邊界行為。半徑 0 ＋ 玩家站在
    正中心是唯一能讓距離**精確等於**半徑的方式（同 #7 的做法）。
    """
    _, token = player
    row = _make_spirit(db_session, unique_spirit_id, sense_radius_meters=0)

    try:
        resp = _sense(client, token, row.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
        assert resp.status_code == 200
    finally:
        db_session.delete(row)
        db_session.commit()


# ── 404 ──────────────────────────────────────────────────────────────

def test_unknown_spirit_returns_404(client, player):
    _, token = player
    resp = _sense(client, token, "no-such-spirit", _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 404


def test_inactive_spirit_returns_404_even_at_dead_centre(
    client, db_session, unique_spirit_id, player
):
    """下架優先於距離判定：站在正中心也是 404，不是 200。"""
    _, token = player
    row = _make_spirit(db_session, unique_spirit_id, is_active=False)

    try:
        resp = _sense(client, token, row.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
        assert resp.status_code == 404
    finally:
        db_session.delete(row)
        db_session.commit()


# ── token payload ────────────────────────────────────────────────────

def test_token_lifetime_is_exactly_1800_seconds(client, spirit, player):
    _, token = player
    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

    decoded = _decode(resp.json()["sense_token"])

    assert decoded["exp"] - decoded["iat"] == 1800
    assert SENSE_TOKEN_EXPIRE_SECONDS == 1800


def test_token_carries_purpose_and_spirit(client, spirit, player):
    player_id, token = player
    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

    decoded = _decode(resp.json()["sense_token"])

    assert decoded["purpose"] == "sense"
    assert decoded["spirit_id"] == spirit.spirit_id
    assert decoded["sub"] == str(player_id)


def test_token_payload_contains_no_gps_coordinates(client, spirit, player):
    """
    payload 的鍵名必須**恰為**這五個。多一個就算失敗。

    這條擋的是「順手把座標放進 token」——SDD 第6節表格明訂「內含GPS座標：否」。
    座標只在驗證期間使用，token 是驗證的結果，不該夾帶輸入。
    """
    _, token = player
    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

    decoded = _decode(resp.json()["sense_token"])

    assert set(decoded) == {"sub", "spirit_id", "purpose", "iat", "exp"}


# ── 三把金鑰互不冒充 ──────────────────────────────────────────────────

def test_three_secrets_are_all_distinct():
    """
    守門測試。三把金鑰只要有兩把相同，這個檔案裡所有「不可互相冒充」的
    測試就全部失去意義——它們會因為金鑰碰巧一樣而通過，而不是因為驗證邏輯正確。
    """
    secrets = {
        settings.session_token_secret,
        settings.encounter_token_secret,
        settings.sense_token_secret,
    }
    assert len(secrets) == 3


def test_sense_token_cannot_be_decoded_with_session_secret(client, spirit, player):
    _, token = player
    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    sense_token = resp.json()["sense_token"]

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(sense_token, settings.session_token_secret, algorithms=["HS256"])


def test_require_sense_token_rejects_encounter_purpose(spirit):
    """
    用 sense 金鑰簽一張 purpose="encounter" 的 token，仍然不能通過 sense 驗證。
    金鑰對了不代表用途對了——兩層檢查缺一不可。
    """
    from fastapi import HTTPException

    from app.modules.body.sense_tokens import require_sense_token

    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "spirit_id": spirit.spirit_id, "purpose": "encounter"},
        settings.sense_token_secret,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc:
        require_sense_token(spirit.spirit_id, forged)

    assert exc.value.status_code == 401


def test_require_sense_token_rejects_other_spirit(spirit):
    """憑證有效但不是給這個地標的 → 403，不是 401。"""
    from fastapi import HTTPException

    from app.modules.body.sense_tokens import require_sense_token

    token = issue_sense_token(uuid.uuid4(), "some-other-spirit")

    with pytest.raises(HTTPException) as exc:
        require_sense_token(spirit.spirit_id, token)

    assert exc.value.status_code == 403


# ── 與 /summon 的關鍵行為差異 ─────────────────────────────────────────

def _quest_row_count(db_session, player_id):
    return (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id)
        .count()
    )


def test_sense_does_not_touch_quest_progress(client, db_session, spirit, player):
    """
    這是 `/sense` 與 `/summon` 最關鍵的行為差異（SDD 第7.2節：不觸發任何任務判定）。

    沒有這條，`/sense` 很容易在某次重構中被「順手」加上任務判定——畢竟它跟
    `/summon` 只差幾行。對照組見下一個測試。
    """
    player_id, token = player
    assert _quest_row_count(db_session, player_id) == 0

    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 200

    db_session.expire_all()
    assert _quest_row_count(db_session, player_id) == 0


def test_summon_does_create_quest_progress_as_the_contrast(
    client, db_session, spirit, player
):
    """
    對照組。同一個玩家、同一個地標，改呼叫 `/summon` 就會長出一列 quest_progress。

    沒有這條的話，上面那個測試可能只是因為任務機制整個壞掉而通過。
    """
    player_id, token = player
    assert _quest_row_count(db_session, player_id) == 0

    resp = client.post(
        "/api/v1/summon",
        json={
            "spirit_id": spirit.spirit_id,
            "latitude": _SPIRIT_LAT,
            "longitude": _SPIRIT_LON,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    try:
        assert _quest_row_count(db_session, player_id) == 1
    finally:
        db_session.query(models.QuestProgress).filter_by(player_id=player_id).delete()
        db_session.commit()


def test_sense_does_not_issue_an_encounter_token(client, spirit, player):
    """感應憑證換不到在場證明。回應裡不該出現 encounter_token。"""
    _, token = player
    resp = _sense(client, token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

    assert "encounter_token" not in resp.json()
