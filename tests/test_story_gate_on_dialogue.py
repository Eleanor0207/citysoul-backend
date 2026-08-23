"""
主線 gate 由「與該靈魂對話」推進（0032）。

## 這個檔案在擋什麼

序章之後整條主線曾經走不到：三個 gate beat 的觸發條件寫在 `trigger_condition`
裡，而那一欄**沒有任何程式在讀**；客戶端只有年代簿會推進 beat，年代簿又只認
五個章節，三個 gate 不在其中。玩家讀完序章、走到龍山寺、什麼都不會發生。

所以這裡驗的是「鏈條能不能走完」，不是單一函式的輸入輸出。
"""

import uuid

import pytest
from sqlalchemy import text

from app.modules.body import story_progress
from app.modules.brain.models import StoryBeat

ARC_ID = "arc_wanhua_homeward_painting"
LONGSHAN = "longshan_temple"


@pytest.fixture
def player(db_session):
    """一個只存在於這個測試的玩家，測完連進度與道具一起清掉。"""
    player_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO players (player_id, device_id, usage_tier_id)"
            " VALUES (:pid, :did, 'closed_beta')"
        ),
        {"pid": player_id, "did": f"test-gate-{player_id.hex[:12]}"},
    )
    db_session.commit()

    yield player_id

    db_session.rollback()
    for table in (
        "players_story_variables",
        "players_story_progress",
        "player_inventory",
        "quest_progress",
    ):
        db_session.execute(
            text(f"DELETE FROM {table} WHERE player_id = :pid"), {"pid": player_id}
        )
    db_session.execute(text("DELETE FROM players WHERE player_id = :pid"), {"pid": player_id})
    db_session.commit()


def _complete_prologue(db_session, player_id):
    """走完序章：推進 beat_prologue_letter，它會發 item_wanhua_letter。"""
    return story_progress.advance_beat(
        db_session,
        player_id=player_id,
        arc_id=ARC_ID,
        beat_id="beat_prologue_letter",
        chosen_option_ids=["look_history"],
    )


def test_the_three_gates_are_the_only_dialogue_advanced_beats(db_session):
    """
    🔒 只有三個 gate 標了 `advance_on_dialogue`。

    章節 beat 一旦被標成 true，玩家不必在年代簿讀劇本、不必做選擇就會自己推進，
    而那些選擇會寫進 `players_story_variables`——症狀是結局分歧默默失效。
    """
    flagged = {
        beat.beat_id
        for beat in db_session.query(StoryBeat)
        .filter_by(arc_id=ARC_ID, advance_on_dialogue=True)
        .all()
    }

    assert flagged == {
        "beat_longshan_gate",
        "beat_redhouse_gate",
        "beat_bopiliao_gate",
    }


def test_talking_to_longshan_advances_the_gate(db_session, player):
    """序章之後跟龍山寺講到話，第一章的前置就補上了。"""
    _complete_prologue(db_session, player)

    advanced = story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id=LONGSHAN
    )

    assert advanced == ["beat_longshan_gate"]

    state = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)
    assert "beat_longshan_gate" in state["completed_beat_ids"]


def test_the_gate_needs_the_prologue_first(db_session, player):
    """
    🔒 沒讀序章就跑去龍山寺，什麼都不該發生。

    gate 的前置是序章＋那封信。少了這道檢查，玩家可以跳過序章直接開始，
    而序章正是寫入 `story_focus` 的地方。
    """
    advanced = story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id=LONGSHAN
    )

    assert advanced == []


def test_talking_again_does_not_advance_twice(db_session, player):
    """每輪對話都會呼叫這一支，已經推過的 beat 不能再推一次。"""
    _complete_prologue(db_session, player)
    story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id=LONGSHAN
    )

    again = story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id=LONGSHAN
    )

    assert again == []


def test_an_unknown_spirit_is_not_an_error(db_session, player):
    """
    對話端點每輪都會叫這一支，查無此靈魂不該讓整支端點失敗——
    跟 `quests.complete_on_dialogue()` 同一個失敗風格。
    """
    assert story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id="no-such-spirit"
    ) == []


def test_a_spirit_without_a_gate_advances_nothing(db_session, player):
    """故宮不在萬華主線上，跟祂講話不該推進任何東西。"""
    _complete_prologue(db_session, player)

    assert story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id="national_palace_museum"
    ) == []


def test_the_gate_does_not_skip_the_chapter(db_session, player):
    """
    🔒 gate 推進之後，第一章仍然要玩家自己去讀。

    這一支在擋「自動推進擴散」：如果哪天有人把章節 beat 也標成
    `advance_on_dialogue`，玩家講一句話就會把整條主線走完，而且不會有人立刻發現。
    """
    _complete_prologue(db_session, player)
    story_progress.advance_gates_on_dialogue(
        db_session, player_id=player, spirit_id=LONGSHAN
    )

    state = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)

    assert "beat_longshan_clue" not in state["completed_beat_ids"]
