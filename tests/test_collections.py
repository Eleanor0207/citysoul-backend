"""
收藏視窗（圖鑑）：`GET /api/v1/collections` 與 `/summon` 的發放。

設計定案 2026-08-18（A.L.）：9 張初次相遇圖 ＋ 每條 arc 一張劇本完成圖，
未解鎖顯示空欄位，不做拍照徽章。
"""
import math
import uuid

import pytest

from app.modules.body import collections_service, models
from app.modules.body.geo import EARTH_RADIUS_M
from app.modules.brain.models import StoryArc

_LAT = 25.0955
_LON = 121.5186
_RADIUS_M = 50


def _offset_north(lat: float, meters: float) -> float:
    return lat + math.degrees(meters / EARTH_RADIUS_M)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _summon(client, token, spirit_id, lat=_LAT, lon=_LON):
    return client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit_id, "latitude": lat, "longitude": lon},
        headers=_auth(token),
    )


def _entry(payload: dict, collection_id: str) -> dict | None:
    return next((e for e in payload["entries"] if e["collection_id"] == collection_id), None)


@pytest.fixture
def spirit(db_session, unique_spirit_id, purge_spirit_child_rows):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=_RADIUS_M,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    # 召喚會寫 resonance／resonance_events，兩者都對 spirits 有外鍵（2026-08-19）。
    purge_spirit_child_rows(row.spirit_id)
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def inactive_spirit(db_session):
    """下架的靈魂。霞海城隍廟目前就是這個狀態（08-16 起，沒有近景文件）。"""
    row = models.Spirit(
        spirit_id=f"test-inactive-{uuid.uuid4()}",
        display_name="下架的測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=_RADIUS_M,
        is_active=False,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client, unique_device_id, db_session):
    resp = client.post("/api/v1/players", json={"device_id": unique_device_id}).json()
    yield resp
    # 測試留下的 inventory 列要自己收掉，不然會累積在共用的測試資料庫裡。
    db_session.query(models.PlayerInventory).filter_by(
        player_id=uuid.UUID(resp["player_id"])
    ).delete()
    db_session.commit()


@pytest.fixture
def encounter_asset(db_session, spirit):
    """該地標的相遇圖素材。"""
    row = models.MediaAsset(
        asset_type=collections_service.ASSET_TYPE_ENCOUNTER,
        owner_type="system",
        spirit_id=spirit.spirit_id,
        gcs_path=f"gs://test/{spirit.spirit_id}.png",
        cdn_url=f"https://cdn.test/{spirit.spirit_id}.png",
        content_type="image/png",
        caption="這是放大檢視裡那段說明文字，長度不設限。",
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def arc(db_session):
    row = StoryArc(arc_id=f"test-arc-{uuid.uuid4()}", title="測試劇本")
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


# ── 驗證 ───────────────────────────────────────────────────────────────

def test_missing_session_token_returns_401(client):
    assert client.get("/api/v1/collections").status_code == 401


# ── 發放 ───────────────────────────────────────────────────────────────

def test_summon_unlocks_the_encounter_slot(client, player, spirit):
    before = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    item_id = collections_service.encounter_item_id(spirit.spirit_id)
    assert _entry(before, item_id)["unlocked"] is False

    assert _summon(client, player["session_token"], spirit.spirit_id).status_code == 200

    after = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    assert _entry(after, item_id)["unlocked"] is True


def test_repeated_summon_grants_exactly_one_row(client, player, spirit, db_session):
    """
    連續召喚只會有一列。

    去重靠 `uq_inventory_player_item` 唯一索引（`ON CONFLICT DO NOTHING`），不是
    應用層先查再寫——玩家在召喚半徑邊緣來回、或單純連點兩次，都會重複打這支端點。
    """
    for _ in range(3):
        assert _summon(client, player["session_token"], spirit.spirit_id).status_code == 200

    count = (
        db_session.query(models.PlayerInventory)
        .filter_by(
            player_id=uuid.UUID(player["player_id"]),
            item_id=collections_service.encounter_item_id(spirit.spirit_id),
        )
        .count()
    )
    assert count == 1


def test_failed_summon_does_not_unlock(client, player, spirit):
    """
    不在召喚半徑內就沒有相遇，也就沒有紀念品。

    發放刻意放在在場驗證之後——跟觀察期紀錄同一個理由：沒通過驗證的請求不構成
    一次相遇。
    """
    far = _offset_north(_LAT, _RADIUS_M + 200)
    assert _summon(client, player["session_token"], spirit.spirit_id, lat=far).status_code == 403

    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    item_id = collections_service.encounter_item_id(spirit.spirit_id)
    assert _entry(payload, item_id)["unlocked"] is False


def test_granting_the_collectible_does_not_award_extra_resonance(
    client, player, spirit, db_session
):
    """
    🔒 **送圖與加分仍然是兩件事**，即使它們現在發生在同一支端點裡。

    ⚠️ 這條測試在 2026-08-19 改寫過。原本斷言「召喚完全不加共鳴值」——那在舊規則
    （共鳴只來自任務 +20 與拍照 +10）下是對的。新規則裡召喚**就是** +10 的來源，
    所以那個斷言必然紅。

    但它原本要守的東西沒有變，只是換了形狀：`grant_encounter_collectible()` 的
    docstring 寫著「送圖是紀念，不是計分」，而**紀念品一輩子一張、分數每天一次**
    ——兩者頻率不同，合併就錯了。所以這裡改成守「入帳只有一筆，而且來自
    `daily_encounter`」：如果哪天有人在發圖那條路上順手也加一次分，帳本會多出
    一列別的 source_type，這條就會紅。

    只斷言 `resonance_value == 10` 是不夠的——兩筆各 +5 也是 10。要看帳本。
    """
    _summon(client, player["session_token"], spirit.spirit_id)

    db_session.expire_all()
    events = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=uuid.UUID(player["player_id"]))
        .all()
    )

    assert [e.source_type for e in events] == ["daily_encounter"], (
        "召喚應該只入帳一筆每日共鳴；多出來的列代表發圖那條路也在加分"
    )
    assert events[0].amount == 10


def test_repeated_summon_awards_resonance_only_once_per_day(
    client, player, spirit, db_session
):
    """
    同一天連續召喚兩次：圖只有一張（既有測試涵蓋），**分也只有一筆**。

    兩層去重的機制不同（inventory 靠 `uq_inventory_player_item`，共鳴靠
    `uq_resonance_events_source`），所以要各自驗。
    """
    _summon(client, player["session_token"], spirit.spirit_id)
    _summon(client, player["session_token"], spirit.spirit_id)

    db_session.expire_all()
    count = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=uuid.UUID(player["player_id"]))
        .count()
    )

    assert count == 1


# ── 未解鎖的遮蔽 ───────────────────────────────────────────────────────

def test_locked_entry_has_a_title(client, player, spirit, encounter_asset):
    """
    未解鎖的格子**看得到名字**（A.L. 2026-08-18）。

    名字不是秘密——那些地標在地圖上本來就看得到，遮起來只會讓收藏視窗變得難懂。
    """
    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    entry = _entry(payload, collections_service.encounter_item_id(spirit.spirit_id))

    assert entry["unlocked"] is False
    assert entry["title"] == spirit.display_name


def test_locked_entry_never_leaks_the_image(client, player, spirit, encounter_asset):
    """
    🔒 未解鎖時圖**不離開伺服器**。

    圖是解鎖真正換到的東西。如果 API 照樣把 URL 送出去，抓一次封包就看完整本圖鑑，
    「要走到現場才看得到」那條就等於沒生效——遮蔽必須做在伺服器端，不能靠客戶端自律。

    ⚠️ 這條跟上面那條是**一起看**的：08-18 放寬了 title，`image_url` 那一層沒有跟著
    放行。哪天有人「順手」把兩個一起打開，紅的會是這一條。
    """
    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    entry = _entry(payload, collections_service.encounter_item_id(spirit.spirit_id))

    assert entry["unlocked"] is False
    assert entry["image_url"] is None
    assert entry["caption"] is None
    assert entry["acquired_at"] is None

    body = client.get("/api/v1/collections", headers=_auth(player["session_token"])).text
    assert encounter_asset.cdn_url not in body
    assert encounter_asset.caption not in body


def test_unlocked_entry_returns_title_and_image(client, player, spirit, encounter_asset):
    _summon(client, player["session_token"], spirit.spirit_id)

    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    entry = _entry(payload, collections_service.encounter_item_id(spirit.spirit_id))

    assert entry["unlocked"] is True
    assert entry["title"] == spirit.display_name
    assert entry["image_url"] == encounter_asset.cdn_url
    assert entry["caption"] == encounter_asset.caption
    assert entry["acquired_at"] is not None


def test_unlocked_entry_without_artwork_still_unlocks(client, player, spirit):
    """
    圖還沒畫好也能先上線：玩家照樣解鎖，只是 `image_url` 是 None。

    這是「圖不存在玩家的列上」換來的——美術之後把 `media_assets` 補進去，所有
    既有玩家下一次開收藏就看得到，不用回填任何一列。
    """
    _summon(client, player["session_token"], spirit.spirit_id)

    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    entry = _entry(payload, collections_service.encounter_item_id(spirit.spirit_id))

    assert entry["unlocked"] is True
    assert entry["title"] == spirit.display_name
    assert entry["image_url"] is None
    assert entry["caption"] is None


# ── 清單組成 ───────────────────────────────────────────────────────────

def test_inactive_spirit_has_no_slot(client, player, inactive_spirit):
    """
    下架的靈魂不佔格子。霞海城隍廟因此不需要任何特例——它 `is_active=false`，
    `/summon` 對它回 404，這裡也不會有它的格子。轉回 true 那一格就自己長出來。
    """
    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    assert _entry(payload, collections_service.encounter_item_id(inactive_spirit.spirit_id)) is None


def test_arc_has_its_own_slot_with_source_type(client, player, arc):
    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    entry = _entry(payload, collections_service.arc_completion_item_id(arc.arc_id))

    assert entry is not None
    assert entry["source_type"] == "arc_completion"
    assert entry["unlocked"] is False


def test_encounter_slots_all_precede_arc_slots(client, player, spirit, arc):
    """
    順序穩定：先全部相遇格，再全部劇本格。客戶端靠這個切 3×3 方陣與下方劇本區，
    不需要自己排序。
    """
    payload = client.get("/api/v1/collections", headers=_auth(player["session_token"])).json()
    types = [e["source_type"] for e in payload["entries"]]

    assert "encounter" in types and "arc_completion" in types
    assert types == sorted(types, key=lambda t: 0 if t == "encounter" else 1)


def test_arc_completion_grant_is_idempotent(db_session, player, arc):
    """
    劇本完成圖的發放路徑先備好（還沒有呼叫端——等 Lead 改完劇本機制才接
    `beat_finale`），但語意必須跟相遇圖完全一致，所以在這裡守著。
    """
    player_id = uuid.UUID(player["player_id"])

    assert collections_service.grant_arc_completion_collectible(
        db_session, player_id=player_id, arc_id=arc.arc_id
    ) is True
    assert collections_service.grant_arc_completion_collectible(
        db_session, player_id=player_id, arc_id=arc.arc_id
    ) is False

    count = (
        db_session.query(models.PlayerInventory)
        .filter_by(
            player_id=player_id,
            item_id=collections_service.arc_completion_item_id(arc.arc_id),
        )
        .count()
    )
    assert count == 1


def test_encounter_grant_reports_false_the_second_time(db_session, player, spirit):
    """
    🔴 回歸測試：`grant_*` 的回傳值必須真的反映「有沒有插進去」。

    2026-08-18 的第一版用 `result.rowcount` 判斷，而 psycopg3 對沒有 RETURNING 的
    INSERT 回傳 **-1**（不知道）——`bool(-1)` 是 True，所以永遠說「剛剛發了」。
    去重本身沒壞（唯一索引照樣擋住），壞的是 log 與測試會說謊。改用
    `ON CONFLICT DO NOTHING ... RETURNING` 之後，跳過時回 0 列。

    arc 那一支走同一個 helper，但這裡也守一次——兩個入口分開壞掉的話，一條紅的
    測試指不出是哪一邊。
    """
    player_id = uuid.UUID(player["player_id"])

    assert collections_service.grant_encounter_collectible(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id
    ) is True
    assert collections_service.grant_encounter_collectible(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id
    ) is False


def test_collections_are_per_player(client, player, spirit, unique_device_id):
    """🔒 別人解鎖的格子不會出現在我的清單裡。"""
    _summon(client, player["session_token"], spirit.spirit_id)

    other = client.post("/api/v1/players", json={"device_id": f"{unique_device_id}-other"}).json()
    payload = client.get("/api/v1/collections", headers=_auth(other["session_token"])).json()

    assert _entry(payload, collections_service.encounter_item_id(spirit.spirit_id))["unlocked"] is False
