"""
劇情任務的步驟進度（0033），以及對話怎麼指示下一步。

## 這個檔案在擋什麼

劇情任務的 `steps` 一直只是顯示用的文字：沒有地方存「哪一步做完了」，也沒有
任何介面能完成它們。唯一能完成整個任務的端點在 2026-08-22 換 UI 之後失去了
呼叫端。結果是劇情任務永遠停在 `in_progress`，主線第一章卡死——它的第三道門
正是這個任務。
"""

import uuid

import pytest
from sqlalchemy import text

from app.modules.body import quests
from app.modules.brain.prompt_builder import (
    active_quest_for,
    build_user_turn,
    quest_guidance_section,
)

LONGSHAN_QUEST = "q_longshan_repair_trace"
LONGSHAN = "longshan_temple"


@pytest.fixture
def player(db_session):
    player_id = uuid.uuid4()
    db_session.execute(
        text(
            "INSERT INTO players (player_id, device_id, usage_tier_id)"
            " VALUES (:pid, :did, 'closed_beta')"
        ),
        {"pid": player_id, "did": f"test-steps-{player_id.hex[:12]}"},
    )
    db_session.commit()

    yield player_id

    db_session.rollback()
    for table in (
        "dialogue_turns",
        "player_quest_steps",
        "quest_progress",
        "resonance_events",
        "resonance",
    ):
        db_session.execute(
            text(f"DELETE FROM {table} WHERE player_id = :pid"), {"pid": player_id}
        )
    db_session.execute(text("DELETE FROM players WHERE player_id = :pid"), {"pid": player_id})
    db_session.commit()


@pytest.fixture
def in_progress_quest(db_session, player):
    """開一列劇情任務進度，模擬玩家召喚過龍山寺。"""
    db_session.execute(
        text(
            "INSERT INTO quest_progress"
            " (player_id, quest_id, status, progress_value, attempts_today, attempts_date)"
            " VALUES (:pid, :qid, 'in_progress', 0, 0, CURRENT_DATE)"
        ),
        {"pid": player, "qid": LONGSHAN_QUEST},
    )
    db_session.commit()
    return LONGSHAN_QUEST


def _step_ids(db_session):
    return [s["step_id"] for s in quests.quest_steps(db_session, LONGSHAN_QUEST)]


def test_the_quest_has_steps_to_begin_with(db_session):
    """前提檢查：內容真的有匯進來，否則底下的測試全部是空轉。"""
    assert len(_step_ids(db_session)) >= 2


def test_completing_one_step_does_not_complete_the_quest(db_session, player, in_progress_quest):
    steps = _step_ids(db_session)

    result = quests.complete_step(
        db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=steps[0]
    )

    assert result.newly_added is True
    assert result.quest_completed is False
    assert quests.completed_step_ids(
        db_session, player_id=player, quest_id=LONGSHAN_QUEST
    ) == {steps[0]}


def test_completing_every_step_completes_the_quest(db_session, player, in_progress_quest):
    """
    🔒 全部做完就自動完成任務。

    玩家不必再按一次「完成任務」——有步驟又有完成鈕等於「做完了沒」有兩個定義。
    """
    steps = _step_ids(db_session)

    results = [
        quests.complete_step(
            db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=step_id
        )
        for step_id in steps
    ]

    assert [r.quest_completed for r in results[:-1]] == [False] * (len(steps) - 1)
    assert results[-1].quest_completed is True

    status = db_session.execute(
        text("SELECT status FROM quest_progress WHERE player_id = :pid AND quest_id = :qid"),
        {"pid": player, "qid": LONGSHAN_QUEST},
    ).scalar()
    assert status == quests.STATUS_COMPLETED


def test_repeating_a_step_is_not_an_error(db_session, player, in_progress_quest):
    """重複提交是正常的使用者行為（網路重試、連點兩下），不是錯誤。"""
    steps = _step_ids(db_session)
    quests.complete_step(
        db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=steps[0]
    )

    repeat = quests.complete_step(
        db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=steps[0]
    )

    assert repeat.newly_added is False


def test_an_unknown_step_is_rejected(db_session, player, in_progress_quest):
    """
    🔒 step_id 沒有外鍵（步驟在 JSONB 裡），有效性只能靠寫入端擋。

    少了這道檢查，打錯字的 step_id 會安靜地寫進去，而任務永遠湊不齊——
    症狀是「我明明三步都做了卻沒完成」。
    """
    with pytest.raises(quests.StepNotFoundError):
        quests.complete_step(
            db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id="no_such_step"
        )


def test_a_player_without_progress_is_rejected(db_session, player):
    """任務是在 /summon 時建立的；沒召喚過就沒有進度列。"""
    steps = _step_ids(db_session)

    with pytest.raises(quests.QuestNotFoundError):
        quests.complete_step(
            db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=steps[0]
        )


# ── 對話指示 ─────────────────────────────────────────────────────────


def test_active_quest_points_at_the_first_pending_step(db_session, player, in_progress_quest):
    steps = _step_ids(db_session)
    quests.complete_step(
        db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=steps[0]
    )

    active = active_quest_for(db_session, player_id=player, spirit_id=LONGSHAN)

    assert active is not None
    assert active["step"]["step_id"] == steps[1]


def test_no_guidance_once_every_step_is_done(db_session, player, in_progress_quest):
    """做完就不要再提。少了這一條，靈魂會一直叫玩家去看已經看過的東西。"""
    for step_id in _step_ids(db_session):
        quests.complete_step(
            db_session, player_id=player, quest_id=LONGSHAN_QUEST, step_id=step_id
        )

    assert active_quest_for(db_session, player_id=player, spirit_id=LONGSHAN) is None


def test_guidance_never_uses_system_words():
    """
    🔒 指示裡不准出現系統詞彙。

    玩家收到的應該是一個角色請他去看某樣東西，不是待辦清單。這一條是文案規則，
    不是實作細節——改寫那段文字時要一起看這個測試。
    """
    text_out = quest_guidance_section(
        {"title": "重建的痕跡", "step": {"title": "屋脊上的痕跡", "hint": "抬頭看正殿屋脊。"}}
    )

    assert text_out is not None
    # 「不要說出…」那句指示本身會提到這些詞，所以只檢查給模型的敘述部分。
    body = text_out.split("如果這一輪")[0]
    for word in ("任務", "步驟", "完成", "進度"):
        assert word not in body


def test_guidance_is_absent_when_there_is_nothing_to_do():
    assert quest_guidance_section(None) is None
    assert quest_guidance_section({"title": "x", "step": {}}) is None


def test_guidance_sits_before_the_player_line():
    """
    🔒 指示要放在玩家這句話**之前**。

    放在後面模型會把它當成最新指令，每一輪都硬把話題轉回任務——那正是
    「靈魂變成任務發布機」的樣子。
    """
    turn = build_user_turn(
        user_input="這裡以前是什麼樣子？",
        active_quest={"title": "重建的痕跡", "step": {"title": "屋脊", "hint": "抬頭看。"}},
    )

    assert turn.index("你想讓這位玩家去看的東西") < turn.index("玩家現在說")


# ── 提幾次就停 ───────────────────────────────────────────────────────


def _say(db_session, player_id, spirit_id, text_body):
    """記一輪玩家發言，模擬對話端點寫進 dialogue_turns 的那一筆。"""
    db_session.execute(
        text(
            "INSERT INTO dialogue_turns (player_id, spirit_id, role, content)"
            " VALUES (:pid, :sid, 'player', :content)"
        ),
        {"pid": player_id, "sid": spirit_id, "content": text_body},
    )
    db_session.commit()


def test_guidance_stops_after_a_few_turns(db_session, player, in_progress_quest):
    """
    🔒 提過 GUIDANCE_TURN_LIMIT 輪就不再提。

    實測（2026-08-23，龍山寺 v5）模型把「話題走得過去再提」讀成「每一輪都要
    提」：玩家講工作累、講天氣熱，回話還是繞回屋簷，四輪全中。靠措辭讓模型
    自律沒有用，所以要有一道確定性的上限。

    玩家不會因此沒事做——年代簿裡隨時查得到還差哪一步。
    """
    from app.modules.brain.prompt_builder import GUIDANCE_TURN_LIMIT

    assert active_quest_for(db_session, player_id=player, spirit_id=LONGSHAN) is not None

    for i in range(GUIDANCE_TURN_LIMIT):
        _say(db_session, player, LONGSHAN, f"第 {i} 句")

    assert active_quest_for(db_session, player_id=player, spirit_id=LONGSHAN) is None


def test_turns_with_other_spirits_do_not_count(db_session, player, in_progress_quest):
    """跟故宮聊天不該用掉龍山寺的提醒次數。"""
    from app.modules.brain.prompt_builder import GUIDANCE_TURN_LIMIT

    for i in range(GUIDANCE_TURN_LIMIT + 2):
        _say(db_session, player, "national_palace_museum", f"第 {i} 句")

    assert active_quest_for(db_session, player_id=player, spirit_id=LONGSHAN) is not None
