"""
Ticket #15．任務進度資料表與狀態機判定邏輯（S4）。

驗收標準對照見 GitHub issue #15。

時間是這個狀態機的核心輸入（憑證逾時、跨日重置），所以大部分測試直接呼叫
`evaluate_on_summon` 並注入 `now`——沒有別的方法能在測試裡讓 16 分鐘或一整天
真的過去。走 API 的測試負責確認「這台狀態機真的被 `/summon` 接上了」。
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_EXPIRE_SECONDS
from app.modules.body.quests import (
    MAX_DAILY_ATTEMPTS,
    STATUS_COMPLETED,
    STATUS_DAILY_LIMIT_REACHED,
    STATUS_IN_PROGRESS,
    TAIPEI,
    evaluate_on_summon,
    quest_id_for_spirit,
    taipei_today,
)

_LAT, _LON = 25.0955, 121.5186
# 比憑證效期多一秒，剛好落在「已過期」那一側。
_EXPIRED = timedelta(seconds=ENCOUNTER_TOKEN_EXPIRE_SECONDS + 1)


def _today_taipei_at(hour: int) -> datetime:
    """
    「台北今天」的某個時刻，以 UTC 表示。

    ⚠️ **這個檔案裡的每個起點都用這支，不要用 `datetime.now(timezone.utc)`。**

    兩個理由，各自都足夠：

    1. 走 API 的測試不能用 `datetime.now(timezone.utc).replace(hour=...)`——後者釘
       的是 **UTC 今天**，而 API 內部用真實時鐘算的是 **台北今天**。UTC 16:00 之後
       兩者就是不同日期，注入的嘗試次數會在 API 呼叫時被當成「昨天的」而重置。
    2. 直接用真實時鐘當起點的測試，只要往後加幾次 `_EXPIRED` 就可能跨過 UTC 16:00
       ＝台北午夜，`attempts_today` 被當成新的一天歸零。**釘在台北凌晨 1 點就有
       整整 23 小時的餘裕**，怎麼加都跨不過去。

    這兩個 bug 都真的發生過——測試寫好當天在 UTC 16:00 前跑都是綠的，過了台北午夜
    才爆，而且爆在專門處理台北午夜的那個模組上。第二種在 2026-08-18 又出現一次
    （`test_attempts_accumulate_across_repeated_timeouts`），所以現在整個檔案統一。
    """
    return (
        datetime.now(TAIPEI)
        .replace(hour=hour, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )


@pytest.fixture
def spirit(db_session):
    row = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client, db_session):
    """建立玩家；結束時連同它的 quest_progress 一起清掉（有外鍵，順序要對）。"""
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    player_id = uuid.UUID(body["player_id"])
    yield player_id, body["session_token"]

    db_session.query(models.QuestProgress).filter_by(player_id=player_id).delete()
    db_session.commit()


def _progress(db_session, player_id, spirit_id) -> models.QuestProgress | None:
    return (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id, quest_id=quest_id_for_spirit(spirit_id))
        .first()
    )


def _summon(client, token, spirit):
    return client.post(
        "/api/v1/summon",
        json={
            "spirit_id": spirit.spirit_id,
            "latitude": spirit.latitude,
            "longitude": spirit.longitude,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


# ── 第一次召喚：建立任務進度 ──────────────────────────────────────────

def test_first_summon_creates_in_progress_quest(db_session, player, spirit):
    player_id, _ = player
    now = _today_taipei_at(1)

    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now
    )

    assert state.status == STATUS_IN_PROGRESS
    assert state.attempts_today == 0

    row = _progress(db_session, player_id, spirit.spirit_id)
    assert row is not None
    assert row.status == STATUS_IN_PROGRESS
    assert row.attempts_date == taipei_today(now)
    assert row.current_token_issued_at is not None


# ── 失敗判定 ───────────────────────────────────────────────────────────

def test_expired_token_counts_as_failed_attempt(db_session, player, spirit):
    """憑證過期、任務仍 in_progress → 下一次召喚記一次失敗。"""
    player_id, _ = player
    start = _today_taipei_at(1)

    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=start)
    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=start + _EXPIRED
    )

    assert state.attempts_today == 1
    assert state.status == STATUS_IN_PROGRESS  # 還有次數，可以重新挑戰


def test_unexpired_token_does_not_count_as_failure(db_session, player, spirit):
    """
    憑證還沒過期就再召喚一次（例如 App 重開），不該算失敗。

    這是失敗判定最容易寫錯的方向：把「又召喚了一次」當成「上一次失敗了」。
    """
    player_id, _ = player
    start = _today_taipei_at(1)

    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=start)
    state = evaluate_on_summon(
        db_session,
        player_id=player_id,
        spirit_id=spirit.spirit_id,
        now=start + timedelta(seconds=ENCOUNTER_TOKEN_EXPIRE_SECONDS - 1),
    )

    assert state.attempts_today == 0


def test_completed_quest_does_not_accrue_failures(db_session, player, spirit):
    """任務已完成的話，憑證放到過期也不算失敗。"""
    player_id, _ = player
    start = _today_taipei_at(1)

    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=start)
    row = _progress(db_session, player_id, spirit.spirit_id)
    row.status = STATUS_COMPLETED
    db_session.commit()

    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=start + _EXPIRED
    )

    assert state.attempts_today == 0
    assert state.status == STATUS_COMPLETED


def test_attempts_accumulate_across_repeated_timeouts(db_session, player, spirit):
    """
    連續逾時兩次 → 次數累加成 2。

    起點必須是 `_today_taipei_at(1)`：這裡會往後加兩次 `_EXPIRED`（約 32 分鐘），
    用真實時鐘的話在台北午夜前約半小時跑就會跨日，第二次的期望值 2 會拿到 1。
    """
    player_id, _ = player
    now = _today_taipei_at(1)

    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now)
    for expected in (1, 2):
        now += _EXPIRED
        state = evaluate_on_summon(
            db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now
        )
        assert state.attempts_today == expected
        assert state.status == STATUS_IN_PROGRESS


# ── 每日上限 ───────────────────────────────────────────────────────────

def _burn_attempts(db_session, player_id, spirit_id, start, count):
    """連續讓 `count` 次嘗試逾時，回傳最後一次的時間點。"""
    now = start
    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit_id, now=now)
    for _ in range(count):
        now += _EXPIRED
        evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit_id, now=now)
    return now


def test_third_failure_locks_out_for_the_day(db_session, player, spirit):
    player_id, _ = player
    start = _today_taipei_at(1)  # 台北凌晨，確保加幾次逾時不會跨過台北午夜

    now = _burn_attempts(db_session, player_id, spirit.spirit_id, start, MAX_DAILY_ATTEMPTS)
    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now
    )

    assert state.attempts_today == MAX_DAILY_ATTEMPTS
    assert state.status == STATUS_DAILY_LIMIT_REACHED


def test_locked_out_state_issues_no_new_attempt(db_session, player, spirit):
    """鎖定當天不該再開新的嘗試——`current_token_issued_at` 不被刷新。"""
    player_id, _ = player
    start = _today_taipei_at(1)

    now = _burn_attempts(db_session, player_id, spirit.spirit_id, start, MAX_DAILY_ATTEMPTS)
    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now)

    row = _progress(db_session, player_id, spirit.spirit_id)
    assert row.current_token_issued_at is None


def test_daily_limit_reached_is_never_written_to_the_database(db_session, player, spirit):
    """
    `daily_limit_reached` 只是查詢當下算出來的結果，存進 DB 隔天就是錯的。
    """
    player_id, _ = player
    start = _today_taipei_at(1)

    now = _burn_attempts(db_session, player_id, spirit.spirit_id, start, MAX_DAILY_ATTEMPTS)
    evaluate_on_summon(db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=now)

    row = _progress(db_session, player_id, spirit.spirit_id)
    assert row.status == STATUS_IN_PROGRESS


# ── 跨日重置 ───────────────────────────────────────────────────────────

def test_attempts_reset_after_taipei_midnight(db_session, player, spirit):
    """
    昨天用完 3 次被鎖定，今天（台北時間）再挑戰 → 次數歸零、可重新挑戰。
    """
    player_id, _ = player
    yesterday = _today_taipei_at(1) - timedelta(days=1)

    _burn_attempts(db_session, player_id, spirit.spirit_id, yesterday, MAX_DAILY_ATTEMPTS)
    assert _progress(db_session, player_id, spirit.spirit_id).attempts_today == MAX_DAILY_ATTEMPTS

    today = _today_taipei_at(1)
    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=today
    )

    assert state.attempts_today == 0
    assert state.status == STATUS_IN_PROGRESS
    assert _progress(db_session, player_id, spirit.spirit_id).attempts_date == taipei_today(today)


def test_day_boundary_is_taipei_midnight_not_utc(db_session, player, spirit):
    """
    UTC 與台北差 8 小時，兩者的「今天」在一天之中有 8 小時是不同的日期。

    先在台北 8/4 晚上用掉次數，再在台北 8/5 早上 7 點挑戰：以台北算已經跨日
    該重置；若誤用 UTC 算，兩個時間點都還落在 UTC 的 8/4，就不會重置。

    起始時間刻意選 20:00 而不是接近午夜——`_burn_attempts` 本身要花掉三段
    15 分鐘，從 23:30 開始會在**製造測試前提的過程中**就跨過午夜，那樣測到的
    就不是我們想測的那件事了（第一版就是這樣寫，跑出來才發現）。
    """
    player_id, _ = player

    late_yesterday = datetime(2026, 8, 4, 20, 0, tzinfo=TAIPEI)
    early_today = datetime(2026, 8, 5, 7, 0, tzinfo=TAIPEI)

    # 確認這組時間真的能區分兩種時區演算法
    assert late_yesterday.astimezone(timezone.utc).date() == early_today.astimezone(timezone.utc).date()
    assert taipei_today(late_yesterday) != taipei_today(early_today)

    _burn_attempts(db_session, player_id, spirit.spirit_id, late_yesterday, MAX_DAILY_ATTEMPTS)
    state = evaluate_on_summon(
        db_session, player_id=player_id, spirit_id=spirit.spirit_id, now=early_today
    )

    assert state.attempts_today == 0


# ── 隔離 ───────────────────────────────────────────────────────────────

def test_attempts_are_per_spirit(db_session, player, spirit, client):
    """對 A 靈魂失敗三次，不該影響 B 靈魂的挑戰次數。"""
    player_id, _ = player
    other = models.Spirit(
        spirit_id=f"test-spirit-{uuid.uuid4()}",
        display_name="另一個地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()

    try:
        start = _today_taipei_at(1)
        _burn_attempts(db_session, player_id, spirit.spirit_id, start, MAX_DAILY_ATTEMPTS)

        state = evaluate_on_summon(
            db_session, player_id=player_id, spirit_id=other.spirit_id, now=start
        )
        assert state.attempts_today == 0
        assert state.status == STATUS_IN_PROGRESS
    finally:
        db_session.query(models.QuestProgress).filter_by(
            player_id=player_id, quest_id=quest_id_for_spirit(other.spirit_id)
        ).delete()
        db_session.delete(other)
        db_session.commit()


# ── 透過 /summon 實際接上 ─────────────────────────────────────────────

def test_summon_response_includes_quest_state(client, player, spirit):
    _, token = player

    body = _summon(client, token, spirit).json()

    assert body["quest"]["quest_id"] == quest_id_for_spirit(spirit.spirit_id)
    assert body["quest"]["status"] == STATUS_IN_PROGRESS
    assert body["quest"]["attempts_today"] == 0


def test_summon_persists_quest_progress(client, player, spirit, db_session):
    player_id, token = player

    _summon(client, token, spirit)

    assert _progress(db_session, player_id, spirit.spirit_id) is not None


def test_summon_still_issues_token_when_daily_limit_reached(
    client, player, spirit, db_session
):
    """
    AC：次數用完時「在場驗證仍可通過（玩家可對話）」。
    encounter_token 照發，只有任務被鎖住。
    """
    player_id, token = player
    start = _today_taipei_at(1)
    _burn_attempts(db_session, player_id, spirit.spirit_id, start, MAX_DAILY_ATTEMPTS)

    resp = _summon(client, token, spirit)

    assert resp.status_code == 200
    body = resp.json()
    assert body["encounter_token"]
    assert body["quest"]["status"] == STATUS_DAILY_LIMIT_REACHED
    assert body["quest"]["attempts_today"] == MAX_DAILY_ATTEMPTS


def test_onetime_uniqueness_survives_the_surrogate_key(db_session, player, spirit):
    """
    0003 把主鍵從 `(player_id, quest_id)` 換成代理鍵 `progress_id`。代理鍵
    本身不擋任何重複，唯一性改由 partial unique index 負責——所以要驗的是
    「換完之後保護還在」，不是「換完之後跑得動」。

    `issued_date IS NULL`（目前所有列都是）落在 `uq_quest_progress_onetime`，
    語意跟舊的複合主鍵完全等價。
    """
    player_id, _ = player
    quest_id = f"test-quest-{uuid.uuid4()}"
    for _ in range(2):
        db_session.add(
            models.QuestProgress(
                player_id=player_id,
                quest_id=quest_id,
                status="in_progress",
                progress_value=0,
                attempts_today=0,
                attempts_date=date(2026, 8, 7),
            )
        )

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_quest_on_different_days_is_allowed(db_session, player, spirit):
    """
    每日任務的整個重點：同一個玩家同一個任務，不同天各一列。舊的複合主鍵
    表達不了這件事，這也是換代理鍵的原因。
    """
    player_id, _ = player
    quest_id = f"test-quest-{uuid.uuid4()}"
    for day in (date(2026, 8, 6), date(2026, 8, 7)):
        db_session.add(
            models.QuestProgress(
                player_id=player_id,
                quest_id=quest_id,
                issued_date=day,
                status="in_progress",
                progress_value=0,
                attempts_today=0,
                attempts_date=day,
            )
        )

    db_session.commit()

    rows = db_session.query(models.QuestProgress).filter_by(quest_id=quest_id).all()
    assert len(rows) == 2
    assert len({r.progress_id for r in rows}) == 2
