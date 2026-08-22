"""劇情台詞的出口：`GET /story-arcs/{arcId}/beats/{beatId}/script`（backend#72）。

在這支端點之前，資料庫裡有完整的序章——`narrative_directive` 存著節點、
`brain.story_strings` 存著文字——但**沒有任何出口**。客戶端拿得到
`beat_prologue_letter` 這個 id，拿不到它的任何一個字。

這裡釘住四件事：

1. 台詞與選項真的解析得出來（含真實的萬華序章資料）
2. **鎖著的 beat 回 403**——腳本就是劇情內容，能任意查等於能先看完結局
3. 缺字串時的降級：台詞跳過，但**半組選項整組跳過**
4. `jump` 與 `target` 不輸出——那些目標節點從來沒有被寫出來（文件附錄 B）
"""
import json
import uuid

import pytest

from app.modules.body import models
from app.modules.body.story_progress import advance_beat
from app.modules.body.story_script import build_script
from app.modules.brain.models import StoryArc, StoryBeat, StoryString

ARC = "arc_wanhua_homeward_painting"
PROLOGUE = "beat_prologue_letter"


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── 真實的萬華序章 ──────────────────────────────────────────────────


def test_the_real_prologue_resolves_to_readable_text(db_session, client, player):
    """
    用**線上那份**萬華序章驗一次，不是自己造的假資料。

    這條測試相依匯入過的內容（`import_story_arcs` ＋ `import_story_strings`）。
    內容沒匯入時 skip——那是環境沒準備好，不是這支端點壞了。
    """
    beat = db_session.query(StoryBeat).filter_by(beat_id=PROLOGUE, active=True).first()
    if beat is None:
        pytest.skip("萬華 arc 還沒匯入這個資料庫")

    script = build_script(db_session, beat)

    lines = [node for node in script.nodes if node.type == "line"]
    choices = [node for node in script.nodes if node.type == "choice"]

    # 兩句台詞（年代簿的場景描述 ＋ 信的內文），一組三個觀察點。
    assert [line.speaker for line in lines] == ["年代簿", "信"]
    assert all(line.text for line in lines)
    assert len(choices) == 1
    assert len(choices[0].options) == 3

    # 三個觀察點各自寫進不同的 story_focus——那是結局分歧的來源。
    assert sorted(
        option.sets.get("story_focus") for option in choices[0].options
    ) == ["history", "home", "person"]

    # 文件 §2.3 拍板「不強制看完三個」。
    assert choices[0].min_viewed_to_proceed == 1
    assert choices[0].exclusive is False


def test_jump_nodes_are_not_returned(db_session):
    """
    序章的最後一個節點是 `{"type": "jump", "target": "prologue_merge"}`，而
    `prologue_merge` **從來沒有被寫出來**（文件附錄 B）。

    原樣送出去等於邀請客戶端去跳一個不存在的節點。
    """
    beat = db_session.query(StoryBeat).filter_by(beat_id=PROLOGUE, active=True).first()
    if beat is None:
        pytest.skip("萬華 arc 還沒匯入這個資料庫")

    script = build_script(db_session, beat)

    assert all(node.type in {"line", "choice"} for node in script.nodes)


# ── 造出來的最小資料：降級與把關 ────────────────────────────────


@pytest.fixture
def scripted(db_session, unique_spirit_id):
    """一條兩節的 arc，第二節鎖著。字串刻意留一個缺口。"""
    arc_id = f"arc-{unique_spirit_id}"
    first = f"beat-first-{unique_spirit_id}"
    locked = f"beat-locked-{unique_spirit_id}"
    key_line = f"t.{unique_spirit_id}.line"
    key_a = f"t.{unique_spirit_id}.a"
    key_b = f"t.{unique_spirit_id}.b"
    key_missing = f"t.{unique_spirit_id}.missing"

    db_session.add(StoryArc(arc_id=arc_id, title="測試腳本", active=True))
    db_session.add_all([
        StoryString(text_key=key_line, text="一句台詞。", active=True),
        StoryString(text_key=key_a, text="選項甲。", active=True),
        StoryString(text_key=key_b, text="選項乙。", active=True),
    ])
    db_session.flush()

    db_session.add_all([
        StoryBeat(
            beat_id=first, arc_id=arc_id, character_id=None, sequence_order=1,
            trigger_condition="x",
            narrative_directive=json.dumps({
                "nodes": [
                    {"type": "line", "speaker": "年代簿", "text_key": key_line},
                    # 這一句的 text_key 查不到 → 只跳過這一句。
                    {"type": "line", "speaker": "年代簿", "text_key": key_missing},
                    {
                        "type": "look_points", "exclusive": False,
                        "min_viewed_to_proceed": 2,
                        "options": [
                            {"id": "a", "text_key": key_a,
                             "set_once": {"story_focus": "person"}},
                            {"id": "b", "text_key": key_b},
                        ],
                    },
                    # 這一組有一個 option 查不到 → 整組跳過。
                    {
                        "type": "look_points",
                        "options": [
                            {"id": "c", "text_key": key_a},
                            {"id": "d", "text_key": key_missing},
                        ],
                    },
                    {"type": "jump", "target": "nowhere"},
                ],
                "commands": [{"show_info_card": "card_test"}],
            }),
            prerequisite_beat_ids=None, active=True,
        ),
        StoryBeat(
            beat_id=locked, arc_id=arc_id, character_id=None, sequence_order=2,
            trigger_condition="x",
            narrative_directive=json.dumps({
                "nodes": [{"type": "line", "speaker": "年代簿", "text_key": key_line}]
            }),
            prerequisite_beat_ids=[first], active=True,
        ),
    ])
    db_session.commit()

    class Fixture:
        pass

    f = Fixture()
    f.arc_id, f.first, f.locked = arc_id, first, locked

    yield f

    db_session.query(models.PlayersStoryProgress).filter(
        models.PlayersStoryProgress.beat_id.in_([first, locked])
    ).delete(synchronize_session=False)
    db_session.query(StoryBeat).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryArc).filter_by(arc_id=arc_id).delete()
    db_session.query(StoryString).filter(
        StoryString.text_key.in_([key_line, key_a, key_b])
    ).delete(synchronize_session=False)
    db_session.commit()


def test_a_missing_line_is_skipped_not_fatal(db_session, scripted):
    """缺字串是內容錯誤，但不該讓整段劇情打不開。"""
    beat = db_session.query(StoryBeat).filter_by(beat_id=scripted.first).one()

    script = build_script(db_session, beat)

    lines = [node for node in script.nodes if node.type == "line"]
    assert [line.text for line in lines] == ["一句台詞。"]


def test_a_half_resolved_choice_is_dropped_entirely(db_session, scripted):
    """
    半組選項比沒有選項更糟——玩家看不出少了什麼，只會覺得選擇很奇怪。

    第一組（兩個都查得到）留下，第二組（一個查不到）整組不見。
    """
    beat = db_session.query(StoryBeat).filter_by(beat_id=scripted.first).one()

    script = build_script(db_session, beat)

    choices = [node for node in script.nodes if node.type == "choice"]
    assert len(choices) == 1
    assert [option.option_id for option in choices[0].options] == ["a", "b"]
    assert choices[0].min_viewed_to_proceed == 2


def test_an_unknown_info_card_is_skipped(db_session, scripted):
    """
    `show_info_card: card_test` 指向一張沒有定義的卡片（0030 之前卡片根本沒有
    進資料庫，這種指向是常態）。跳過並留在 log 上，不讓整段腳本壞掉。
    """
    beat = db_session.query(StoryBeat).filter_by(beat_id=scripted.first).one()

    assert build_script(db_session, beat).info_cards == []


def test_a_real_info_card_carries_both_texts(db_session):
    """
    史實與虛構是兩個欄位，後端不合併——文件 §1 要求每張卡明確區分兩者。
    """
    beat = db_session.query(StoryBeat).filter_by(
        beat_id="beat_longshan_clue", active=True
    ).first()
    if beat is None:
        pytest.skip("萬華 arc 還沒匯入這個資料庫")

    cards = build_script(db_session, beat).info_cards
    if not cards:
        pytest.skip("資訊卡還沒匯入這個資料庫")

    card = cards[0]
    assert card.card_id == "card_longshan_rebuild"
    assert card.historical_text and card.fiction_text
    assert card.historical_text != card.fiction_text


def test_unparsable_directive_yields_an_empty_script(db_session, scripted):
    """舊資料的 narrative_directive 是純文字敘事指令，不是 JSON。"""
    beat = db_session.query(StoryBeat).filter_by(beat_id=scripted.first).one()
    beat.narrative_directive = "這是一段敘事指令，不是 JSON"
    db_session.commit()

    script = build_script(db_session, beat)

    assert script.nodes == []
    assert script.info_cards == []


# ── 端點與把關 ──────────────────────────────────────────────────────


def test_endpoint_returns_the_script_for_an_eligible_beat(client, scripted, player):
    _, token = player

    response = client.get(
        f"/api/v1/story-arcs/{scripted.arc_id}/beats/{scripted.first}/script",
        headers=_auth(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["beat_id"] == scripted.first
    assert body["nodes"][0]["type"] == "line"
    assert body["nodes"][0]["text"] == "一句台詞。"


def test_a_locked_beat_is_forbidden(client, scripted, player):
    """
    腳本就是劇情內容本身。能任意查等於能先把結局看完——這跟
    `eligible_beat_ids` 不列出未解鎖節點是同一條規則。
    """
    _, token = player

    response = client.get(
        f"/api/v1/story-arcs/{scripted.arc_id}/beats/{scripted.locked}/script",
        headers=_auth(token),
    )

    assert response.status_code == 403
    assert "locked" in response.json()["detail"]


def test_a_completed_beat_can_be_reread(client, db_session, scripted, player):
    """玩家本來就讀過了，回看不該被擋。"""
    player_id, token = player
    advance_beat(
        db_session, player_id=player_id, arc_id=scripted.arc_id,
        beat_id=scripted.first,
    )

    response = client.get(
        f"/api/v1/story-arcs/{scripted.arc_id}/beats/{scripted.first}/script",
        headers=_auth(token),
    )

    assert response.status_code == 200


def test_beat_from_another_arc_is_not_found(client, scripted, player):
    """URL 上的 arc_id 與 beat_id 對不起來，跟 beat 不存在是同一種「查無此節點」。"""
    _, token = player

    response = client.get(
        f"/api/v1/story-arcs/arc-does-not-exist/beats/{scripted.first}/script",
        headers=_auth(token),
    )

    assert response.status_code == 404


def test_script_requires_a_session_token(client, scripted):
    response = client.get(
        f"/api/v1/story-arcs/{scripted.arc_id}/beats/{scripted.first}/script"
    )

    assert response.status_code == 401
