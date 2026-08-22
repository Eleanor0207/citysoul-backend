"""story 型任務與劇情的接合（backend#71／#72，migration 0028／0029）。

補的是第三道門：**做完任務才推得動劇情**。

在這之前 `quests` 表一列都沒有，而匯入器把 arc 文件裡的 `quest_completed`
安靜丟棄——玩家不做任何任務也能把整條主線推完，三個地標觀察任務在劇情上
完全沒有作用。那個缺口沒有症狀：沒有錯誤、沒有 log、測試也不會紅。

這裡釘住整條鏈的四個接點：

1. 目錄裡有任務（`quests`），而且 daily 型任務**不會**混進去
2. 召喚時替這個地標開進度列（沒有進度列就完成不了任務）
3. 任務沒完成 → beat 推不動，而且 `eligible_beat_ids` 也不會列它
4. 任務完成 → beat 推得動

⚠️ 用真的資料庫。三道門都是 `TEXT[]` 的子集判斷，而錯的寫法（例如把任務
誤當成前置 beat）在假物件上一樣會過。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.quests import (
    STATUS_COMPLETED,
    complete_quest,
    open_story_quests,
    quest_id_for_spirit,
)
from app.modules.body.story_progress import (
    RequiredQuestIncompleteError,
    advance_beat,
    arc_state,
)
from app.modules.brain.models import StoryArc, StoryBeat


@pytest.fixture
def flow(db_session, unique_spirit_id):
    """一個地標、一個 story 任務、一個需要那個任務的 beat。"""
    spirit_id = f"test-spirit-{unique_spirit_id}"
    arc_id = f"arc-{unique_spirit_id}"
    quest_id = f"q_test_{uuid.uuid4().hex[:8]}"
    beat_open = f"beat-open-{unique_spirit_id}"
    beat_gated = f"beat-gated-{unique_spirit_id}"

    db_session.add(
        models.Spirit(
            spirit_id=spirit_id, display_name="測試地標",
            latitude=25.037398, longitude=121.499732,
        )
    )
    db_session.add(StoryArc(arc_id=arc_id, title="測試主線", active=True))
    db_session.flush()

    db_session.add(
        models.Quest(
            quest_id=quest_id, spirit_id=spirit_id, title="測試任務",
            quest_type="story", story_beat_id=beat_gated,
            steps=[{"step_id": "s1", "title": "第一個觀察點", "hint": "看那面牆"}],
            intro="走一圈，找出被補過的地方。", is_active=True,
        )
    )
    db_session.add_all([
        StoryBeat(
            beat_id=beat_open, arc_id=arc_id, character_id=None, sequence_order=1,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=None, active=True,
        ),
        StoryBeat(
            beat_id=beat_gated, arc_id=arc_id, character_id=None, sequence_order=2,
            trigger_condition="x", narrative_directive="x",
            prerequisite_beat_ids=[beat_open], required_quest_ids=[quest_id],
            active=True,
        ),
    ])
    db_session.commit()

    class Fixture:
        pass

    f = Fixture()
    f.spirit_id, f.arc_id, f.quest_id = spirit_id, arc_id, quest_id
    f.beat_open, f.beat_gated = beat_open, beat_gated

    yield f

    db_session.query(models.PlayersStoryProgress).filter(
        models.PlayersStoryProgress.beat_id.in_([beat_open, beat_gated])
    ).delete(synchronize_session=False)
    db_session.query(models.QuestProgress).filter(
        models.QuestProgress.quest_id.in_([quest_id, quest_id_for_spirit(spirit_id)])
    ).delete(synchronize_session=False)
    db_session.query(StoryBeat).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryArc).filter_by(arc_id=arc_id).delete()
    db_session.query(models.Quest).filter_by(quest_id=quest_id).delete()
    db_session.query(models.Spirit).filter_by(spirit_id=spirit_id).delete()
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _open(db_session, flow, player_id):
    from datetime import date

    return open_story_quests(
        db_session, player_id=player_id, spirit_id=flow.spirit_id, today=date.today()
    )


# ── 開任務 ──────────────────────────────────────────────────────────


def test_summoning_opens_the_story_quest(db_session, flow, player):
    """沒有進度列就完成不了任務——`complete_quest()` 會拋 QuestNotFoundError。"""
    player_id, _ = player

    assert _open(db_session, flow, player_id) == [flow.quest_id]

    row = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id, quest_id=flow.quest_id)
        .one()
    )
    # story 任務一輩子只完成一次，所以 issued_date 是 NULL（0003 的
    # uq_quest_progress_onetime 就是照這個分的）。
    assert row.issued_date is None


def test_opening_twice_does_not_duplicate(db_session, flow, player):
    player_id, _ = player

    assert _open(db_session, flow, player_id) == [flow.quest_id]
    assert _open(db_session, flow, player_id) == []

    rows = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id, quest_id=flow.quest_id)
        .all()
    )
    assert len(rows) == 1


def test_opening_does_not_reset_a_completed_quest(db_session, flow, player):
    player_id, _ = player
    _open(db_session, flow, player_id)
    complete_quest(db_session, player_id=player_id, quest_id=flow.quest_id)

    _open(db_session, flow, player_id)

    db_session.expire_all()
    row = (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id, quest_id=flow.quest_id)
        .one()
    )
    assert row.status == STATUS_COMPLETED


# ── 第三道門 ────────────────────────────────────────────────────────


def test_beat_is_blocked_until_the_quest_is_done(db_session, flow, player):
    player_id, _ = player
    advance_beat(
        db_session, player_id=player_id, arc_id=flow.arc_id, beat_id=flow.beat_open
    )

    with pytest.raises(RequiredQuestIncompleteError) as caught:
        advance_beat(
            db_session, player_id=player_id, arc_id=flow.arc_id,
            beat_id=flow.beat_gated,
        )

    assert flow.quest_id in str(caught.value)


def test_blocked_beat_is_not_listed_as_eligible(db_session, flow, player):
    """
    `eligible_beat_ids` 要跟推進判定一致。

    不一致的話客戶端會把一個推不動的節點畫成「可以走」，玩家點下去才拿到
    403——介面在說謊，而且症狀看起來像後端壞了。
    """
    player_id, _ = player
    advance_beat(
        db_session, player_id=player_id, arc_id=flow.arc_id, beat_id=flow.beat_open
    )

    state = arc_state(db_session, player_id=player_id, arc_id=flow.arc_id)

    assert state["eligible_beat_ids"] == []


def test_completing_the_quest_opens_the_beat(db_session, flow, player):
    player_id, _ = player
    advance_beat(
        db_session, player_id=player_id, arc_id=flow.arc_id, beat_id=flow.beat_open
    )
    _open(db_session, flow, player_id)
    complete_quest(db_session, player_id=player_id, quest_id=flow.quest_id)

    state = arc_state(db_session, player_id=player_id, arc_id=flow.arc_id)
    assert state["eligible_beat_ids"] == [flow.beat_gated]

    result = advance_beat(
        db_session, player_id=player_id, arc_id=flow.arc_id, beat_id=flow.beat_gated
    )
    assert result.already_completed is False


def test_endpoint_reports_the_unfinished_quest(client, db_session, flow, player):
    """403 的 detail 要說出是哪個任務，客戶端才導得回去。"""
    player_id, token = player
    advance_beat(
        db_session, player_id=player_id, arc_id=flow.arc_id, beat_id=flow.beat_open
    )

    response = client.post(
        f"/api/v1/story-arcs/{flow.arc_id}/beats/{flow.beat_gated}/advance",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "required quest" in response.json()["detail"]
    assert flow.quest_id in response.json()["detail"]


# ── 任務列表 ────────────────────────────────────────────────────────


def test_story_quest_carries_its_catalogue_content(client, db_session, flow, player):
    """
    玩家要看得到任務的標題、引言與步驟——那是「有事情可做」跟「有一個 id」的差別。

    這些欄位只有目錄裡的任務才有；daily 型任務是空的，見
    `test_player_queries.test_daily_quests_returns_the_full_item_shape`。
    """
    player_id, token = player
    _open(db_session, flow, player_id)

    body = client.get(
        "/api/v1/quests/daily", headers={"Authorization": f"Bearer {token}"}
    ).json()
    item = next(q for q in body["quests"] if q["quest_id"] == flow.quest_id)

    assert item["quest_type"] == "story"
    assert item["title"] == "測試任務"
    assert item["intro"].startswith("走一圈")
    assert [step["step_id"] for step in item["steps"]] == ["s1"]
    # spirit_id 來自目錄，不是從 quest_id 反推——`q_...` 不合命名慣例。
    assert item["spirit_id"] == flow.spirit_id


# ── 隨附內容 ────────────────────────────────────────────────────────


def test_the_shipped_quest_files_validate():
    from scripts import import_quests

    loaded = import_quests.load_files(import_quests.QUESTS_DIR)
    assert loaded, "content/quests 裡應該有萬華三地標的任務"

    by_spirit, errors = import_quests.validate(loaded)
    assert errors == []
    assert set(by_spirit) == {
        "longshan_temple",
        "ximen_red_house",
        "bopiliao_historic_block",
    }


def test_daily_quests_are_rejected_from_the_catalogue():
    """
    `{spirit_id}:daily` 進了目錄表就會有兩個同名的東西——一個推導的、一個
    編輯的——而它們之後一定會漂移。
    """
    from scripts import import_quests

    rows, errors = import_quests.build_rows({
        "spirit_id": "longshan_temple",
        "review_status": "DRAFT",
        "quests": [{
            "quest_id": "longshan_temple:daily",
            "title": "不該進目錄",
            "steps": [{"step_id": "s1", "title": "t", "hint": "h"}],
        }],
    })

    assert any("不進目錄表" in error for error in errors)
