"""劇情變數與完成時間（backend#72，migration 0030）。

## 變數

萬華 arc 文件 §7.1 的 `story_focus` / `reveal_lens` / `ending_mark` 決定個人化
年代簿那一頁的措辭。先前**沒有儲存位置**——節點資料裡有
`set_once: {story_focus: person}`，但沒有任何表能放它。

這裡釘住 set_once 的語意由**主鍵**保證，不由呼叫端記得：玩家回看序章重選一次，
第一次的選擇仍然算數（文件 §2.3）。

## 完成時間

`players_story_progress.triggered_at` 本來就記著每個 beat 的完成時間，但
`StoryArcStateResponse` 只回 id 串，時間留在資料庫裡沒有出口。終點 beat 的
時間＝玩家走完主線的時間。
"""
import json
import uuid

import pytest

from app.modules.body import models
from app.modules.body.story_progress import advance_beat, story_variables
from app.modules.brain.models import StoryArc, StoryBeat, StoryString


@pytest.fixture
def branching(db_session, unique_spirit_id):
    """一個帶三選一觀察點的 beat，加一個沒有選項的後續節點。"""
    arc_id = f"arc-{unique_spirit_id}"
    first = f"beat-choice-{unique_spirit_id}"
    second = f"beat-plain-{unique_spirit_id}"
    keys = {name: f"t.{unique_spirit_id}.{name}" for name in ("a", "b", "c", "line")}

    db_session.add(
        StoryArc(
            arc_id=arc_id, title="測試分歧", active=True,
            variables={"story_focus": ["person", "history", "home"]},
        )
    )
    db_session.add_all([
        StoryString(text_key=key, text=f"文字 {name}。", active=True)
        for name, key in keys.items()
    ])
    db_session.flush()

    db_session.add_all([
        StoryBeat(
            beat_id=first, arc_id=arc_id, character_id=None, sequence_order=1,
            trigger_condition="x",
            narrative_directive=json.dumps({
                "nodes": [{
                    "type": "look_points",
                    "options": [
                        {"id": "a", "text_key": keys["a"],
                         "set_once": {"story_focus": "person"}},
                        {"id": "b", "text_key": keys["b"],
                         "set_once": {"story_focus": "history"}},
                        # 這個節點宣告了一個 arc 沒有列進合法值的東西。
                        {"id": "c", "text_key": keys["c"],
                         "set_once": {"story_focus": "銀河"}},
                    ],
                }],
            }),
            prerequisite_beat_ids=None, active=True,
        ),
        StoryBeat(
            beat_id=second, arc_id=arc_id, character_id=None, sequence_order=2,
            trigger_condition="x",
            narrative_directive=json.dumps({
                "nodes": [{"type": "line", "speaker": "年代簿",
                           "text_key": keys["line"]}]
            }),
            prerequisite_beat_ids=[first], active=True,
        ),
    ])
    db_session.commit()

    class Fixture:
        pass

    f = Fixture()
    f.arc_id, f.first, f.second = arc_id, first, second

    yield f

    db_session.query(models.PlayersStoryVariable).filter_by(arc_id=arc_id).delete()
    db_session.query(models.PlayersStoryProgress).filter(
        models.PlayersStoryProgress.beat_id.in_([first, second])
    ).delete(synchronize_session=False)
    db_session.query(StoryBeat).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryArc).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryString).filter(
        StoryString.text_key.in_(list(keys.values()))
    ).delete(synchronize_session=False)
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── 寫入 ────────────────────────────────────────────────────────────


def test_a_choice_is_recorded(db_session, branching, player):
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first, chosen_option_ids=["a"],
    )

    assert result.set_variables == {"story_focus": "person"}
    assert story_variables(
        db_session, player_id=player_id, arc_id=branching.arc_id
    ) == {"story_focus": "person"}


def test_no_choice_records_nothing(db_session, branching, player):
    """多數 beat 沒有選項。不送就是不寫，不是錯誤。"""
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first,
    )

    assert result.set_variables == {}


def test_set_once_is_enforced_by_the_primary_key(db_session, branching, player):
    """
    玩家回看序章重選一次，**第一次的選擇仍然算數**（文件 §2.3）。

    這條規則由 `(player_id, arc_id, variable)` 主鍵 ＋ ON CONFLICT DO NOTHING
    保證，不是靠某段程式碼記得先查再寫。
    """
    player_id, _ = player
    advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first, chosen_option_ids=["a"],
    )

    # 直接再寫一次（模擬回看重選）——beat 已完成，走的是重複提交那條路。
    again = advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first, chosen_option_ids=["b"],
    )

    assert again.already_completed is True
    assert story_variables(
        db_session, player_id=player_id, arc_id=branching.arc_id
    ) == {"story_focus": "person"}


def test_a_value_outside_the_arc_declaration_is_rejected(
    db_session, branching, player
):
    """
    選項 c 寫的 `story_focus: 銀河` 不在 arc 宣告的合法值裡。

    安靜跳過並留在 log 上——那是內容的錯，不該讓已經完成的 beat 回捲。
    """
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first, chosen_option_ids=["c"],
    )

    assert result.set_variables == {}
    assert story_variables(
        db_session, player_id=player_id, arc_id=branching.arc_id
    ) == {}


def test_an_option_id_from_another_beat_is_ignored(db_session, branching, player):
    """
    值來自**內容**，不是來自請求——否則玩家可以自己指定結局。
    """
    player_id, _ = player

    result = advance_beat(
        db_session, player_id=player_id, arc_id=branching.arc_id,
        beat_id=branching.first, chosen_option_ids=["不存在的選項"],
    )

    assert result.set_variables == {}


# ── API ─────────────────────────────────────────────────────────────


def test_endpoint_accepts_choices_and_reports_them(client, branching, player):
    _, token = player

    response = client.post(
        f"/api/v1/story-arcs/{branching.arc_id}/beats/{branching.first}/advance",
        headers=_auth(token),
        json={"chosen_option_ids": ["b"]},
    )

    assert response.status_code == 200
    assert response.json()["set_variables"] == {"story_focus": "history"}


def test_endpoint_works_without_a_body(client, branching, player):
    """整個 body 可以省略——多數 beat 沒有選項。"""
    _, token = player

    response = client.post(
        f"/api/v1/story-arcs/{branching.arc_id}/beats/{branching.first}/advance",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["set_variables"] == {}


def test_arc_state_exposes_variables(client, branching, player):
    _, token = player
    client.post(
        f"/api/v1/story-arcs/{branching.arc_id}/beats/{branching.first}/advance",
        headers=_auth(token), json={"chosen_option_ids": ["a"]},
    )

    body = client.get(
        f"/api/v1/story-arcs/{branching.arc_id}/state", headers=_auth(token)
    ).json()

    assert body["variables"] == {"story_focus": "person"}


# ── 完成時間 ────────────────────────────────────────────────────────


def test_arc_state_reports_when_each_beat_was_completed(client, branching, player):
    """
    `triggered_at` 本來就記著，缺的只是出口。終點 beat 的時間＝走完主線的時間。
    """
    _, token = player
    for beat_id in (branching.first, branching.second):
        client.post(
            f"/api/v1/story-arcs/{branching.arc_id}/beats/{beat_id}/advance",
            headers=_auth(token),
        )

    body = client.get(
        f"/api/v1/story-arcs/{branching.arc_id}/state", headers=_auth(token)
    ).json()

    completed = body["completed_beats"]
    assert [row["beat_id"] for row in completed] == [
        branching.first, branching.second
    ]
    assert all(row["completed_at"] for row in completed)
    # 依完成時間排序——玩家走過的順序才是這串東西的意義。
    assert completed[0]["completed_at"] <= completed[1]["completed_at"]


def test_completed_beat_ids_still_present_for_older_clients(
    client, branching, player
):
    """新欄位是**加上去**的，舊客戶端讀 `completed_beat_ids` 仍然正確。"""
    _, token = player
    client.post(
        f"/api/v1/story-arcs/{branching.arc_id}/beats/{branching.first}/advance",
        headers=_auth(token),
    )

    body = client.get(
        f"/api/v1/story-arcs/{branching.arc_id}/state", headers=_auth(token)
    ).json()

    assert body["completed_beat_ids"] == [branching.first]
