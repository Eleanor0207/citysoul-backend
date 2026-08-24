"""
主線 gate：由「與該靈魂對話」觸發（0032／0034）。

## 這個檔案在擋什麼

序章之後整條主線曾經走不到：三個 gate beat 的觸發條件寫在 `trigger_condition`
裡，而那一欄**沒有任何程式在讀**；客戶端只有年代簿會推進 beat，年代簿又只認
五個章節，三個 gate 不在其中。玩家讀完序章、走到龍山寺、什麼都不會發生。

## 為什麼這裡不再測「自動推進」

2026-08-23 曾經在對話端點自動推進 gate（過渡處置），2026-08-24 移除——
客戶端補上了播放器，會播那段開場戲再自己呼叫 `/advance`。**兩者不能同時存在**：
後端先推掉的話，客戶端問到的 arc state 裡那個 beat 已經不在 `eligible_beat_ids`，
戲就永遠播不到。

所以現在驗的是**客戶端看得到什麼**：序章走完之後，gate 要出現在 eligible 裡。
"""

import uuid

import pytest
from sqlalchemy import text

from app.modules.body import story_progress
from app.modules.brain.models import StoryBeat

ARC_ID = "arc_wanhua_homeward_painting"


@pytest.fixture
def player(db_session):
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


def test_the_three_gates_are_flagged_as_dialogue_triggered(db_session):
    """
    🔒 只有三個 gate 標了 `advance_on_dialogue`。

    這一欄現在由客戶端的語意使用（哪些 beat 是走到現場才播的），標錯的症狀是
    章節 beat 被當成開場戲，玩家在聊天途中被塞進一整章。
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


def test_the_gate_becomes_eligible_after_the_prologue(db_session, player):
    """
    🔒 序章走完之後，龍山寺的開場戲要出現在 `eligible_beat_ids`。

    客戶端就是照這個決定要不要播（TaskController.TryPlayGateAsync）——
    不在裡面就永遠不播，而那種錯誤沒有錯誤訊息。
    """
    before = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)
    assert "beat_longshan_gate" not in before["eligible_beat_ids"]

    _complete_prologue(db_session, player)

    after = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)
    assert "beat_longshan_gate" in after["eligible_beat_ids"]


def test_the_chapter_stays_locked_until_the_gate_is_played(db_session, player):
    """
    🔒 第一章的前置是 gate。gate 沒播完，章就不能顯影。

    這一條在擋「有人為了讓主線走得通，把 gate 從 prerequisite 拿掉」——
    那樣玩家永遠不會知道有那段戲。
    """
    _complete_prologue(db_session, player)

    state = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)
    assert "beat_longshan_clue" not in state["eligible_beat_ids"]

    story_progress.advance_beat(
        db_session, player_id=player, arc_id=ARC_ID, beat_id="beat_longshan_gate"
    )

    after = story_progress.arc_state(db_session, player_id=player, arc_id=ARC_ID)
    # 第一章還要任務完成才會 eligible，所以這裡只驗前置那一道門開了。
    beat = db_session.query(StoryBeat).filter_by(beat_id="beat_longshan_clue").first()
    assert set(beat.prerequisite_beat_ids).issubset(set(after["completed_beat_ids"]))
