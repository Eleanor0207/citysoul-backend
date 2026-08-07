"""
地理距離計算（S2 在場驗證用）。

刻意做成不依賴 DB／FastAPI 的純函式，方便單獨驗證數學正確性。
"""
import math

EARTH_RADIUS_M = 6371000.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    兩組經緯度之間的大圓距離（公尺）。

    對應 SDD 第7.1節：`distance = haversine(玩家GPS, spirit.latitude, spirit.longitude)`。
    這裡用球面近似（非橢球體），在召喚半徑 50m 這個尺度上誤差遠小於 GPS
    本身的定位誤差，而 `summon_radius_meters` 本來就是「已把 GPS 常見誤差考慮進去
    的有效半徑」（SDD 第7節決策1），所以不需要更精確的 Vincenty 公式。
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
