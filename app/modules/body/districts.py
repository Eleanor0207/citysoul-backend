"""
地理圍欄查詢（`brain.districts` 的前置）。

目前只有一支通用的多邊形內含判定。`districts` 表本身還沒建（見 #46 被擋住的
清單），所以這裡刻意不寫 `check_player_in_district()`——那需要先有表和真實的
萬華區邊界資料。

## 為什麼不用 geo.py 那種純 Python 實作

`haversine_distance_m` 是純函式，因為那是「點到點的距離」，數學很短。多邊形
內含判定不一樣：真實的行政區邊界是帶洞、可能跨經度換日線的複雜多邊形，而
PostGIS 已經把這些邊界情況處理完了。自己刻一份射線法，等於在重寫一個會在真實
資料上出錯的 PostGIS。

代價是這支函式需要 DB session，不能像 `geo.py` 那樣單獨驗證——這是刻意的取捨。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


def is_point_in_polygon(
    session: Session, latitude: float, longitude: float, polygon_wkt: str
) -> bool:
    """
    判定一個經緯度點是否落在多邊形「內部」。

    `polygon_wkt` 是 WGS84（SRID 4326）的 WKT 多邊形，例如
    `POLYGON((121.49 25.03, 121.51 25.03, 121.51 25.05, 121.49 25.05, 121.49 25.03))`。

    ⚠️ **邊界上的點回傳 False。** 這是 OGC 對 `ST_Contains` 的定義——邊界屬於
    多邊形的 boundary 而不是 interior，所以「包含」不成立。這不是 bug，但它跟
    直覺相反，之後畫行政區圍欄時要記得：剛好站在區界線上的玩家會被判定成不在
    區內。如果哪天需要把邊界算進去，要換成 `ST_Covers` 而不是加 buffer。

    座標順序是 PostGIS 的 `(經度, 緯度)`，跟我們 API 慣用的 `(緯度, 經度)`
    相反，所以下面 `ST_MakePoint` 的參數是先 lon 後 lat。這裡是唯一需要轉換的
    地方，函式簽章對外維持專案慣用的 lat/lon 順序。
    """
    result = session.execute(
        text(
            "SELECT ST_Contains("
            "  ST_GeomFromText(:polygon_wkt, 4326),"
            "  ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)"
            ")"
        ),
        {"polygon_wkt": polygon_wkt, "longitude": longitude, "latitude": latitude},
    ).scalar()
    return bool(result)
