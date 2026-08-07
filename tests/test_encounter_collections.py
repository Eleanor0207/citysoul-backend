"""
相遇收藏（AC5.2、migration 0004）。

目前只有表，還沒有建立收藏的 API（#44）。這裡驗的是**表本身的保證**——
尤其是「每個地標只能收藏一次」那條約束，以及這張表不含位置資料。
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.body import models


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=25.0373983,
        longitude=121.4997318,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.EncounterCollection).filter_by(place_id=row.spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player_id(client, db_session, unique_device_id):
    body = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    return uuid.UUID(body["player_id"])


def test_same_landmark_cannot_be_collected_twice(db_session, player_id, spirit):
    """
    AC5.2：同一個地標只加一次共鳴值。

    這條約束是**唯一**擋得住重複入帳的東西，跟 `resonance_events` 同一個道理。
    「先查有沒有收藏過、沒有才寫」在兩個並行請求下會兩個都通過——兩邊各自查到
    的都是當下真實的資料，中間那條縫關不起來。所以流程是先寫、撞到約束才知道
    自己是重複的。
    """
    for _ in range(2):
        db_session.add(
            models.EncounterCollection(player_id=player_id, place_id=spirit.spirit_id)
        )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_different_players_can_collect_the_same_landmark(client, db_session, spirit):
    """約束是 (player_id, place_id)，不是 place_id。別的玩家當然也能收藏龍山寺。"""
    ids = [
        uuid.UUID(
            client.post(
                "/api/v1/players", json={"device_id": f"collector-{uuid.uuid4()}"}
            ).json()["player_id"]
        )
        for _ in range(2)
    ]
    for player_id in ids:
        db_session.add(
            models.EncounterCollection(player_id=player_id, place_id=spirit.spirit_id)
        )
    db_session.commit()

    assert (
        db_session.query(models.EncounterCollection).filter_by(place_id=spirit.spirit_id).count()
        == 2
    )


def test_collection_stores_no_coordinates(db_session, player_id, spirit):
    """
    CONTEXT.md 對「相遇收藏」的定義是「玩家、地標、收藏時間、本機辨識結果與
    共鳴貢獻」，**不含原始照片或 GPS 座標**。

    這張表帶 player_id 又帶時間，一旦加上座標欄位，累積起來就是移動軌跡——
    只是名字叫收藏。這個測試在下次有人想加 `latitude` 進來時變紅。
    """
    columns = set(models.EncounterCollection.__table__.columns.keys())

    assert columns == {
        "collection_id",
        "player_id",
        "place_id",
        "recognized_label",
        "collected_at",
    }
