"""
「當地氛圍」即時天氣（backend#75／SDD §20.5）。

## 這不是 B9 的輸入

§20.1 的三類白名單（日期／節日、地標官方公開活動、人工審核素材）**維持原狀**。
天氣**不得**進入 `daily_event.py` 的 prompt——那條掃描原始碼的測試仍然有效。

這裡取得的溫度與天氣狀況只走一條路：API 回應 → 客戶端頂列那條 bar。中間不經過
任何模型，也不影響任何生成內容。放行的理由記在 §20.5.1：原禁令要防的是未經審核
的外部**文字**流進敘事，而這裡處理的是數值。

## 🔒 三條隱私邊界

1. **座標降精度**。收到的經緯度一律先四捨五入到小數點後 2 位（約 1 km）才拿去
   查、才拿去當快取 key。天氣不需要知道玩家站在哪一條巷子，而降精度在**進入
   這支模組的第一行**就做掉，不是「記得在某處做」。
2. **不落地**。座標與查詢結果都不寫資料庫。快取在 Redis 且有 TTL，key 只含降
   精度後的格點——那個格點涵蓋約 1 km²，對應不到特定玩家。
3. **金鑰在後端**。客戶端不得直接呼叫 Google Weather API：金鑰會跟著 APK 一起
   發出去，而且那等於讓玩家裝置直接把位置送給第三方。

## 快取為什麼是格點不是玩家

同一個路口的兩個玩家會落在同一個格點上，共用一次查詢。這既省呼叫次數，也讓
快取本身沒有「誰查過」這件事可以洩漏。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.redis_client import redis_client

logger = logging.getLogger(__name__)

# 座標降精度：小數點後 2 位 ≈ 1.1 km（緯度）。SDD §20.5.2。
COORD_PRECISION = 2

# 快取存活時間。天氣不會在幾分鐘內變成另一回事，而客戶端本身就限制 30 分鐘
# 才更新一次——後端快取比它短一點，讓「玩家重開 App」有機會拿到新一點的值。
CACHE_TTL_SECONDS = 15 * 60

_ENDPOINT = "https://weather.googleapis.com/v1/currentConditions:lookup"


@dataclass(frozen=True)
class CurrentWeather:
    """
    一次查詢的結果。frozen——組好之後不該被改寫。

    `condition_text` 是 Google 回的在地化描述（例如「多雲時晴」），`condition_type`
    是它的機器可讀列舉（例如 `PARTLY_CLOUDY`）。兩個都帶回去：文字給玩家看，
    列舉留給客戶端日後要換圖示時用，不必再改一次契約。
    """

    temperature_c: float
    condition_text: str
    condition_type: str


class WeatherProvider(ABC):
    """
    天氣來源的抽象介面。

    抽出來的理由跟 `PushSender` 一樣：測試不該打真實的 Google API——那要金鑰、
    要網路，而且**你沒辦法要求真實 API「下一次回錯誤」**，那正是最需要測的路徑。
    """

    @abstractmethod
    def fetch(self, latitude: float, longitude: float) -> CurrentWeather | None:
        """查詢；失敗回 None，**不拋例外**（呼叫端要的是「有沒有值」）。"""


class FakeWeatherProvider(WeatherProvider):
    """測試用。放在正式程式碼而不是 tests/ 底下，理由同其他 fake。"""

    def __init__(self, result: CurrentWeather | None = None):
        self.result = result
        self.calls: list[tuple[float, float]] = []

    def fetch(self, latitude: float, longitude: float) -> CurrentWeather | None:
        self.calls.append((latitude, longitude))
        return self.result


class GoogleWeatherProvider(WeatherProvider):
    """
    Google Maps Platform Weather API 的 `currentConditions:lookup`。

    只取兩個欄位：`temperature.degrees` 與 `weatherCondition`。回應裡還有濕度、
    風速、紫外線指數等等——**刻意不取**，多帶的欄位會變成之後有人「順手用一下」
    的來源，而 §20.5.2 定義的這條 bar 只顯示溫度與天氣狀況。

    **空氣品質也是刻意不取，不是漏做。** #75 原本的規格寫「溫度 ＋ 空氣品質」，
    2026-08-22 定案不納入（§20.5.4）：空品在 Google Maps Platform 是另一支 API
    （Air Quality API），要另外開通、計費與快取，換到的只是這條 bar 上多一個詞。

    `languageCode` 送 zh-TW：描述文字是直接顯示給玩家的，不經翻譯也不經模型。
    """

    def __init__(self, api_key: str, timeout_seconds: float = 5.0):
        self._api_key = api_key
        self._timeout = timeout_seconds

    def fetch(self, latitude: float, longitude: float) -> CurrentWeather | None:
        params = {
            "key": self._api_key,
            "location.latitude": latitude,
            "location.longitude": longitude,
            "unitsSystem": "METRIC",
            "languageCode": "zh-TW",
        }

        try:
            response = httpx.get(_ENDPOINT, params=params, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            # 天氣查不到不是錯誤，是這條 bar 今天不顯示。整條路徑都不該讓一個
            # 第三方服務的壞天氣（雙關）變成我們的 500。
            # ⚠️ 不要把 params 記進 log——裡面有金鑰，也有玩家所在的格點。
            logger.warning("weather lookup failed", exc_info=True)
            return None

        return _parse(payload)


def _parse(payload: dict) -> CurrentWeather | None:
    """
    把 API 回應轉成我們要的三個值；缺了溫度就當作沒有結果。

    **溫度缺席比溫度是 0 更常見**（欄位改名、部分回應），所以這裡分辨的是
    「有沒有這個 key」，不是真值判斷——`if not degrees` 會把攝氏 0 度當成失敗，
    而台北的冬天雖然到不了，這種寫法遲早會在別的地方咬人。
    """
    temperature = payload.get("temperature") or {}
    degrees = temperature.get("degrees")
    if degrees is None:
        logger.warning("weather response has no temperature.degrees")
        return None

    condition = payload.get("weatherCondition") or {}
    description = condition.get("description") or {}

    return CurrentWeather(
        temperature_c=float(degrees),
        condition_text=str(description.get("text") or ""),
        condition_type=str(condition.get("type") or ""),
    )


def coarsen(value: float) -> float:
    """把一個座標降到約 1 km 的格點。進到這支模組的第一件事。"""
    return round(float(value), COORD_PRECISION)


def _cache_key(latitude: float, longitude: float) -> str:
    return f"weather:{latitude:.2f},{longitude:.2f}"


def get_current_weather(
    provider: WeatherProvider, latitude: float, longitude: float
) -> CurrentWeather | None:
    """
    取得該座標的目前天氣；拿不到回 None。

    座標在這裡就被降精度，**之後的每一步（快取 key、對外查詢）用的都是降過的
    值**——原始座標不會離開這支函式的第一行。
    """
    lat = coarsen(latitude)
    lon = coarsen(longitude)
    key = _cache_key(lat, lon)

    cached = redis_client.hgetall(key)
    if cached:
        return CurrentWeather(
            temperature_c=float(cached["temperature_c"]),
            condition_text=cached.get("condition_text", ""),
            condition_type=cached.get("condition_type", ""),
        )

    weather = provider.fetch(lat, lon)
    if weather is None:
        # ⚠️ 刻意不快取失敗：第三方服務抽風一次，不該讓這個格點在接下來 15 分鐘
        # 內的所有玩家都看不到天氣。代價是連續失敗時會連續重試，那是可接受的
        # ——這條路徑一天最多每格點幾十次。
        return None

    redis_client.hset(
        key,
        mapping={
            "temperature_c": str(weather.temperature_c),
            "condition_text": weather.condition_text,
            "condition_type": weather.condition_type,
        },
    )
    redis_client.expire(key, CACHE_TTL_SECONDS)
    return weather


def build_provider() -> WeatherProvider | None:
    """
    依設定建立來源；沒有金鑰時回 None＝這個環境不提供天氣。

    沒設金鑰是**正常的本機開發狀態**，不是錯誤：跟 TTS 沒設 bucket 時降級成
    「只回文字」同一個道理，缺一項外部服務不該讓服務起不來。
    """
    if not settings.google_weather_api_key:
        return None

    return GoogleWeatherProvider(
        settings.google_weather_api_key, settings.weather_timeout_seconds
    )
