"""
Ticket #7．`/summon` 在場驗證與召喚半徑（S2）。

驗收標準對照見 GitHub issue #7。
"""
import math

import jwt
import pytest

from app.core.config import settings
from app.modules.body import models
from app.modules.body.geo import EARTH_RADIUS_M, haversine_distance_m

# 天文館附近的座標，跟 seed data 無關（測試自己建 spirit，避免污染）。
_SPIRIT_LAT = 25.0955
_SPIRIT_LON = 121.5186
_RADIUS_M = 50


def _offset_north(lat: float, meters: float) -> float:
    """沿經線往北推 `meters` 公尺後的緯度（同經度時 haversine 距離剛好是 meters）。"""
    return lat + math.degrees(meters / EARTH_RADIUS_M)


@pytest.fixture
def spirit(db_session, unique_spirit_id, purge_spirit_child_rows):
    """建立一個測試專用的啟用中 spirit，測試結束後刪掉。"""
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_SPIRIT_LAT,
        longitude=_SPIRIT_LON,
        summon_radius_meters=_RADIUS_M,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    # 🔴 召喚會寫 resonance／resonance_events，兩者都對 spirits 有外鍵
    # （2026-08-19 起）。少了這一行，**這個檔案裡每一條成功召喚的測試**都會在
    # teardown 撞 ForeignKeyViolation——測試本身是綠的，錯誤只出現在拆卸階段。
    purge_spirit_child_rows(row.spirit_id)
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def session_token(client, unique_device_id):
    return client.post("/api/v1/players", json={"device_id": unique_device_id}).json()["session_token"]


def _summon(client, token, spirit_id, lat, lon, **extra):
    body = {"spirit_id": spirit_id, "latitude": lat, "longitude": lon, **extra}
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/api/v1/summon", json=body, headers=headers)


# ── 401：Session Token 驗證 ────────────────────────────────────────────

def test_missing_session_token_returns_401(client, spirit):
    resp = _summon(client, None, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_garbage_session_token_returns_401(client, spirit):
    resp = _summon(client, "not-a-jwt", spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_token_signed_with_encounter_secret_is_rejected_as_session(client, spirit):
    """
    拿 encounter token 的金鑰簽一張 purpose=session 的 token 也不能過。
    這正是「兩種 token 金鑰分開管理、驗證邏輯不共用」的行為證據——
    如果哪天有人把兩把金鑰改成同一把，這個測試會紅。
    """
    forged = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "purpose": "session"},
        settings.encounter_token_secret,
        algorithm="HS256",
    )
    resp = _summon(client, forged, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


def test_encounter_purpose_token_cannot_be_used_as_session(client, spirit):
    """即使用對的 session 金鑰簽，purpose 不是 session 也擋下來。"""
    forged = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "purpose": "encounter"},
        settings.session_token_secret,
        algorithm="HS256",
    )
    resp = _summon(client, forged, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 401


# ── 在場判定 ───────────────────────────────────────────────────────────

def test_inside_radius_issues_encounter_token(client, session_token, spirit):
    resp = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

    assert resp.status_code == 200
    body = resp.json()
    assert body["spirit_id"] == spirit.spirit_id
    assert body["encounter_token"]


def test_just_inside_radius_boundary_passes(client, session_token, spirit):
    """
    距離 = 半徑 - 1mm，確認半徑判定沒有被實作成偏保守（例如少算一段緩衝）。

    注意這一對測試（±1mm）證明的是「半徑切在正確的位置」，**不是**
    `<=` 與 `<` 的差別——用經緯度構造出「距離恰好等於半徑」的座標在浮點數
    上做不到（實測構造 50m 會得到 50.00000000012554）。真正含邊界值的
    `<=` 由下面的 test_distance_exactly_equal_to_radius_passes 驗證。
    """
    lat = _offset_north(_SPIRIT_LAT, _RADIUS_M - 0.001)
    resp = _summon(client, session_token, spirit.spirit_id, lat, _SPIRIT_LON)
    assert resp.status_code == 200


def test_just_outside_radius_returns_403(client, session_token, spirit):
    lat = _offset_north(_SPIRIT_LAT, _RADIUS_M + 0.001)
    resp = _summon(client, session_token, spirit.spirit_id, lat, _SPIRIT_LON)
    assert resp.status_code == 403
    assert "encounter_token" not in resp.json()


def test_distance_exactly_equal_to_radius_passes(client, session_token, spirit, db_session):
    """
    驗收標準的「含邊界值」：distance == summon_radius_meters 要判定為在場成立。

    唯一能讓浮點距離精確等於半徑的情境，是把兩者都放在 0——半徑設 0、
    玩家站在地標正中心，haversine 回傳的是精確的 0.0。實作寫成 `<` 的話
    這個測試會回 403 而紅掉，寫成 `<=` 才會過。
    """
    spirit.summon_radius_meters = 0
    db_session.commit()

    resp = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 200

    # 同樣半徑 0，往外 1mm 就該被擋下（確認上面不是因為半徑 0 被特殊處理才過）
    lat = _offset_north(_SPIRIT_LAT, 0.001)
    assert _summon(client, session_token, spirit.spirit_id, lat, _SPIRIT_LON).status_code == 403


def test_far_away_returns_403(client, session_token, spirit):
    """台北車站附近（離天文館約 5 公里）。"""
    resp = _summon(client, session_token, spirit.spirit_id, 25.0478, 121.5170)
    assert resp.status_code == 403


# ── 404：spirit 不存在 / 已下架 ────────────────────────────────────────

def test_unknown_spirit_returns_404(client, session_token):
    resp = _summon(client, session_token, "no-such-spirit", _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 404


def test_inactive_spirit_returns_404(client, session_token, spirit, db_session):
    spirit.is_active = False
    db_session.commit()

    # 站在正中心也一樣 404——下架優先於在場判定。
    resp = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
    assert resp.status_code == 404


# ── Encounter Token 本身 ──────────────────────────────────────────────

def test_encounter_token_claims_and_lifetime(client, session_token, spirit):
    body = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON).json()

    decoded = jwt.decode(
        body["encounter_token"], settings.encounter_token_secret, algorithms=["HS256"]
    )

    assert decoded["purpose"] == "encounter"
    assert decoded["spirit_id"] == spirit.spirit_id
    assert decoded["exp"] - decoded["iat"] == 900


def test_encounter_token_sub_is_the_calling_player(client, unique_device_id, spirit):
    player = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    body = _summon(
        client, player["session_token"], spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON
    ).json()

    decoded = jwt.decode(
        body["encounter_token"], settings.encounter_token_secret, algorithms=["HS256"]
    )
    assert decoded["sub"] == player["player_id"]


def test_encounter_token_contains_no_gps_coordinates(client, session_token, spirit):
    """SDD 第6節表格：Encounter Token「內含GPS座標：否」。"""
    body = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON).json()
    decoded = jwt.decode(
        body["encounter_token"], settings.encounter_token_secret, algorithms=["HS256"]
    )

    assert set(decoded) == {"sub", "spirit_id", "purpose", "iat", "exp"}


def test_encounter_token_not_verifiable_with_session_secret(client, session_token, spirit):
    """
    相遇憑證不能用 session 金鑰驗過——兩套簽章金鑰確實分開。
    """
    body = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON).json()

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(body["encounter_token"], settings.session_token_secret, algorithms=["HS256"])


def test_all_three_secrets_are_actually_different():
    """
    守門測試：金鑰若有任何兩把相同，上面那些「不可互相冒充」的測試就失去意義
    ——它們會照樣通過，因為驗簽本來就會過。

    三把兩兩比對，不是只比 session 與 encounter。`sense_token_secret` 是 #18
    才加的第三把，當時沒有跟著擴這個測試；只要有兩把一樣，SDD 第6節那套
    「90 天的 session token 不能拿來冒充 15 分鐘的相遇憑證」就形同虛設。
    """
    secrets = {
        "session": settings.session_token_secret,
        "encounter": settings.encounter_token_secret,
        "sense": settings.sense_token_secret,
    }

    assert len(set(secrets.values())) == 3, f"有金鑰重複：{secrets}"


# ── haversine 純函式 ──────────────────────────────────────────────────

def test_haversine_zero_distance():
    assert haversine_distance_m(_SPIRIT_LAT, _SPIRIT_LON, _SPIRIT_LAT, _SPIRIT_LON) == 0


def test_haversine_known_distance():
    """
    台北101 → 台北車站。5028.7m 這個參考值是先前在 mobile_app 的 Dart 版
    haversine 測試中，用獨立的 Python 實作交叉驗算過的（見
    mobile_app/test/haversine_test.dart 的註解）。兩邊用同一個參考值，
    順便確保前後端算出來的距離一致。
    """
    d = haversine_distance_m(25.0339, 121.5645, 25.0478, 121.5170)
    assert d == pytest.approx(5028.7, abs=1.0)


def test_haversine_matches_constructed_offset():
    """沿經線推 N 公尺，算回來就該是 N 公尺。"""
    lat = _offset_north(_SPIRIT_LAT, 50)
    d = haversine_distance_m(_SPIRIT_LAT, _SPIRIT_LON, lat, _SPIRIT_LON)
    assert d == pytest.approx(50, abs=0.001)


# ── 🆕 每日共鳴 +10（A.L. 2026-08-19 定案的新規則）─────────────────────
#
# 這一段測的是「後端真的接上了」，不是入帳邏輯本身——後者在
# `test_resonance.py` 有完整覆蓋。這裡只走端到端那條路。


def test_summon_awards_daily_resonance(client, spirit, session_token, db_session, purge_spirit_child_rows):
    """走到現場召喚 → +10、stage 1。"""
    try:
        body = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON).json()

        assert body["resonance_awarded"] is True
        assert body["resonance_value"] == 10
        assert body["stage"] == 1
        assert body["newly_unlocked_stages"] == [1]
    finally:
        purge_spirit_child_rows(spirit.spirit_id)


def test_second_summon_same_day_does_not_award(client, spirit, session_token, db_session, purge_spirit_child_rows):
    """
    同一天第二次召喚不加分，但**仍然是 200**。

    ⚠️ `resonance_awarded: false` 不是錯誤，客戶端要靠它決定要不要播加分動畫。
    這條紅了通常代表有人把「已領過」當成 4xx 回了。
    """
    try:
        _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)
        resp = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON)

        assert resp.status_code == 200
        body = resp.json()
        assert body["resonance_awarded"] is False
        assert body["resonance_value"] == 10
        assert body["newly_unlocked_stages"] == []
    finally:
        purge_spirit_child_rows(spirit.spirit_id)


def test_failed_summon_awards_nothing(client, spirit, session_token, db_session, purge_spirit_child_rows):
    """
    🔒 不在半徑內 → 403，**而且沒有留下任何入帳**。

    入帳刻意排在在場驗證之後。這條守的是那個順序：如果哪天有人把
    `award_daily_encounter` 移到距離判定前面，玩家在家就能刷共鳴值，
    而 403 的回應本身看起來完全正常。
    """
    far_lat = _offset_north(_SPIRIT_LAT, _RADIUS_M + 100)
    try:
        resp = _summon(client, session_token, spirit.spirit_id, far_lat, _SPIRIT_LON)
        assert resp.status_code == 403

        assert (
            db_session.query(models.ResonanceEvent)
            .filter_by(spirit_id=spirit.spirit_id)
            .count()
            == 0
        )
    finally:
        purge_spirit_child_rows(spirit.spirit_id)


def test_summon_response_keeps_quest_field(client, spirit, session_token, db_session, purge_spirit_child_rows):
    """
    🔒 新欄位是**加法**，`quest` 沒有被拿掉。

    客戶端 `TokenManager.cs` 在讀這個欄位；共鳴值改制不該讓它變成 null。
    """
    try:
        body = _summon(client, session_token, spirit.spirit_id, _SPIRIT_LAT, _SPIRIT_LON).json()

        assert body["quest"] is not None
        assert body["quest"]["quest_id"] == f"{spirit.spirit_id}:daily"
    finally:
        purge_spirit_child_rows(spirit.spirit_id)
