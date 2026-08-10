"""
Ticket #38．GET /assets/{avatarId} 素材 catalog 與版本。

驗收標準對照見 GitHub issue #38。不需要憑證——這是靜態資產的版本資訊。
"""
import uuid

import pytest

from app.modules.body.models import AvatarAsset


@pytest.fixture
def avatar(db_session):
    avatar_id = f"test-avatar-{uuid.uuid4()}"
    row = AvatarAsset(
        avatar_id=avatar_id, bundle_url="https://example.test/bundles/v1.bundle", version="v1"
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(AvatarAsset).filter_by(avatar_id=avatar_id).delete()
    db_session.commit()


def _get(client, avatar_id):
    return client.get(f"/api/v1/assets/{avatar_id}")


# ── 回傳 URL 與版本（issue #38 AC1）───────────────────────────────────────

def test_returns_bundle_url_and_version(client, avatar):
    body = _get(client, avatar.avatar_id).json()

    assert body == {
        "avatar_id": avatar.avatar_id,
        "bundle_url": "https://example.test/bundles/v1.bundle",
        "version": "v1",
    }


# ── 不存在回 404（issue #38 AC2）─────────────────────────────────────────

def test_nonexistent_avatar_returns_404(client):
    assert _get(client, "no-such-avatar").status_code == 404


# ── 版本識別可判斷快取是否過期（issue #38 AC3）───────────────────────────

def test_version_changes_when_updated_and_stays_stable_otherwise(client, db_session, avatar):
    first = _get(client, avatar.avatar_id).json()["version"]
    assert first == "v1"

    avatar.version = "v2"
    avatar.bundle_url = "https://example.test/bundles/v2.bundle"
    db_session.commit()

    second = _get(client, avatar.avatar_id).json()["version"]
    assert second != first
    assert second == "v2"

    third = _get(client, avatar.avatar_id).json()["version"]
    assert third == second  # 沒改，版本要穩定不變


# ── 不強制依賴已佈建的 CDN（issue #38 AC4）──────────────────────────────

def test_bundle_url_can_point_at_a_local_or_test_location(client, db_session):
    """
    只要 `avatar_assets` 資料列存在，端點就回傳它記錄的 URL——不驗證、不要求
    那個 URL 指向真實可用的 CDN。開發期指向本機路徑或測試 bucket 一樣能用。
    """
    avatar_id = f"test-avatar-{uuid.uuid4()}"
    row = AvatarAsset(avatar_id=avatar_id, bundle_url="file:///tmp/test-bundle.bundle", version="dev")
    db_session.add(row)
    db_session.commit()

    body = _get(client, avatar_id).json()
    assert body["bundle_url"] == "file:///tmp/test-bundle.bundle"

    db_session.query(AvatarAsset).filter_by(avatar_id=avatar_id).delete()
    db_session.commit()
