"""劇情道具：beat 的 `grant_item` 發放與 `required_item_ids` 把關（backend#72）。

補的是主線道具鏈上兩個**靜默失效**的缺口：

1. `advance_beat()` 從來不執行 `narrative_directive` 裡的 `commands`，所以
   「回家的畫」永遠發不出來——玩家走到紅樓那一節時手上是空的
2. `unlockable()` 只看前置 beat、不看 `required_item_ids`，所以就算沒有畫，
   紅樓那一節照樣推得動——道具需求形同虛設

兩個都不會報錯：一個是少了東西，一個是門沒關。所以這裡的測試同時釘住
「有發」與「沒有就擋」，只測其中一邊會讓另一邊繼續壞著。

測試用的鏈刻意是最短的兩節，不是萬華主線本身：

    beat_grant（無前置，`grant_item: <畫>`）
      └── beat_gated（前置 = beat_grant，`required_item_ids = [<畫>]`）
"""
import json
import uuid

import pytest

from app.modules.body import models
from app.modules.body.story_progress import (
    RequiredItemMissingError,
    advance_beat,
    arc_state,
    beat_commands,
    unlockable,
)
from app.modules.brain.models import StoryArc, StoryBeat


@pytest.fixture
def chain(db_session, unique_spirit_id):
    """兩節的鏈：第一節發道具，第二節需要那個道具。"""
    arc_id = f"arc-{unique_spirit_id}"
    item_id = f"item-painting-{unique_spirit_id}"
    beat_grant = f"beat-grant-{unique_spirit_id}"
    beat_gated = f"beat-gated-{unique_spirit_id}"

    db_session.add(StoryArc(arc_id=arc_id, title="測試道具鏈", active=True))
    db_session.flush()

    db_session.add_all([
        StoryBeat(
            beat_id=beat_grant, arc_id=arc_id, character_id=None, sequence_order=1,
            trigger_condition="x",
            # 匯入器寫進這一欄的形狀：nodes／commands／completion 整包 JSON。
            # 這裡刻意連 show_info_card 一起放，確認呈現指令會被安靜跳過。
            narrative_directive=json.dumps({
                "commands": [
                    {"grant_item": item_id},
                    {"show_info_card": "card_whatever"},
                ],
                "completion": {"mark_beat_completed": beat_grant},
            }),
            prerequisite_beat_ids=None, active=True,
        ),
        StoryBeat(
            beat_id=beat_gated, arc_id=arc_id, character_id=None, sequence_order=2,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=[beat_grant], required_item_ids=[item_id],
            active=True,
        ),
    ])
    db_session.commit()

    class Fixture:
        pass

    f = Fixture()
    f.arc_id, f.item_id = arc_id, item_id
    f.beat_grant, f.beat_gated = beat_grant, beat_gated

    yield f

    db_session.query(models.PlayersStoryProgress).filter(
        models.PlayersStoryProgress.beat_id.in_([beat_grant, beat_gated])
    ).delete(synchronize_session=False)
    db_session.query(models.PlayerInventory).filter_by(item_id=item_id).delete()
    db_session.query(StoryBeat).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryArc).filter_by(arc_id=arc_id).delete()
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _items(db_session, player_id) -> list[str]:
    db_session.expire_all()
    rows = db_session.query(models.PlayerInventory).filter_by(player_id=player_id).all()
    return sorted(row.item_id for row in rows)


# ── 發放 ────────────────────────────────────────────────────────────


def test_advancing_a_beat_grants_its_item(db_session, chain, player):
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )

    assert result.granted_item_ids == [chain.item_id]
    assert _items(db_session, player_id) == [chain.item_id]


def test_presentation_commands_are_skipped_without_error(db_session, chain, player):
    """`show_info_card` 是客戶端的事。後端遇到它該安靜跳過，不是報錯。"""
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )

    # 只發了道具那一個指令，卡片那個沒有變成第二筆庫存。
    assert len(result.granted_item_ids) == 1


def test_replaying_the_beat_does_not_grant_twice(db_session, chain, player):
    """重複提交是正常的玩家行為。道具只發一次，靠唯一索引擋。"""
    player_id, _ = player

    advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )
    again = advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )

    assert again.already_completed is True
    assert again.granted_item_ids == []
    assert _items(db_session, player_id) == [chain.item_id]


def test_unparsable_directive_does_not_break_progression(db_session, chain, player):
    """
    `beat_gated` 的 `narrative_directive` 是 `"x"`，不是 JSON。舊資料就長這樣。

    解析失敗時該回空清單、讓玩家繼續走，而不是把人卡在半路——beat 這時已經
    記成完成了，丟例外只會讓進度與回應對不起來。
    """
    player_id, _ = player

    advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )
    result = advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_gated
    )

    assert result.already_completed is False
    assert result.granted_item_ids == []


def test_beat_commands_tolerates_junk(db_session, chain):
    beat = db_session.query(StoryBeat).filter_by(beat_id=chain.beat_gated).one()
    assert beat_commands(beat) == []


# ── 把關 ────────────────────────────────────────────────────────────


def test_missing_required_item_blocks_the_beat(db_session, chain, player):
    """
    前置 beat 完成了，但道具不在手上——這一節就不該推得動。

    這裡刻意繞過 `beat_grant` 的正常路徑：直接把前置記成完成，模擬「發放
    那一步壞掉了」的情況。那正是這道門要擋的東西。
    """
    player_id, _ = player
    db_session.add(
        models.PlayersStoryProgress(player_id=player_id, beat_id=chain.beat_grant)
    )
    db_session.commit()

    with pytest.raises(RequiredItemMissingError) as caught:
        advance_beat(
            db_session, player_id=player_id, arc_id=chain.arc_id,
            beat_id=chain.beat_gated,
        )

    # 錯誤訊息要說出是哪一件道具，否則現場只知道「卡住了」。
    assert chain.item_id in str(caught.value)


def test_holding_the_item_opens_the_beat(db_session, chain, player):
    player_id, _ = player

    advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )
    result = advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_gated
    )

    assert result.already_completed is False


def test_gated_beat_is_not_eligible_without_the_item(db_session, chain, player):
    """
    `arc_state()` 的 `eligible_beat_ids` 也要看道具。

    只在 `advance_beat()` 擋的話，客戶端會把一個推不動的節點畫成「可以走」，
    玩家點下去才拿到 403——那是介面在說謊。
    """
    player_id, _ = player
    db_session.add(
        models.PlayersStoryProgress(player_id=player_id, beat_id=chain.beat_grant)
    )
    db_session.commit()

    state = arc_state(db_session, player_id=player_id, arc_id=chain.arc_id)

    assert chain.beat_gated not in state["eligible_beat_ids"]


def test_gated_beat_becomes_eligible_once_the_item_is_held(db_session, chain, player):
    player_id, _ = player

    advance_beat(
        db_session, player_id=player_id, arc_id=chain.arc_id, beat_id=chain.beat_grant
    )
    state = arc_state(db_session, player_id=player_id, arc_id=chain.arc_id)

    assert state["eligible_beat_ids"] == [chain.beat_gated]


def test_unlockable_without_items_argument_ignores_the_item_door(db_session, chain):
    """
    省略 `held_item_ids` 時只判斷前置鏈——匯入器的圖驗證用得到這個形式。

    ⚠️ 這也是一個陷阱：服務層漏傳的話道具門會安靜失效。這條測試把那個行為
    釘住，讓它至少是**寫下來的**選擇，而不是某天有人「順手簡化」的結果。
    """
    beat = db_session.query(StoryBeat).filter_by(beat_id=chain.beat_gated).one()

    assert unlockable(beat, {chain.beat_grant}) is True
    assert unlockable(beat, {chain.beat_grant}, set()) is False


# ── API 層 ──────────────────────────────────────────────────────────


def test_endpoint_reports_granted_items(client, db_session, chain, player):
    player_id, token = player

    response = client.post(
        f"/api/v1/story-arcs/{chain.arc_id}/beats/{chain.beat_grant}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["granted_item_ids"] == [chain.item_id]


def test_endpoint_rejects_a_beat_whose_item_is_missing(
    client, db_session, chain, player
):
    """道具缺漏回 403，且 detail 要跟「前置沒完成」分得出來。"""
    player_id, token = player
    db_session.add(
        models.PlayersStoryProgress(player_id=player_id, beat_id=chain.beat_grant)
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/story-arcs/{chain.arc_id}/beats/{chain.beat_gated}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "required item" in response.json()["detail"]


def test_granted_item_shows_up_in_inventory(client, chain, player):
    """發放要真的進 `/inventory`——客戶端問的是那一支，不是推進的回應。"""
    _, token = player
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        f"/api/v1/story-arcs/{chain.arc_id}/beats/{chain.beat_grant}/advance",
        headers=headers,
    )
    response = client.get("/api/v1/inventory", headers=headers)

    assert response.status_code == 200
    assert any(
        item["item_id"] == chain.item_id for item in response.json()["items"]
    )
