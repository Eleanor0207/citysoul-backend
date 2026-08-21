"""
待辦 P3 第 21 項．道具查詢。

`player_inventory` 在這之前是**只寫不讀**的：走進萬華會發一封信、
`story_beats.required_item_ids` 拿它當解鎖條件，但沒有端點讓玩家看得到。

⚠️ 這裡最重要的一條是**只看得到自己的**：玩家從 session token 解出來，沒有
任何參數可以指定別人。那條規則壞掉不會有任何症狀浮上來，只會安靜地洩漏。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.brain.models import StoryString

_LETTER = "item_wanhua_letter"
_LETTER_KEY = "wanhua.prologue.letter_body"


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture
def other_player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"])


def _grant(db_session, player_id, item_id, item_type="story_document"):
    row = models.PlayerInventory(
        player_id=player_id, item_id=item_id, item_type=item_type
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture(autouse=True)
def _clean(db_session):
    yield
    db_session.query(models.PlayerInventory).delete()
    db_session.commit()


def _ask(client, session_token):
    return client.get(
        "/api/v1/inventory", headers={"Authorization": f"Bearer {session_token}"}
    )


def test_no_items_is_an_empty_list_not_a_404(client, player):
    # 沒有道具是正常的起始狀態，不是「找不到」（同 /spirits 的規則）。
    pid, token = player

    response = _ask(client, token)

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_an_item_comes_back_with_its_reviewed_story_text(client, db_session, player):
    # 那封信的正文早就在 story_strings 裡（人工審核、匯入器帶進來的），
    # 不另建一張道具文案表——同一段文字有兩個真相就是遲早會不一致的問題。
    pid, token = player
    _grant(db_session, pid, _LETTER)

    letter = db_session.query(StoryString).filter_by(text_key=_LETTER_KEY).first()
    if letter is None:
        pytest.skip("這個資料庫還沒匯入 wanhua 故事字串")

    item = _ask(client, token).json()["items"][0]

    assert item["item_id"] == _LETTER
    assert item["item_type"] == "story_document"
    assert item["story_text"] == letter.text


def test_an_item_without_a_text_mapping_is_not_an_error(client, db_session, player):
    # 徽章、紀念品本來就可能只有 id 沒有長文。那時 story_text 是 null，
    # 客戶端顯示名稱就好——不是缺漏。
    pid, token = player
    _grant(db_session, pid, "badge_first_encounter", item_type="badge")

    item = _ask(client, token).json()["items"][0]

    assert item["story_text"] is None


def test_only_the_callers_own_items_are_visible(client, db_session, player, other_player):
    # 🔒 玩家從 token 解出來，沒有任何參數可以指定別人。這條壞掉不會有症狀，
    # 只會安靜地洩漏。
    pid, token = player
    _grant(db_session, other_player, _LETTER)

    assert _ask(client, token).json()["items"] == []


def test_requires_a_session_token(client):
    assert client.get("/api/v1/inventory").status_code == 401


def test_the_newest_item_comes_first(client, db_session, player):
    from datetime import datetime, timedelta, timezone

    pid, token = player
    older = _grant(db_session, pid, "badge_old", item_type="badge")
    newer = _grant(db_session, pid, "badge_new", item_type="badge")
    older.acquired_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    items = _ask(client, token).json()["items"]

    assert [item["item_id"] for item in items] == ["badge_new", "badge_old"]
