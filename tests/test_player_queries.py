"""
玩家自身資料的唯讀查詢端點：#33 `/quests/daily`、#35 `/resonance/{spiritId}`、
#36 `/profile`、#37 `/players/me/memory-summary`。

四支放在同一個檔案，是因為它們共用 `queries.py` 的計算，而其中一條 AC 明訂
要**同時呼叫兩支端點做對照**（#36：profile 的 stage 必須與 resonance 端點一致）。
分檔的話那條測試就得跨檔案共用夾具。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.body import models
from app.modules.body.encounter_tokens import issue_encounter_token
from app.modules.body.quests import quest_id_for_spirit, taipei_today
from app.modules.body.resonance import apply_resonance
from app.modules.brain.memory import write_memory
from app.modules.brain.models import EMBEDDING_DIM

_LAT, _LON = 25.0373983, 121.4997318


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.QuestProgress).filter_by(
        quest_id=quest_id_for_spirit(unique_spirit_id)
    ).delete()
    db_session.delete(row)
    db_session.commit()


def _make_player(client):
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture
def player(client):
    return _make_player(client)


@pytest.fixture
def other_player(client):
    return _make_player(client)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _add_progress(db_session, player_id, spirit_id, *, attempts=0, attempts_date=None, status="in_progress"):
    row = models.QuestProgress(
        player_id=player_id,
        quest_id=quest_id_for_spirit(spirit_id),
        status=status,
        progress_value=0,
        attempts_today=attempts,
        attempts_date=attempts_date or taipei_today(datetime.now(timezone.utc)),
    )
    db_session.add(row)
    db_session.commit()
    return row


# ══════════════════════════════════════════════════════════════════════
# #33 GET /quests/daily
# ══════════════════════════════════════════════════════════════════════

_QUEST_PATHS = ["/api/v1/quests/daily", "/api/v1/profile", "/api/v1/players/me/memory-summary"]


@pytest.mark.parametrize("path", _QUEST_PATHS + ["/api/v1/resonance/whatever"])
def test_endpoints_require_a_session_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=_auth("garbage")).status_code == 401


@pytest.mark.parametrize("path", _QUEST_PATHS)
def test_encounter_token_cannot_be_used_as_a_session_token(client, path, player):
    """
    AC：用 encounter 金鑰簽的 token 必須失敗——證明 token 種類不可互換。

    三種 token 用不同金鑰（SDD 第6節），所以這在驗章就會失敗。
    """
    pid, _ = player
    forged = issue_encounter_token(pid, "some-spirit")

    assert client.get(path, headers=_auth(forged)).status_code == 401


def test_daily_quests_returns_all_four_fields(client, db_session, spirit, player):
    pid, sess = player
    _add_progress(db_session, pid, spirit.spirit_id, attempts=1)

    body = client.get("/api/v1/quests/daily", headers=_auth(sess)).json()

    assert body["quests"] == [
        {
            "quest_id": quest_id_for_spirit(spirit.spirit_id),
            "spirit_id": spirit.spirit_id,
            "status": "in_progress",
            "attempts_today": 1,
        }
    ]


def test_spirit_id_is_derived_from_quest_id(client, db_session, spirit, player):
    """
    AC：特別確認 `spirit_id` 正確——它**不在資料表裡**，是從 `quest_id` 反推的。
    """
    pid, sess = player
    _add_progress(db_session, pid, spirit.spirit_id)

    body = client.get("/api/v1/quests/daily", headers=_auth(sess)).json()

    assert body["quests"][0]["spirit_id"] == spirit.spirit_id
    assert "spirit_id" not in {c.name for c in models.QuestProgress.__table__.columns}


def test_daily_limit_reached_is_computed_not_stored(client, db_session, spirit, player):
    """
    🔒 AC：`attempts_today=3` 時回應是 `daily_limit_reached`，但**資料庫仍是
    `in_progress`**。

    這條釘住「回應狀態 ≠ 資料庫狀態」的分界（#15 建立的規則）：
    `daily_limit_reached` 取決於「今天」是哪一天，存進資料庫隔天就是錯的。

    AC 指定要做 mutation 驗證的其中一條。
    """
    pid, sess = player
    row = _add_progress(db_session, pid, spirit.spirit_id, attempts=3)

    body = client.get("/api/v1/quests/daily", headers=_auth(sess)).json()

    assert body["quests"][0]["status"] == "daily_limit_reached"

    db_session.expire_all()
    assert db_session.query(models.QuestProgress).filter_by(progress_id=row.progress_id).one().status == "in_progress"


def test_attempts_reset_across_the_day_boundary(client, db_session, spirit, player):
    """
    AC：`attempts_date` 是台北昨天、`attempts_today=3` → 回應是 0 / in_progress。

    ⚠️ 直接寫入昨天的 `attempts_date` 而不是依賴真實時鐘——#15 就因此出過一個
    「過了台北午夜才爆」的 flaky 測試。
    """
    pid, sess = player
    yesterday = taipei_today(datetime.now(timezone.utc)) - timedelta(days=1)
    _add_progress(db_session, pid, spirit.spirit_id, attempts=3, attempts_date=yesterday)

    quest = client.get("/api/v1/quests/daily", headers=_auth(sess)).json()["quests"][0]

    assert quest["attempts_today"] == 0
    assert quest["status"] == "in_progress"


def test_query_does_not_write_back_the_reset(client, db_session, spirit, player):
    """
    🔒 唯讀：跨日的歸零是**算出來的**，不寫回資料庫。

    查詢端點如果順手做了狀態轉移，玩家只要打開任務列表就等於推進了一次任務
    ——那是很難追查的副作用。真正的歸零由下一次 `/summon` 寫入。
    """
    pid, sess = player
    yesterday = taipei_today(datetime.now(timezone.utc)) - timedelta(days=1)
    row = _add_progress(db_session, pid, spirit.spirit_id, attempts=3, attempts_date=yesterday)

    client.get("/api/v1/quests/daily", headers=_auth(sess))

    db_session.expire_all()
    stored = db_session.query(models.QuestProgress).filter_by(progress_id=row.progress_id).one()
    assert stored.attempts_today == 3, "查詢端點把跨日歸零寫回資料庫了"
    assert stored.attempts_date == yesterday


def test_no_quests_returns_an_empty_array(client, player):
    """AC：冷啟動是正常狀態，不是 404。"""
    _, sess = player

    response = client.get("/api/v1/quests/daily", headers=_auth(sess))

    assert response.status_code == 200
    assert response.json() == {"quests": []}


def test_quests_are_isolated_per_player(client, db_session, spirit, player, other_player):
    """AC 指定要做 mutation 驗證的其中一條（拿掉 player_id 過濾）。"""
    pid, sess = player
    qid, _ = other_player
    _add_progress(db_session, pid, spirit.spirit_id, attempts=1)
    _add_progress(db_session, qid, spirit.spirit_id, attempts=2)

    body = client.get("/api/v1/quests/daily", headers=_auth(sess)).json()

    assert len(body["quests"]) == 1
    assert body["quests"][0]["attempts_today"] == 1


# ══════════════════════════════════════════════════════════════════════
# #35 GET /resonance/{spiritId}
# ══════════════════════════════════════════════════════════════════════

def _set_resonance(db_session, player_id, spirit_id, value):
    row = (
        db_session.query(models.Resonance)
        .filter_by(player_id=player_id, spirit_id=spirit_id)
        .first()
    )
    if row is None:
        row = models.Resonance(player_id=player_id, spirit_id=spirit_id, resonance_value=value)
        db_session.add(row)
    else:
        row.resonance_value = value
    db_session.commit()


def test_resonance_returns_four_fields(client, db_session, spirit, player):
    pid, sess = player
    _set_resonance(db_session, pid, spirit.spirit_id, 30)

    body = client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).json()

    assert body == {
        "spirit_id": spirit.spirit_id,
        "resonance_value": 30,
        "stage": 1,
        "next_threshold": 40,
    }


@pytest.mark.parametrize(
    "value,stage,next_t",
    [
        (0, 0, 10),
        (9, 0, 10),
        (10, 1, 40),  # 邊界：恰好等於門檻就算達標
        (39, 1, 40),
        (40, 2, 100),  # 邊界
        (99, 2, 100),
        (100, 3, None),  # 邊界：滿階，next_threshold 為 null
        (250, 3, None),
    ],
)
def test_threshold_boundaries(client, db_session, spirit, player, value, stage, next_t):
    """
    🔒 AC：逐一驗證門檻邊界。**恰好等於門檻就算達標**（`>=` 而非 `>`）。

    AC 指定要做 mutation 驗證的其中一條。
    """
    pid, sess = player
    _set_resonance(db_session, pid, spirit.spirit_id, value)

    body = client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).json()

    assert body["stage"] == stage
    assert body["next_threshold"] == next_t


def test_no_resonance_yet_returns_zero_not_404(client, spirit, player):
    """AC：「還沒開始」不是錯誤——前端要能直接顯示 0/10 的進度條。"""
    _, sess = player

    response = client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess))

    assert response.status_code == 200
    assert response.json() == {
        "spirit_id": spirit.spirit_id,
        "resonance_value": 0,
        "stage": 0,
        "next_threshold": 10,
    }


def test_unknown_spirit_returns_404(client, player):
    _, sess = player

    response = client.get("/api/v1/resonance/no-such-spirit", headers=_auth(sess))

    assert response.status_code == 404
    assert response.json()["detail"] == "spirit not found"


def test_inactive_spirit_returns_404_even_with_resonance(client, db_session, spirit, player):
    """AC：即使該玩家已有共鳴值也要 404。"""
    pid, sess = player
    _set_resonance(db_session, pid, spirit.spirit_id, 50)
    spirit.is_active = False
    db_session.commit()

    assert client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).status_code == 404


def test_resonance_is_isolated_per_player(client, db_session, spirit, player, other_player):
    """AC：P 有 30、Q 有 100 → P 看到 30（不是 100、也不是 130）。"""
    pid, sess = player
    qid, _ = other_player
    _set_resonance(db_session, pid, spirit.spirit_id, 30)
    _set_resonance(db_session, qid, spirit.spirit_id, 100)

    body = client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).json()

    assert body["resonance_value"] == 30


def test_stage_is_recomputed_because_there_is_no_stage_column(db_session):
    """
    ⚠️ **AC 的驗收方式做不到，這裡說明為什麼。**

    AC 說「人為把 DB 的 `resonance.stage` 改成錯誤值，確認端點回傳重算的結果」。
    但 **`resonance` 表沒有 `stage` 欄位**——migration `0003` 把它刪了，理由正是
    `stage_for_value()` 從來沒讀過它（一個永遠不被信任的快取欄位，存在的唯一
    效果是讓下一個人誤用它）。

    所以 AC 想防的那個 bug 在 schema 層級就不可能發生。這條測試改成釘住那個
    前提：欄位不存在。哪天有人「為了效能」把它加回來，這裡會紅，而那正是該
    重新討論的時機。
    """
    columns = {c.name for c in models.Resonance.__table__.columns}

    assert "stage" not in columns, (
        "resonance 表長出了 stage 欄位。0003 刪掉它是刻意的——"
        "value 是事實來源，stage 一律重算。"
    )


# ══════════════════════════════════════════════════════════════════════
# #36 GET /profile
# ══════════════════════════════════════════════════════════════════════

def test_profile_returns_quests_and_resonance(client, db_session, spirit, player, unique_device_id):
    """
    AC：一個靈魂有任務＋共鳴，另一個只有共鳴 → 兩個陣列長度不必相同。
    """
    pid, sess = player
    other_spirit = models.Spirit(
        spirit_id=f"{spirit.spirit_id}-b",
        display_name="第二個地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(other_spirit)
    db_session.commit()

    try:
        _add_progress(db_session, pid, spirit.spirit_id, attempts=1)
        _set_resonance(db_session, pid, spirit.spirit_id, 30)
        _set_resonance(db_session, pid, other_spirit.spirit_id, 100)

        body = client.get("/api/v1/profile", headers=_auth(sess)).json()

        assert len(body["quests"]) == 1
        assert len(body["resonance"]) == 2
        by_spirit = {r["spirit_id"]: r for r in body["resonance"]}
        assert by_spirit[spirit.spirit_id] == {
            "spirit_id": spirit.spirit_id,
            "resonance_value": 30,
            "stage": 1,
        }
        assert by_spirit[other_spirit.spirit_id]["stage"] == 3
    finally:
        db_session.query(models.Resonance).filter_by(spirit_id=other_spirit.spirit_id).delete()
        db_session.delete(other_spirit)
        db_session.commit()


def test_new_player_profile_is_two_empty_arrays(client, player):
    _, sess = player

    response = client.get("/api/v1/profile", headers=_auth(sess))

    assert response.status_code == 200
    assert response.json() == {"quests": [], "resonance": []}


def test_profile_stage_matches_the_resonance_endpoint(client, db_session, spirit, player):
    """
    🔒 AC：**同時呼叫兩支端點做對照**，而不是各自寫死期望值。

    這樣才擋得住「兩邊各自重算、日後其中一邊改了規則」——寫死期望值的話，
    兩邊一起改錯會一起變綠。
    """
    pid, sess = player
    _set_resonance(db_session, pid, spirit.spirit_id, 40)

    profile = client.get("/api/v1/profile", headers=_auth(sess)).json()
    single = client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).json()

    profile_stage = next(
        r["stage"] for r in profile["resonance"] if r["spirit_id"] == spirit.spirit_id
    )

    assert profile_stage == single["stage"]
    assert profile_stage == 2


def test_profile_is_isolated_per_player(client, db_session, spirit, player, other_player):
    pid, sess = player
    qid, _ = other_player
    _set_resonance(db_session, pid, spirit.spirit_id, 30)
    _set_resonance(db_session, qid, spirit.spirit_id, 100)
    _add_progress(db_session, qid, spirit.spirit_id, attempts=2)

    body = client.get("/api/v1/profile", headers=_auth(sess)).json()

    assert body["quests"] == []
    assert [r["resonance_value"] for r in body["resonance"]] == [30]


def test_profile_keeps_resonance_for_delisted_spirits(client, db_session, spirit, player):
    """
    ⚠️ 跟 `GET /resonance/{spiritId}` 的 404 刻意不同。

    Profile 是玩家自己的歷程——一個靈魂下架不該讓他累積的共鳴值從個人頁消失，
    那看起來像資料遺失。單一查詢回 404 是因為那是「去看那個靈魂」，而它不在了。
    """
    pid, sess = player
    _set_resonance(db_session, pid, spirit.spirit_id, 30)
    spirit.is_active = False
    db_session.commit()

    body = client.get("/api/v1/profile", headers=_auth(sess)).json()

    assert [r["spirit_id"] for r in body["resonance"]] == [spirit.spirit_id]
    assert client.get(f"/api/v1/resonance/{spirit.spirit_id}", headers=_auth(sess)).status_code == 404


# ══════════════════════════════════════════════════════════════════════
# #37 GET /players/me/memory-summary
# ══════════════════════════════════════════════════════════════════════

def _write(db_session, player_id, spirit_id, text):
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=spirit_id,
        summary_text=text,
        embedding=[0.1] * EMBEDDING_DIM,
        source="dialogue_summary",
    )


def test_memories_are_grouped_by_spirit(client, db_session, spirit, player):
    """AC：A 靈魂 2 筆、B 靈魂 1 筆，每筆含 `summary_text` 與 `created_at`。"""
    pid, sess = player
    other_spirit_id = f"{spirit.spirit_id}-b"
    _write(db_session, pid, spirit.spirit_id, "第一段記憶")
    _write(db_session, pid, spirit.spirit_id, "第二段記憶")
    _write(db_session, pid, other_spirit_id, "另一個靈魂的記憶")

    body = client.get("/api/v1/players/me/memory-summary", headers=_auth(sess)).json()

    by_spirit = {g["spirit_id"]: g["memories"] for g in body["groups"]}
    assert len(by_spirit[spirit.spirit_id]) == 2
    assert len(by_spirit[other_spirit_id]) == 1
    assert set(by_spirit[other_spirit_id][0]) == {"summary_text", "created_at"}


def test_memories_are_isolated_per_player(client, db_session, spirit, player, other_player):
    """
    🔒 AC：Q 對**同一個靈魂**有 5 筆、P 只有 1 筆 → 只回 P 的那 1 筆。

    刻意讓 Q 的資料更多且在同一靈魂下——若過濾條件只用 `spirit_id` 而漏了
    `player_id`，這條會立刻紅。
    """
    pid, sess = player
    qid, _ = other_player
    _write(db_session, pid, spirit.spirit_id, "我的記憶")
    for i in range(5):
        _write(db_session, qid, spirit.spirit_id, f"別人的記憶{i}")

    body = client.get("/api/v1/players/me/memory-summary", headers=_auth(sess)).json()

    all_texts = [m["summary_text"] for g in body["groups"] for m in g["memories"]]
    assert all_texts == ["我的記憶"]


def test_no_memories_returns_empty_groups(client, player):
    _, sess = player

    response = client.get("/api/v1/players/me/memory-summary", headers=_auth(sess))

    assert response.status_code == 200
    assert response.json() == {"groups": []}


def test_response_contains_no_embedding_vector(client, db_session, spirit, player):
    """
    🔒 AC：遞迴檢查所有鍵名與值，找不到任何 768 維陣列、也沒有 `embedding` 鍵。

    向量是內部實作，對玩家無意義且會讓回應暴增數十 KB。
    """
    pid, sess = player
    _write(db_session, pid, spirit.spirit_id, "一段記憶")

    raw = client.get("/api/v1/players/me/memory-summary", headers=_auth(sess)).text
    body = client.get("/api/v1/players/me/memory-summary", headers=_auth(sess)).json()

    assert "embedding" not in raw

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert "embed" not in key.lower()
                _walk(value)
        elif isinstance(node, list):
            assert len(node) < EMBEDDING_DIM, "回應裡出現了看起來像向量的長陣列"
            for item in node:
                _walk(item)

    _walk(body)
