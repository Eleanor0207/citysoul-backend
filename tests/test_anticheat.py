"""
Ticket #14．防作弊 mock location 觀察期（S3）。

驗收標準對照見 GitHub issue #14。

貫穿所有測試的一條線：**不管偵測到什麼，召喚都要照常成立**。所以幾乎每個
測試除了檢查 log，都會再斷言一次 HTTP 200 與 encounter_token 還在。
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.redis_client import redis_client
from app.modules.body import models
from app.modules.body.anticheat import (
    ANTICHEAT_LOGGER_NAME,
    EVENT_IMPLAUSIBLE_SPEED,
    EVENT_MOCK_LOCATION,
    MAX_PLAUSIBLE_SPEED_KMH,
    _last_summon_key,
    run_observation_checks,
)

# 天文館與（約 5 公里外的）台北車站，用來製造地標之間的位移。
_LAT_A, _LON_A = 25.0955, 121.5186
_LAT_B, _LON_B = 25.0478, 121.5170


@pytest.fixture(autouse=True)
def _capture_anticheat(caplog):
    """把 anticheat logger 的輸出接進 caplog。"""
    caplog.set_level(logging.WARNING, logger=ANTICHEAT_LOGGER_NAME)
    return caplog


def _anticheat_records(caplog, event: str | None = None):
    records = [r for r in caplog.records if hasattr(r, "anticheat_event")]
    if event is not None:
        records = [r for r in records if r.anticheat_event == event]
    return records


@pytest.fixture
def make_spirit(db_session, purge_spirit_child_rows):
    """建立測試用地標，測試結束一併清掉。"""
    created = []

    def _make(lat: float, lon: float, radius: int = 50) -> models.Spirit:
        row = models.Spirit(
            spirit_id=f"test-spirit-{uuid.uuid4()}",
            display_name="測試地標",
            latitude=lat,
            longitude=lon,
            summon_radius_meters=radius,
            is_active=True,
        )
        db_session.add(row)
        db_session.commit()
        created.append(row)
        return row

    yield _make

    for row in created:
        # 召喚會寫 resonance／resonance_events，兩者都對 spirits 有外鍵。
        purge_spirit_child_rows(row.spirit_id)
        db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    """回傳 (player_id, session_token)，並在結束後清掉 Redis 的 last_summon。"""
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    yield body["player_id"], body["session_token"]
    redis_client.delete(_last_summon_key(body["player_id"]))


def _summon(client, token, spirit, *, lat=None, lon=None, **extra):
    return client.post(
        "/api/v1/summon",
        json={
            "spirit_id": spirit.spirit_id,
            "latitude": lat if lat is not None else spirit.latitude,
            "longitude": lon if lon is not None else spirit.longitude,
            **extra,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


# ── mock location ─────────────────────────────────────────────────────

def test_mock_location_is_logged_and_summon_still_succeeds(client, player, make_spirit, caplog):
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    resp = _summon(client, token, spirit, is_mock_location=True, gps_accuracy_m=8.5)

    # 召喚照常成立——這是「觀察期不阻擋」的核心
    assert resp.status_code == 200
    assert resp.json()["encounter_token"]

    records = _anticheat_records(caplog, EVENT_MOCK_LOCATION)
    assert len(records) == 1


def test_mock_location_log_contains_required_fields(client, player, make_spirit, caplog):
    player_id, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    _summon(client, token, spirit, is_mock_location=True, gps_accuracy_m=8.5)

    record = _anticheat_records(caplog, EVENT_MOCK_LOCATION)[0]

    # 驗收標準指名的四個欄位：player_id、spirit_id、偵測時間、偵測依據
    assert record.player_id == player_id
    assert record.spirit_id == spirit.spirit_id
    datetime.fromisoformat(record.detected_at)  # 可解析的時間戳
    assert "is_mock_location=true" in record.basis
    assert "8.5" in record.basis


def test_no_mock_location_log_when_flag_is_false(client, player, make_spirit, caplog):
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    resp = _summon(client, token, spirit, is_mock_location=False)

    assert resp.status_code == 200
    assert _anticheat_records(caplog, EVENT_MOCK_LOCATION) == []


def test_no_mock_location_log_when_flag_omitted(client, player, make_spirit, caplog):
    """欄位沒帶時預設 false，不該記 log。"""
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    _summon(client, token, spirit)

    assert _anticheat_records(caplog, EVENT_MOCK_LOCATION) == []


# ── 移動速度 ───────────────────────────────────────────────────────────

def test_first_ever_summon_logs_no_speed_event(client, player, make_spirit, caplog):
    """沒有上一次召喚可比對時，不該憑空產生速度事件。"""
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    _summon(client, token, spirit)

    assert _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED) == []


def test_two_rapid_summons_at_distant_landmarks_are_flagged(
    client, player, make_spirit, caplog
):
    """
    連續兩次召喚落在相距約 5 公里的兩個地標，中間只隔幾秒——換算超過
    100,000 km/h，遠高於門檻。兩次都要正常回 200。
    """
    _, token = player
    spirit_a = make_spirit(_LAT_A, _LON_A)
    spirit_b = make_spirit(_LAT_B, _LON_B)

    assert _summon(client, token, spirit_a).status_code == 200
    resp = _summon(client, token, spirit_b)

    assert resp.status_code == 200
    assert resp.json()["encounter_token"]

    records = _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED)
    assert len(records) == 1
    assert records[0].spirit_id == spirit_b.spirit_id


def test_speed_log_contains_required_fields(client, player, make_spirit, caplog):
    player_id, token = player
    spirit_a = make_spirit(_LAT_A, _LON_A)
    spirit_b = make_spirit(_LAT_B, _LON_B)

    _summon(client, token, spirit_a)
    _summon(client, token, spirit_b)

    record = _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED)[0]

    assert record.player_id == player_id
    assert record.spirit_id == spirit_b.spirit_id
    datetime.fromisoformat(record.detected_at)
    # 偵測依據要能讓人看懂為什麼被標記：速度、來源地標、距離、時間、門檻
    assert "km/h" in record.basis
    assert spirit_a.spirit_id in record.basis


def test_repeated_summon_at_same_landmark_is_not_flagged(client, player, make_spirit, caplog):
    """待在同一個地標連續召喚沒有位移，不該被當成瞬移。"""
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    _summon(client, token, spirit)
    _summon(client, token, spirit)

    assert _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED) == []


def test_plausible_speed_between_landmarks_is_not_flagged(
    db_session, player, make_spirit, caplog
):
    """
    同樣是相距 5 公里的兩個地標，但中間隔了一小時（約 5 km/h，走路速度）——
    不該被標記。

    這裡直接呼叫 `run_observation_checks` 並注入 `now`，而不是打 API：走 API
    沒辦法讓兩次請求之間真的過一小時，硬睡一小時更不可行。時間相關的判定
    要能被測，時間就必須是可注入的參數。
    """
    player_id, _ = player
    spirit_a = make_spirit(_LAT_A, _LON_A)
    spirit_b = make_spirit(_LAT_B, _LON_B)

    start = datetime.now(timezone.utc)
    run_observation_checks(
        db_session,
        player_id=player_id,
        spirit=spirit_a,
        is_mock_location=False,
        gps_accuracy_m=None,
        now=start,
    )
    run_observation_checks(
        db_session,
        player_id=player_id,
        spirit=spirit_b,
        is_mock_location=False,
        gps_accuracy_m=None,
        now=start + timedelta(hours=1),
    )

    assert _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED) == []


def test_speed_just_over_threshold_is_flagged(db_session, player, make_spirit, caplog):
    """
    邊界：把兩個地標之間的距離與時間差算成剛好略高於門檻的速度。

    距離約 5.03 公里，要達到 300 km/h 需要 60.3 秒。取 55 秒（約 329 km/h）
    確保在門檻之上，同時仍是同一個數量級——不是靠「快到離譜」才過關。
    """
    player_id, _ = player
    spirit_a = make_spirit(_LAT_A, _LON_A)
    spirit_b = make_spirit(_LAT_B, _LON_B)

    start = datetime.now(timezone.utc)
    run_observation_checks(
        db_session, player_id=player_id, spirit=spirit_a,
        is_mock_location=False, gps_accuracy_m=None, now=start,
    )
    run_observation_checks(
        db_session, player_id=player_id, spirit=spirit_b,
        is_mock_location=False, gps_accuracy_m=None, now=start + timedelta(seconds=55),
    )

    records = _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED)
    assert len(records) == 1
    assert f"threshold {MAX_PLAUSIBLE_SPEED_KMH:.0f}" in records[0].basis


def test_speed_check_is_per_player(client, make_spirit, caplog):
    """
    A 玩家在地標一召喚、B 玩家緊接著在地標二召喚，兩人都不該被標記——
    上一次召喚的記錄是按 player_id 分開存的。
    """
    spirit_a = make_spirit(_LAT_A, _LON_A)
    spirit_b = make_spirit(_LAT_B, _LON_B)

    tokens = []
    for _ in range(2):
        body = client.post(
            "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
        ).json()
        tokens.append((body["player_id"], body["session_token"]))

    try:
        _summon(client, tokens[0][1], spirit_a)
        _summon(client, tokens[1][1], spirit_b)

        assert _anticheat_records(caplog, EVENT_IMPLAUSIBLE_SPEED) == []
    finally:
        for player_id, _ in tokens:
            redis_client.delete(_last_summon_key(player_id))


# ── 不可影響主流程 ─────────────────────────────────────────────────────

def test_summon_succeeds_even_if_anticheat_raises(client, player, make_spirit, monkeypatch):
    """
    防作弊模組壞掉時，召喚仍必須成立。

    這條是整個模組最重要的性質：觀察期收集的資料再有價值，也不值得讓
    Redis 斷線把正常玩家的召喚變成 500。
    """
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    def _boom(*args, **kwargs):
        raise RuntimeError("redis is down")

    monkeypatch.setattr("app.modules.body.anticheat.check_travel_speed", _boom)

    resp = _summon(client, token, spirit, is_mock_location=True)

    assert resp.status_code == 200
    assert resp.json()["encounter_token"]


def test_out_of_radius_summon_records_nothing(client, player, make_spirit, caplog):
    """
    沒通過在場驗證的請求不構成「在場紀錄」，所以不記任何觀察期事件——
    即使它宣稱 is_mock_location=true。
    """
    _, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    resp = _summon(client, token, spirit, lat=_LAT_B, lon=_LON_B, is_mock_location=True)

    assert resp.status_code == 403
    assert _anticheat_records(caplog) == []


# ── 隱私：不得留存原始 GPS ────────────────────────────────────────────

def test_stored_summon_record_contains_no_raw_gps(client, player, make_spirit):
    """
    CONTEXT.md：「原始 GPS 座標只在驗證期間使用後即丟棄，不形成移動軌跡」。

    存進 Redis 的只能是地標 id 與時間。這個測試直接讀原始字串斷言裡面
    沒有座標——哪天有人為了方便把 lat/lon 一起存進去，這裡會紅。
    """
    player_id, token = player
    spirit = make_spirit(_LAT_A, _LON_A)

    _summon(client, token, spirit)

    raw = redis_client.get(_last_summon_key(player_id))
    assert raw is not None
    assert set(json.loads(raw)) == {"spirit_id", "at"}
    assert str(_LAT_A) not in raw
    assert str(_LON_A) not in raw
