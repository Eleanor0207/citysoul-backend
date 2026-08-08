"""
#38．`GET /api/v1/assets/{avatarId}` 素材 catalog 與版本。

對真實 Postgres 跑。**不依賴已佈建的 CDN**——URL 只是資料，測試用本機路徑，
否則這張票會被基礎建設進度卡住（AC 明訂）。
"""
import uuid

import pytest

from app.modules.body import models

_CATALOG = "http://localhost:8080/addressables/catalog_v1.json"
_BUNDLE = "http://localhost:8080/addressables/longshan_spirit.bundle"


@pytest.fixture
def avatar(db_session):
    avatar_id = f"test-avatar-{uuid.uuid4().hex[:8]}"
    row = models.AvatarAsset(
        avatar_id=avatar_id,
        catalog_url=_CATALOG,
        bundle_url=_BUNDLE,
        version="v1",
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.AvatarAsset).filter_by(avatar_id=avatar_id).delete()
    db_session.commit()


def _get(client, avatar_id):
    return client.get(f"/api/v1/assets/{avatar_id}")


# ── 基本回應 ───────────────────────────────────────────────────────────

def test_returns_urls_and_version(client, avatar):
    """AC：回應至少含 `avatar_id`、URL、版本識別。"""
    response = _get(client, avatar.avatar_id)

    assert response.status_code == 200
    assert response.json() == {
        "avatar_id": avatar.avatar_id,
        "catalog_url": _CATALOG,
        "bundle_url": _BUNDLE,
        "version": "v1",
    }


def test_requires_no_token(client, avatar):
    """資產位置不是玩家資料，不需要驗證。"""
    assert _get(client, avatar.avatar_id).status_code == 200


def test_unknown_avatar_returns_404(client):
    assert _get(client, "no-such-avatar").status_code == 404


# ── 版本語意：改了要變、沒改要穩定 ────────────────────────────────────

def test_version_changes_when_the_asset_is_updated(client, avatar, db_session):
    """AC 步驟 1–3：更新資產版本後，回傳的版本必須不同於 v1。"""
    first = _get(client, avatar.avatar_id).json()["version"]

    avatar.version = "v2"
    db_session.commit()

    second = _get(client, avatar.avatar_id).json()["version"]

    assert first == "v1"
    assert second != first


def test_version_is_stable_when_nothing_changes(client, avatar):
    """
    🔒 AC 步驟 4：**沒改就要穩定不變。**

    ⚠️ 只驗「改了要變」的話，一個每次回傳隨機值（或 `now()`）的實作也會通過
    ——但那會讓客戶端**每次啟動都重抓整包**，正好是這支端點要避免的事。

    連續三次呼叫，值必須完全相同。
    """
    versions = {_get(client, avatar.avatar_id).json()["version"] for _ in range(3)}

    assert versions == {"v1"}


def test_version_does_not_track_unrelated_row_updates(client, avatar, db_session):
    """
    版本是**明確寫入的欄位**，不從 `updated_at` 或內容 hash 算。

    這裡只改 URL 不改版本——如果版本是算出來的，它會跟著變，而所有客戶端會
    因為一次無關的更新重抓整包。
    """
    before = _get(client, avatar.avatar_id).json()["version"]

    avatar.bundle_url = _BUNDLE + "?cache-bust=1"
    db_session.commit()

    assert _get(client, avatar.avatar_id).json()["version"] == before


# ── 開發期可指向本機 ───────────────────────────────────────────────────

def test_urls_can_point_at_a_local_location(client, avatar):
    """
    AC：開發期可指向本機或測試用儲存位置，**不強制依賴已佈建的 CDN**。

    URL 只是資料表裡的字串，換位置只要改資料——這就是把它放進資料表而不是
    設定檔或程式碼常數的理由。
    """
    body = _get(client, avatar.avatar_id).json()

    assert body["catalog_url"].startswith("http://localhost")
    assert body["bundle_url"].startswith("http://localhost")


def test_catalog_and_bundle_are_separate_fields(client, avatar, db_session):
    """
    Addressables 的索引與實際內容可能放在不同路徑甚至不同 bucket。
    合成一個欄位的話，之後要分開就是破壞性變更。
    """
    avatar.catalog_url = "https://cdn.example.test/catalog.json"
    avatar.bundle_url = "https://other-bucket.example.test/spirit.bundle"
    db_session.commit()

    body = _get(client, avatar.avatar_id).json()

    assert body["catalog_url"] != body["bundle_url"]
