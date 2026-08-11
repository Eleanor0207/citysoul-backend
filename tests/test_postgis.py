"""
Ticket #46．PostGIS extension：本機映像檔與 Cloud SQL 都要能啟用。

驗收標準對照見 GitHub issue #46。這裡驗證的是「extension 真的可用」，
不是 `brain.districts` 功能本身（那還沒開票）。
"""
from app.modules.body.districts import is_point_in_polygon

# 手刻的簡單正方形，不是真的萬華區界（issue #46 AC 明訂不需要）。
_SQUARE = "POLYGON((121.0 25.0, 121.0 25.1, 121.1 25.1, 121.1 25.0, 121.0 25.0))"


def test_point_inside_polygon_returns_true(db_session):
    assert is_point_in_polygon(
        db_session, latitude=25.05, longitude=121.05, polygon_wkt=_SQUARE
    ) is True


def test_point_outside_polygon_returns_false(db_session):
    assert is_point_in_polygon(
        db_session, latitude=26.0, longitude=122.0, polygon_wkt=_SQUARE
    ) is False


def test_point_on_boundary_is_not_contained(db_session):
    """
    `ST_Contains` 排除邊界：點必須落在**內部**才算 contains，OGC 定義如此
    （這是實測結果，不是憑印象寫的——先猜邊界算「內」，實跑才發現不是）。
    """
    assert is_point_in_polygon(
        db_session, latitude=25.0, longitude=121.05, polygon_wkt=_SQUARE
    ) is False
