"""
PostGIS 地理圍欄判斷（issue #46 前置驗證）。

這支檔案**不是** `brain.districts` 功能本身——那張表、`story_arcs`、
`landmark_souls.district_id` 都還沒開票（見 #46「被擋住的東西」清單）。
這裡只證明 PostGIS extension 真的可用，並先把「判斷一個座標是否落在某個
地理圍欄內」這個查詢寫成可重用的函式，供未來的 `check_player_in_district()`
直接呼叫，不用重寫這段 SQL。

## 座標不落地

`latitude`／`longitude` 只當作這次查詢的參數，**不寫入任何資料表、不進
log**（issue #46 AC）。CONTEXT.md「在場紀錄」：原始 GPS 座標用完即丟；
「玩家曾在某時刻進入某區域」未來只會以 arc 信件這種結果性資料表示，
不會是座標本身留存。
"""
from sqlalchemy import text
from sqlalchemy.orm import Session


def is_point_in_polygon(db: Session, *, latitude: float, longitude: float, polygon_wkt: str) -> bool:
    """
    `ST_Contains`：座標是否落在給定多邊形內。

    `polygon_wkt` 是 WKT 格式的多邊形字串（例如
    `"POLYGON((121.0 25.0, 121.0 25.1, 121.1 25.1, 121.1 25.0, 121.0 25.0))"`），
    SRID 固定 4326（WGS84，跟 `spirits.latitude/longitude` 用的座標系一致）。

    這支函式本身**也是** extension 真的可用的證明（issue #46 AC）：如果
    PostGIS 沒裝好，這裡會直接拋 `UndefinedFunction`，不會是「建立 extension
    沒報錯，但函式其實不能用」這種安靜的假成功。
    """
    result = db.execute(
        text(
            "SELECT ST_Contains("
            "ST_GeomFromText(:polygon, 4326), "
            "ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)"
            ")"
        ),
        {"polygon": polygon_wkt, "longitude": longitude, "latitude": latitude},
    ).scalar()
    return bool(result)
