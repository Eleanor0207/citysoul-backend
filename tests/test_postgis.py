"""
PostGIS 可用性驗證（#46）。

這組測試的重點不是「`CREATE EXTENSION postgis` 沒報錯」——那只證明 extension
裝得起來。真正要擋住的失敗是「extension 在，但空間函式在我們實際的用法下不能
用」，所以這裡對一個手刻的正方形實跑 `ST_Contains`。

`districts` 表還沒建，所以多邊形是測試自己給的 WKT，不從資料庫拿。
"""
import pytest
from sqlalchemy import text

from app.modules.body.districts import is_point_in_polygon

# 台北車站一帶的一塊正方形，邊長約 2km。座標是 (經度, 緯度)，PostGIS 的順序。
SQUARE_WKT = (
    "POLYGON((121.49 25.03, 121.51 25.03, 121.51 25.05, 121.49 25.05, 121.49 25.03))"
)


def test_postgis_extension_is_installed(db_session):
    """extension 真的在這個資料庫裡生效，而不是只有映像檔裡有檔案。"""
    installed = db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
    ).scalar()
    assert installed == 1, (
        "PostGIS 沒有啟用。若映像檔是舊的，需要 `docker compose up -d --build`；"
        "若是資料庫沒跑過 migration，需要 `alembic upgrade head`。"
    )


def test_pgvector_still_installed(db_session):
    """
    疊裝 PostGIS 不能把 pgvector 弄掉。

    這條是為了守住 Dockerfile.postgres 的基底選擇——哪天有人把它改成從
    postgis/postgis 疊上來，B6 的記憶檢索會整個壞掉，而這條測試會先紅。
    """
    installed = db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    assert installed == 1


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (25.04, 121.50),  # 正中央
        (25.0301, 121.4901),  # 貼近角落，仍在內部
    ],
)
def test_point_inside_polygon(db_session, latitude, longitude):
    assert is_point_in_polygon(db_session, latitude, longitude, SQUARE_WKT) is True


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (25.06, 121.50),  # 北邊外面
        (25.04, 121.48),  # 西邊外面
        (24.00, 120.00),  # 遠在他方
    ],
)
def test_point_outside_polygon(db_session, latitude, longitude):
    assert is_point_in_polygon(db_session, latitude, longitude, SQUARE_WKT) is False


def test_point_exactly_on_boundary_is_not_contained(db_session):
    """
    ⚠️ 邊界上的點**不算**在內部。

    這條測試存在的目的是把這個反直覺的行為釘住並記錄下來，不是因為我們喜歡這個
    語意。OGC 定義裡 `ST_Contains(A, B)` 要求 B 完全落在 A 的 interior，而邊界
    不屬於 interior。

    實務影響：剛好站在區界線上的玩家會被判定成不在區內。要改成包含邊界的話，
    正確做法是換 `ST_Covers`，不是給多邊形加 buffer。
    """
    on_edge_latitude = 25.03  # 正方形的南邊
    on_edge_longitude = 121.50

    assert is_point_in_polygon(db_session, on_edge_latitude, on_edge_longitude, SQUARE_WKT) is False
