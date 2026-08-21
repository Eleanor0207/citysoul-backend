"""
backend#75．「當地氛圍」即時天氣（SDD §20.5）。

外部服務以 fake 注入——**不需要 Google 金鑰，也不打網路**。理由同 push／gemini：
最需要測的是「上游壞掉時我們怎麼辦」，而你沒辦法要求真實 API 下一次回錯誤。

⚠️ 這裡最重要的一組是**隱私**：座標降精度、不落地、回應不含位置。那三條是
SDD §20.5.2 的硬規則，也是最容易在之後某次「順手加個欄位」時被弄破的東西。
"""
import uuid

import pytest

from app.core.redis_client import redis_client
from app.modules.body import weather
from app.modules.body.router import get_weather_provider
from app.main import app

_TAIPEI_LAT, _TAIPEI_LON = 25.0373983, 121.4997318

_SUNNY = weather.CurrentWeather(
    temperature_c=26.4, condition_text="多雲時晴", condition_type="PARTLY_CLOUDY"
)


@pytest.fixture(autouse=True)
def _clear_weather_cache():
    """
    每個測試前後清掉天氣快取。

    快取 key 是**格點**不是玩家，所以測試之間一定會互相污染——那正是它的設計
    （同一個路口的兩個玩家共用一次查詢），不是缺陷。
    """
    def _clear():
        for key in redis_client.scan_iter("weather:*"):
            redis_client.delete(key)

    _clear()
    yield
    _clear()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return body["session_token"]


@pytest.fixture
def provider():
    """把 fake 掛進端點的依賴，測完還原——不要讓覆寫外洩到別的測試檔。"""
    fake = weather.FakeWeatherProvider(_SUNNY)
    app.dependency_overrides[get_weather_provider] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_weather_provider, None)


def _ask(client, session_token, lat=_TAIPEI_LAT, lon=_TAIPEI_LON):
    return client.get(
        "/api/v1/weather/current",
        params={"latitude": lat, "longitude": lon},
        headers={"Authorization": f"Bearer {session_token}"},
    )


# ── 基本行為 ───────────────────────────────────────────────────────────

def test_returns_temperature_and_condition(client, player, provider):
    body = _ask(client, player).json()

    assert body["temperature_c"] == 26.4
    assert body["condition_text"] == "多雲時晴"
    assert body["condition_type"] == "PARTLY_CLOUDY"


def test_requires_a_session_token(client, provider):
    # 這支不含玩家資料，本來可以公開。要憑證是為了不讓它變成免費天氣代理——
    # 金鑰在我們這邊，開著等於誰都能拿我們的額度去查。
    response = client.get(
        "/api/v1/weather/current",
        params={"latitude": _TAIPEI_LAT, "longitude": _TAIPEI_LON},
    )

    assert response.status_code == 401
    assert provider.calls == [], "沒有憑證不該白打一次上游"


# ── 🔒 隱私 ────────────────────────────────────────────────────────────

def test_the_coordinate_sent_upstream_is_coarsened(client, player, provider):
    # 小數點後 2 位 ≈ 1.1 km。天氣不需要知道玩家站在哪一條巷子（§20.5.2）。
    _ask(client, player)

    assert provider.calls == [(25.04, 121.50)]


def test_two_nearby_players_share_one_upstream_call(client, player, provider):
    # 同一個格點只查一次：省呼叫次數，也讓快取本身沒有「誰查過」可以洩漏。
    _ask(client, player, lat=25.0373983, lon=121.4997318)
    _ask(client, player, lat=25.0382000, lon=121.5001000)

    assert len(provider.calls) == 1


def test_the_response_carries_no_location(client, player, provider):
    # 請求帶了座標進來，回應不該再把它送回去——那只會讓這個值出現在客戶端的
    # log 與快取裡（同 landmark-photo 不回傳任何影像痕跡的理由）。
    body = _ask(client, player).json()

    assert set(body) == {"temperature_c", "condition_text", "condition_type"}


def test_nothing_is_written_to_the_database(client, player, provider, db_session):
    from app.modules.body import models

    # 這條 bar 是一次性顯示。留下任何紀錄都違反 CONTEXT.md「不在背景追蹤、
    # 判斷或保存玩家位置」——這裡挑一張真的會因位置而寫入的表當哨兵
    # （`/districts/check-entry` 走進萬華時會寫它）。
    before = db_session.query(models.PlayerInventory).count()
    _ask(client, player)
    db_session.expire_all()
    after = db_session.query(models.PlayerInventory).count()

    assert before == after


# ── 失敗路徑 ───────────────────────────────────────────────────────────

def test_an_upstream_failure_is_503_not_a_fake_value(client, player):
    # §18.7.3「回退不阻擋進度」講的是敘事；這條 bar 不是敘事也不是進度，
    # 它就是一個數值。沒有值的時候客戶端整條隱藏，比顯示一個假的 0 度誠實。
    fake = weather.FakeWeatherProvider(None)
    app.dependency_overrides[get_weather_provider] = lambda: fake
    try:
        assert _ask(client, player).status_code == 503
    finally:
        app.dependency_overrides.pop(get_weather_provider, None)


def test_no_api_key_configured_is_503_not_a_crash(client, player):
    # 沒設金鑰是正常的本機開發狀態，不是錯誤：缺一項外部服務不該讓服務起不來。
    app.dependency_overrides[get_weather_provider] = lambda: None
    try:
        assert _ask(client, player).status_code == 503
    finally:
        app.dependency_overrides.pop(get_weather_provider, None)


def test_a_failed_lookup_is_not_cached(client, player):
    # 第三方抽風一次，不該讓這個格點在接下來 15 分鐘內的所有玩家都看不到天氣。
    failing = weather.FakeWeatherProvider(None)
    app.dependency_overrides[get_weather_provider] = lambda: failing
    try:
        _ask(client, player)
        _ask(client, player)
        assert len(failing.calls) == 2
    finally:
        app.dependency_overrides.pop(get_weather_provider, None)


def test_out_of_range_coordinates_are_rejected_before_any_lookup(client, player, provider):
    response = client.get(
        "/api/v1/weather/current",
        params={"latitude": 999.0, "longitude": 121.5},
        headers={"Authorization": f"Bearer {player}"},
    )

    assert response.status_code == 422
    assert provider.calls == []


# ── 回應解析 ───────────────────────────────────────────────────────────

def test_zero_degrees_is_a_real_temperature_not_a_missing_one():
    # `if not degrees` 會把攝氏 0 度當成失敗。台北的冬天到不了，但這種寫法
    # 遲早會在別的地方咬人——所以解析看的是「有沒有這個 key」。
    parsed = weather._parse(
        {
            "temperature": {"degrees": 0, "unit": "CELSIUS"},
            "weatherCondition": {"description": {"text": "下雪"}, "type": "SNOW"},
        }
    )

    assert parsed is not None
    assert parsed.temperature_c == 0.0


def test_a_response_without_temperature_yields_nothing():
    assert weather._parse({"weatherCondition": {"type": "CLEAR"}}) is None


def test_a_missing_condition_still_gives_the_temperature():
    # 溫度是這條 bar 的主體，天氣狀況缺席時不該整條消失。
    parsed = weather._parse({"temperature": {"degrees": 31.2}})

    assert parsed.temperature_c == 31.2
    assert parsed.condition_text == ""
    assert parsed.condition_type == ""
