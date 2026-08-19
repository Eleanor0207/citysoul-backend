"""
Ticket #16．共鳴值資料表與判定邏輯（S5）。

驗收標準對照見 GitHub issue #16。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.body import models
from app.modules.body.resonance import (
    AMOUNT_DAILY_ENCOUNTER,
    AMOUNT_STORY_BEAT,
    RESONANCE_THRESHOLDS,
    SOURCE_DAILY_ENCOUNTER,
    SOURCE_STORY_BEAT,
    apply_resonance,
    award_daily_encounter,
    award_story_beat,
    daily_encounter_source_id,
    next_threshold,
    stage_for_value,
)


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="測試地標",
        latitude=25.0955,
        longitude=121.5186,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=row.spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=row.spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"])


def _award(
    db_session,
    player_id,
    spirit_id,
    *,
    source_type=SOURCE_STORY_BEAT,
    source_id=None,
    amount=20,
):
    return apply_resonance(
        db_session,
        player_id=player_id,
        spirit_id=spirit_id,
        source_type=source_type,
        source_id=source_id or f"src-{uuid.uuid4()}",
        amount=amount,
    )


# ── 純函式：門檻換算 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "value,expected_stage",
    [(0, 0), (9, 0), (10, 1), (39, 1), (40, 2), (99, 2), (100, 3), (250, 3)],
)
def test_stage_for_value(value, expected_stage):
    """門檻 10/40/100 含邊界（剛好等於門檻就算達標）。"""
    assert stage_for_value(value) == expected_stage


@pytest.mark.parametrize(
    "value,expected", [(0, 10), (9, 10), (10, 40), (39, 40), (40, 100), (99, 100), (100, None)]
)
def test_next_threshold(value, expected):
    assert next_threshold(value) == expected


def test_thresholds_match_context_definition():
    """守門測試：門檻是 CONTEXT.md 明訂的共用固定值，不該被隨手改掉。"""
    assert RESONANCE_THRESHOLDS == (10, 40, 100)


# ── 基本入帳 ───────────────────────────────────────────────────────────

def test_first_award_creates_row(db_session, player, spirit):
    result = _award(db_session, player, spirit.spirit_id, amount=20)

    assert result.awarded is True
    assert result.resonance_value == 20
    assert result.stage == 1

    row = db_session.query(models.Resonance).filter_by(
        player_id=player, spirit_id=spirit.spirit_id
    ).one()
    assert row.resonance_value == 20
    # 階段不存在資料庫裡（0003 刪掉了 stage 欄位）。這一列只該有 value；
    # 階段永遠是從 value 算出來的。
    assert not hasattr(row, "stage")
    assert stage_for_value(row.resonance_value) == 1


def test_awards_accumulate(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, amount=20)
    result = _award(db_session, player, spirit.spirit_id, amount=20)

    assert result.resonance_value == 40
    assert result.awarded is True


def test_event_ledger_records_each_award(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, source_id="quest-a")
    _award(db_session, player, spirit.spirit_id, source_id="quest-b")

    count = db_session.query(models.ResonanceEvent).filter_by(
        player_id=player, spirit_id=spirit.spirit_id
    ).count()
    assert count == 2


# ── 重複入帳 ───────────────────────────────────────────────────────────

def test_same_source_is_not_credited_twice(db_session, player, spirit):
    """AC：同一 source_type + source_id 重複呼叫不重複加值。"""
    first = _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    second = _award(db_session, player, spirit.spirit_id, source_id="quest-001")

    assert first.awarded is True
    assert second.awarded is False
    assert second.resonance_value == first.resonance_value  # 沒有再加上去


def test_duplicate_does_not_raise(db_session, player, spirit):
    """
    AC 明講「不是讓例外往外拋炸掉呼叫端」。重複提交是正常的使用者行為
    （網路重試、連點兩下），不是錯誤。
    """
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")  # 不該拋


def test_session_still_usable_after_duplicate(db_session, player, spirit):
    """
    撞到 UNIQUE 之後 session 要還能用——這是用 savepoint 而不是讓整個
    transaction 髒掉的理由。後面接著入帳另一筆來源必須成功。
    """
    _award(db_session, player, spirit.spirit_id, source_id="quest-001", amount=20)
    _award(db_session, player, spirit.spirit_id, source_id="quest-001", amount=20)

    result = _award(db_session, player, spirit.spirit_id, source_id="quest-002", amount=20)

    assert result.awarded is True
    assert result.resonance_value == 40


def test_duplicate_writes_no_extra_ledger_row(db_session, player, spirit):
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")
    _award(db_session, player, spirit.spirit_id, source_id="quest-001")

    count = db_session.query(models.ResonanceEvent).filter_by(
        player_id=player, source_id="quest-001"
    ).count()
    assert count == 1


def test_same_source_id_different_source_type_is_a_separate_award(db_session, player, spirit):
    """UNIQUE 是 (player_id, source_type, source_id)，type 不同就是不同來源。"""
    first = _award(
        db_session, player, spirit.spirit_id, source_type=SOURCE_STORY_BEAT, source_id="X", amount=20
    )
    second = _award(
        db_session,
        player,
        spirit.spirit_id,
        source_type=SOURCE_DAILY_ENCOUNTER,
        source_id="X",
        amount=10,
    )

    assert first.awarded is True
    assert second.awarded is True
    assert second.resonance_value == 30


def test_same_source_is_blocked_across_spirits(db_session, player, spirit, client):
    """
    SDD 的 UNIQUE **不含 spirit_id**，所以同一個 player 的同一個 source_id
    跨靈魂也只能入帳一次。這條容易被誤以為是 bug，用測試把規格釘住。
    """
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        first = _award(db_session, player, spirit.spirit_id, source_id="shared-id")
        second = _award(db_session, player, other.spirit_id, source_id="shared-id")

        assert first.awarded is True
        assert second.awarded is False
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()


# ── 門檻解鎖 ───────────────────────────────────────────────────────────

def test_crossing_first_threshold_reports_unlock(db_session, player, spirit):
    """0 → 10：跨過第一個門檻。"""
    result = _award(
        db_session, player, spirit.spirit_id, amount=10
    )

    assert result.newly_unlocked_stages == [1]
    assert result.has_new_unlock is True


def test_not_crossing_a_threshold_reports_no_unlock(db_session, player, spirit):
    """
    10 → 30：已經在 stage 1，還沒到 40，不該回報新解鎖。
    """
    _award(db_session, player, spirit.spirit_id, amount=10, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=20, source_id="b")

    assert result.resonance_value == 30
    assert result.stage == 1
    assert result.newly_unlocked_stages == []
    assert result.has_new_unlock is False


def test_below_first_threshold_reports_no_unlock(db_session, player, spirit):
    result = _award(db_session, player, spirit.spirit_id, amount=5)

    assert result.stage == 0
    assert result.newly_unlocked_stages == []


def test_crossing_second_threshold(db_session, player, spirit):
    """30 → 50：跨過 40。"""
    _award(db_session, player, spirit.spirit_id, amount=30, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=20, source_id="b")

    assert result.stage == 2
    assert result.newly_unlocked_stages == [2]


def test_crossing_multiple_thresholds_reports_each(db_session, player, spirit):
    """
    5 → 55 一次跨過 10 與 40，兩個 stage 都要回報。

    MVP 的 10／20 點跨不到兩個門檻，但每個新 stage 都該有自己的一段敘事，
    只回報最後一個會讓中間那段靜默消失——不會有任何錯誤訊息提醒。
    """
    _award(db_session, player, spirit.spirit_id, amount=5, source_id="a")
    result = _award(db_session, player, spirit.spirit_id, amount=50, source_id="b")

    assert result.resonance_value == 55
    assert result.stage == 2
    assert result.newly_unlocked_stages == [1, 2]


def test_duplicate_award_reports_no_unlock(db_session, player, spirit):
    """重複入帳沒有加值，自然也不該回報解鎖。"""
    first = _award(db_session, player, spirit.spirit_id, amount=10, source_id="dup")
    assert first.newly_unlocked_stages == [1]

    second = _award(db_session, player, spirit.spirit_id, amount=10, source_id="dup")
    assert second.newly_unlocked_stages == []
    assert second.stage == 1


# ── 兩種來源（A.L. 2026-08-19 定案的新規則）───────────────────────────
#
# 舊規則是 quest(+20) 與 encounter_collection(+10)，兩者都已移除。
# 下面測的是新的兩條：每日召喚 +10、劇本節點 +30。


def test_rule_amounts_match_spec():
    """
    🔒 守門測試：規則值。

    改這兩個數字等於改成長曲線——門檻 (10, 40, 100) 是配著它們定的
    （10 ＝ 第一天召喚、40 ＝ 走完劇本、100 ＝ 劇本後再回訪六天）。
    要改請連同 `resonance.py` 模組 docstring 那張表一起改。
    """
    assert AMOUNT_DAILY_ENCOUNTER == 10
    assert AMOUNT_STORY_BEAT == 30


def test_old_sources_are_gone():
    """
    🔒 舊的 source_type 常數不該再存在。

    留著的話，接劇本時很容易順手 import 一個「看起來對」的名字，而它的
    去重語意跟新規則不一樣（見 `award_story_beat` 的 docstring）。
    """
    import app.modules.body.resonance as r

    for name in ("SOURCE_QUEST", "SOURCE_ENCOUNTER_COLLECTION", "AMOUNT_QUEST"):
        assert not hasattr(r, name), f"{name} 應該已經隨舊規則移除"


# ── 每日召喚 +10 ──────────────────────────────────────────────────────

def test_daily_encounter_awards_ten(db_session, player, spirit):
    result = award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id)

    assert result.awarded is True
    assert result.resonance_value == 10
    assert result.stage == 1  # 第一天召喚就跨過第一道門檻


def test_daily_encounter_is_once_per_day(db_session, player, spirit):
    """同一天第二次召喚不加分，而且**不是錯誤**。"""
    award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id)
    second = award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id)

    assert second.awarded is False
    assert second.resonance_value == 10


def test_daily_encounter_awards_again_next_day(db_session, player, spirit):
    """跨過台北午夜就是新的一天，可以再拿一次。"""
    day1 = datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc)   # 台北 8/19 12:00
    day2 = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)   # 台北 8/20 12:00

    award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id, now=day1)
    result = award_daily_encounter(
        db_session, player_id=player, spirit_id=spirit.spirit_id, now=day2
    )

    assert result.awarded is True
    assert result.resonance_value == 20


def test_daily_encounter_boundary_is_taipei_midnight(db_session, player, spirit):
    """
    🔴 換日發生在**台北**午夜，不是 UTC 午夜。

    UTC 16:00 = 台北隔天 00:00。所以這兩個時間點分屬不同的台北日期，
    即使它們在 UTC 是同一天。用 UTC 當基準的話這個測試會紅。
    """
    before = datetime(2026, 8, 19, 15, 59, tzinfo=timezone.utc)  # 台北 8/19 23:59
    after = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)    # 台北 8/20 00:00

    award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id, now=before)
    result = award_daily_encounter(
        db_session, player_id=player, spirit_id=spirit.spirit_id, now=after
    )

    assert result.awarded is True, "跨過台北午夜就該是新的一天"
    assert result.resonance_value == 20


def test_daily_encounter_is_per_spirit_on_the_same_day(db_session, player, spirit, client):
    """
    🔴 **這是整批最重要的一條。**

    `uq_resonance_events_source` 是 `UNIQUE(player_id, source_type, source_id)`，
    **沒有 spirit_id**。如果 `source_id` 只放日期，玩家當天在第一個地標拿了 +10
    之後，走到第二個地標就會撞約束、拿不到分——而且不會有任何錯誤訊息，
    只會安靜地少加十點。
    """
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    same_moment = datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc)
    try:
        first = award_daily_encounter(
            db_session, player_id=player, spirit_id=spirit.spirit_id, now=same_moment
        )
        second = award_daily_encounter(
            db_session, player_id=player, spirit_id=other.spirit_id, now=same_moment
        )

        assert first.awarded is True
        assert second.awarded is True, "同一天在不同地標各該拿一次"
        # 各自累計，不共用同一條 resonance 列
        assert first.resonance_value == 10
        assert second.resonance_value == 10
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()


def test_daily_encounter_source_id_contains_spirit_and_date():
    """去重鍵的形狀本身也守住——拼錯了不會有錯誤訊息，只會靜默算錯。"""
    moment = datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc)

    assert daily_encounter_source_id("longshan_temple", now=moment) == "longshan_temple:2026-08-19"


# ── 劇本節點 +30 ──────────────────────────────────────────────────────

def test_story_beat_awards_thirty(db_session, player, spirit):
    result = award_story_beat(
        db_session, player_id=player, spirit_id=spirit.spirit_id, beat_id="wanhua_beat_1"
    )

    assert result.awarded is True
    assert result.resonance_value == 30


def test_story_beat_is_once_per_lifetime(db_session, player, spirit):
    award_story_beat(
        db_session, player_id=player, spirit_id=spirit.spirit_id, beat_id="wanhua_beat_1"
    )
    second = award_story_beat(
        db_session, player_id=player, spirit_id=spirit.spirit_id, beat_id="wanhua_beat_1"
    )

    assert second.awarded is False
    assert second.resonance_value == 30


def test_story_beat_id_is_global_not_per_spirit(db_session, player, spirit, client):
    """
    🔴 跟每日入帳**刻意相反**：同一個 beat_id 在不同 spirit 下仍算同一筆。

    `beat_id` 是 `story_beats` 的主鍵、全域唯一，「一輩子一次」要擋的正是
    「同一個節點被算了兩次」。這條紅了代表 source_id 被加上了 spirit 前綴。
    """
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        award_story_beat(
            db_session, player_id=player, spirit_id=spirit.spirit_id, beat_id="shared_beat"
        )
        second = award_story_beat(
            db_session, player_id=player, spirit_id=other.spirit_id, beat_id="shared_beat"
        )

        assert second.awarded is False, "同一個 beat 不該因為換了 spirit 就能再領一次"
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()


# ── 成長曲線：門檻是配著新規則定的 ─────────────────────────────────────

def test_growth_curve_wanhua_landmark(db_session, player, spirit):
    """
    有劇本的地標：召喚(+10) → stage 1，走完劇本(+30) → stage 2。

    這條測的不是程式碼，是**設計意圖**：(10, 40, 100) 這組門檻要讓
    「第一天見面」與「走完劇本」各自剛好落在一個階段上。任何一邊的數字動了
    這裡就會紅，那正是它存在的理由。
    """
    first = award_daily_encounter(db_session, player_id=player, spirit_id=spirit.spirit_id)
    assert first.stage == 1
    assert first.newly_unlocked_stages == [1]

    after_story = award_story_beat(
        db_session, player_id=player, spirit_id=spirit.spirit_id, beat_id="wanhua_beat_1"
    )
    assert after_story.resonance_value == 40
    assert after_story.stage == 2
    assert after_story.newly_unlocked_stages == [2]


def test_growth_curve_landmark_without_story(db_session, player, spirit):
    """
    沒有劇本的七個地標：只能靠每天回訪，**十天**到滿階。

    這條把「非萬華地標的成長速度」釘住。覺得十天太慢是產品判斷，但改之前
    要先知道現在是十天。
    """
    day = datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc)
    result = None
    for i in range(10):
        result = award_daily_encounter(
            db_session,
            player_id=player,
            spirit_id=spirit.spirit_id,
            now=day + timedelta(days=i),
        )

    assert result.resonance_value == 100
    assert result.stage == 3


# ── 隔離 ───────────────────────────────────────────────────────────────

def test_resonance_is_per_player(db_session, player, spirit, client):
    other_player = uuid.UUID(
        client.post(
            "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
        ).json()["player_id"]
    )

    _award(db_session, player, spirit.spirit_id, amount=20)
    result = _award(db_session, other_player, spirit.spirit_id, amount=20)

    assert result.resonance_value == 20  # 沒有把別人的分數算進來


def test_resonance_is_per_spirit(db_session, player, spirit):
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        _award(db_session, player, spirit.spirit_id, amount=20, source_id="a")
        result = _award(db_session, player, other.spirit_id, amount=20, source_id="b")

        assert result.resonance_value == 20
    finally:
        db_session.query(models.ResonanceEvent).filter_by(spirit_id=other.spirit_id).delete()
        db_session.query(models.Resonance).filter_by(spirit_id=other.spirit_id).delete()
        db_session.commit()
        db_session.delete(other)
        db_session.commit()
