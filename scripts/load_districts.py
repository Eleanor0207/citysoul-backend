"""把 `data/districts/*.geojson` 載進 `brain.districts`。

冪等：同一個 `district_id` 重跑會更新邊界，不會長出第二列。

## 為什麼是獨立腳本而不是 migration

邊界是**內容**，不是 schema。行政區界會修（雖然很少），修的時候應該重跑這支
腳本，而不是寫一支「更新資料」的 migration——migration 的語意是「結構往前走一
步」，把會變動的內容塞進去，等於讓資料的歷史跟 schema 的歷史綁在一起。

## 為什麼不放進 scripts/init_db.py

`init_db` 是垂直切片的 seed，跑的是「讓一個空資料庫能動起來」的最小集合。
區界資料只有用到地理圍欄的劇情才需要，兩者的更新時機完全不同。

## 座標系

GeoJSON 是 TWD97[2020] 地理座標，與 EPSG:4326 差在公分等級，直接以 4326 寫入，
不做轉換（理由見 `data/districts/README.md`）。

`ST_GeomFromGeoJSON` 產出的是 `geometry`，欄位型別是 `geography`，所以中間走一次
`ST_AsText` → `ST_GeogFromText`。
"""
from __future__ import annotations

import json
import pathlib
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "districts"

UPSERT = text(
    """
    INSERT INTO brain.districts
        (district_id, city_id, name, center_lat, center_lng, radius_meters, boundary)
    VALUES
        (:district_id, :city_id, :name, :center_lat, :center_lng, :radius_meters,
         ST_GeogFromText(ST_AsText(ST_GeomFromGeoJSON(:geometry))))
    ON CONFLICT (district_id) DO UPDATE SET
        city_id       = EXCLUDED.city_id,
        name          = EXCLUDED.name,
        center_lat    = EXCLUDED.center_lat,
        center_lng    = EXCLUDED.center_lng,
        radius_meters = EXCLUDED.radius_meters,
        boundary      = EXCLUDED.boundary
    """
)


def load_district(conn, path: pathlib.Path) -> str:
    feature = json.loads(path.read_text(encoding="utf-8"))
    p = feature["properties"]
    conn.execute(
        UPSERT,
        {
            "district_id": p["district_id"],
            "city_id": p["city_id"],
            "name": p["name"],
            "center_lat": p["center_lat"],
            "center_lng": p["center_lng"],
            "radius_meters": p["radius_meters"],
            "geometry": json.dumps(feature["geometry"]),
        },
    )

    # 寫完就驗：多邊形無效的話 ST_Contains 會給出無聲的錯誤答案，而不是報錯。
    valid = conn.execute(
        text("SELECT ST_IsValid(boundary::geometry) FROM brain.districts WHERE district_id = :d"),
        {"d": p["district_id"]},
    ).scalar()
    if not valid:
        raise SystemExit(f"{p['district_id']} 的多邊形無效，已中止")

    return p["district_id"]


def main() -> None:
    files = sorted(DATA_DIR.glob("*.geojson"))
    if not files:
        raise SystemExit(f"{DATA_DIR} 沒有 .geojson")

    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        for path in files:
            district_id = load_district(conn, path)
            print(f"載入 {district_id} <- {path.name}")

        rows = conn.execute(
            text(
                """SELECT district_id, name, radius_meters,
                          ST_NPoints(boundary::geometry)
                   FROM brain.districts ORDER BY district_id"""
            )
        ).all()

    print(f"\nbrain.districts 現有 {len(rows)} 列：")
    for district_id, name, radius, npoints in rows:
        print(f"  {district_id:12s} {name:6s} 概略半徑 {radius} m，邊界 {npoints} 個頂點")


if __name__ == "__main__":
    sys.exit(main())
