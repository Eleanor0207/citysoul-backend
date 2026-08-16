"""
聊天紀錄：對話寫入 `dialogue_turns`，以及讀它的兩支端點。

    POST /spirits/{placeId}/dialogue          → 順手寫兩列
    GET  /dialogue-history/spirits            → 聊過哪些靈魂（第一層）
    GET  /spirits/{placeId}/dialogue-history  → 內文，由舊到新（第二層）

## 為什麼有「寫入」的測試

`dialogue_turns` 這張表在 2026-08-16 之前**從來沒有被寫過**——定義有、migration
有，就是沒有任何程式碼寫進去，而對話只活在 Redis 的 30 分鐘 TTL 裡。這一批的
主要工作其實是補上寫入；讀取端點只是把它開出來。

所以最重要的那幾條測試不是分頁，是「寫進去了嗎」與「寫進去的東西正確嗎」。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.modules.body import dialogue_log, models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.brain.models import Character, CitySoul, LandmarkSoul

_LAT, _LON = 25.0373, 121.4999


# ── 夾具 ───────────────────────────────────────────────────────────────


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    """
    一條 city → landmark → character → spirit 鏈（跟 test_dialogue.py 同一套）。

    ⚠️ 拆除時**必須先刪 `dialogue_turns`**：那張表對 `spirits.spirit_id` 有外鍵，
    留著列會讓 `db_session.delete(row)` 直接撞上外鍵違反，而錯誤會出現在 teardown
    —— pytest 會報成一個跟被測行為無關的 ERROR，很難聯想到是紀錄沒清。
    """
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    character_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[])
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    row = models.Spirit(
        spirit_id=unique_spirit_id, display_name="測試地標", latitude=_LAT, longitude=_LON,
        character_id=character_id, landmark_id=landmark_id,
        summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    yield row

    db_session.query(models.DialogueTurn).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def other_spirit(db_session):
    """第二個靈魂。用來驗「清單只列聊過的」與「內文不會混到別的地標」。"""
    sid = f"test-spirit-{uuid.uuid4()}"
    city_id, landmark_id, character_id = f"city-{sid}", f"lm-{sid}", f"ch-{sid}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市2", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(landmark_id=landmark_id, city_id=city_id, name="測試地標2", founding_facts=[])
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    row = models.Spirit(
        spirit_id=sid, display_name="測試地標2", latitude=_LAT, longitude=_LON,
        character_id=character_id, landmark_id=landmark_id,
        summon_radius_meters=50, is_active=True,
    )
    db_session.add(row)
    db_session.commit()

    yield row

    db_session.query(models.DialogueTurn).filter_by(spirit_id=sid).delete()
    db_session.commit()
    db_session.delete(row)
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture
def other_player(client):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


def _say(client, spirit, session_token, player_id, text):
    return client.post(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue",
        json={"user_input": text},
        headers={
            "Authorization": f"Bearer {session_token}",
            ENCOUNTER_TOKEN_HEADER: issue_encounter_token(player_id, spirit.spirit_id),
        },
    )


def _seed(db_session, *, player_id, spirit_id, pairs):
    """直接塞列。分頁與清單不需要真的呼叫模型，那只會讓測試變慢又變脆。"""
    for user_text, spirit_text in pairs:
        db_session.add_all(
            [
                models.DialogueTurn(
                    player_id=player_id, spirit_id=spirit_id,
                    role=dialogue_log.ROLE_PLAYER, content=user_text,
                ),
                models.DialogueTurn(
                    player_id=player_id, spirit_id=spirit_id,
                    role=dialogue_log.ROLE_SPIRIT, content=spirit_text,
                ),
            ]
        )
    db_session.commit()


def _history(client, spirit, token, **params):
    return client.get(
        f"/api/v1/spirits/{spirit.spirit_id}/dialogue-history",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )


def _threads(client, token):
    return client.get(
        "/api/v1/dialogue-history/spirits", headers={"Authorization": f"Bearer {token}"}
    )


# ── 寫入 ───────────────────────────────────────────────────────────────


def test_dialogue_records_two_turns(client, db_session, spirit, player):
    """一次來回落地兩列，內容與順序都對得起來。"""
    pid, token = player

    response = _say(client, spirit, token, pid, "你好啊")
    assert response.status_code == 200
    reply = response.json()["reply_text"]

    rows = (
        db_session.query(models.DialogueTurn)
        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
        .order_by(models.DialogueTurn.turn_id)
        .all()
    )

    assert len(rows) == 2
    assert (rows[0].role, rows[0].content) == (dialogue_log.ROLE_PLAYER, "你好啊")
    assert (rows[1].role, rows[1].content) == (dialogue_log.ROLE_SPIRIT, reply)


def test_roles_are_player_and_spirit_not_user_and_assistant(client, db_session, spirit, player):
    """
    🔴 釘住那個最容易踩的坑。

    Redis 短期記憶用的是 `user` / `assistant`，但這張表的 CHECK 約束只收
    `player` / `spirit`。照抄 Redis 的字串會違反約束，而寫入是靜默失敗的——
    症狀會是「聊天紀錄永遠空的」，沒有任何錯誤訊息指向真正的原因。
    """
    pid, token = player
    _say(client, spirit, token, pid, "你好")

    roles = {
        r.role
        for r in db_session.query(models.DialogueTurn).filter_by(
            player_id=pid, spirit_id=spirit.spirit_id
        )
    }
    assert roles == {"player", "spirit"}


def test_log_failure_does_not_break_the_dialogue(
    client, db_session, spirit, player, monkeypatch
):
    """
    寫日誌炸掉時，對話仍然正常回覆。

    玩家的回覆已經生成、配額也扣了。把記帳失敗變成 500 等於「因為沒記到帳，
    所以假裝這次對話沒發生」——玩家付了配額卻什麼都沒拿到。
    """
    pid, token = player

    def _boom(*args, **kwargs):
        raise RuntimeError("資料庫在這一刻壞掉")

    monkeypatch.setattr(dialogue_log, "_resonance_value_at", _boom)

    response = _say(client, spirit, token, pid, "你好")

    assert response.status_code == 200
    assert response.json()["reply_text"]
    assert (
        db_session.query(models.DialogueTurn)
        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
        .count()
        == 0
    )


# ── 讀取：內文 ─────────────────────────────────────────────────────────


def test_history_requires_session_token(client, spirit):
    assert client.get(f"/api/v1/spirits/{spirit.spirit_id}/dialogue-history").status_code == 401


def test_history_unknown_spirit_returns_404(client, player):
    _, token = player
    response = client.get(
        "/api/v1/spirits/no-such-place/dialogue-history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_history_is_empty_not_404_when_never_talked(client, spirit, player):
    """冷啟動是正常狀態。空陣列讓客戶端直接畫「還沒有聊過」，不必走錯誤流程。"""
    _, token = player
    response = _history(client, spirit, token)

    assert response.status_code == 200
    assert response.json() == {"turns": [], "has_more": False, "next_before": None}


def test_history_is_oldest_first(client, db_session, spirit, player):
    """聊天視窗由上往下讀，所以回應必須是時間正序。"""
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id,
          pairs=[("第一句", "回第一句"), ("第二句", "回第二句")])

    contents = [t["content"] for t in _history(client, spirit, token).json()["turns"]]
    assert contents == ["第一句", "回第一句", "第二句", "回第二句"]


def test_history_never_leaks_another_players_turns(
    client, db_session, spirit, player, other_player
):
    """
    🔒 過濾條件是 session token 裡的 player_id，不是路徑上的 spirit_id。

    只憑 spirit_id 查會回傳**所有玩家**跟這個靈魂講過的話。這條是那個錯誤唯一
    會變紅的地方——單人測試永遠看不出差別。
    """
    mine, my_token = player
    theirs, _ = other_player

    _seed(db_session, player_id=mine, spirit_id=spirit.spirit_id, pairs=[("我說的", "回我")])
    _seed(db_session, player_id=theirs, spirit_id=spirit.spirit_id, pairs=[("他說的", "回他")])

    contents = [t["content"] for t in _history(client, spirit, my_token).json()["turns"]]
    assert contents == ["我說的", "回我"]


def test_history_does_not_mix_in_other_spirits(
    client, db_session, spirit, other_spirit, player
):
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=[("對甲說", "甲回")])
    _seed(db_session, player_id=pid, spirit_id=other_spirit.spirit_id, pairs=[("對乙說", "乙回")])

    contents = [t["content"] for t in _history(client, spirit, token).json()["turns"]]
    assert contents == ["對甲說", "甲回"]


# ── 讀取：分頁 ─────────────────────────────────────────────────────────


def test_pagination_walks_backwards_without_gaps_or_repeats(
    client, db_session, spirit, player
):
    """
    照 `next_before` 一頁一頁往回翻，最後要剛好拼回完整、不重複的原文。

    這是游標用 `turn_id` 而不是 `created_at` 的驗收：同一次來回的兩列時間常常
    落在同一微秒內，用時間當游標會在邊界上漏掉或重複，而且只在對話密集時才出現。
    """
    pid, token = player
    pairs = [(f"問{i}", f"答{i}") for i in range(5)]      # 10 列
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=pairs)

    expected = [text for pair in pairs for text in pair]

    collected, before, pages = [], None, 0
    while True:
        body = _history(client, spirit, token, limit=3, **({"before": before} if before else {})).json()
        collected = [t["content"] for t in body["turns"]] + collected
        pages += 1
        if not body["has_more"]:
            assert body["next_before"] is None
            break
        before = body["next_before"]
        assert pages < 10, "翻頁沒有收斂——游標可能沒有往前走"

    assert collected == expected


def test_limit_bounds_are_in_the_contract(client, db_session, spirit, player):
    """
    `limit` 的上下界由 `Query(ge=1, le=MAX_PAGE_SIZE)` 擋，超出範圍回 422。

    ## 為什麼是拒絕而不是靜默夾住

    上限寫進 `Query` 才會進 OpenAPI，客戶端 codegen 看得到 200 這個數字。靜默
    夾住的話，客戶端傳 1000 拿回 200 筆，會以為「總共就這麼多」而停止翻頁——
    那是**資料被吃掉**，而且沒有任何跡象。

    `recent_turns()` 裡那行 `min(limit, MAX_PAGE_SIZE)` 仍然留著：它守的是不走
    HTTP 的呼叫端（測試、之後的批次），不是這一層。
    """
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=[("問", "答")])

    assert _history(client, spirit, token, limit=dialogue_log.MAX_PAGE_SIZE + 1).status_code == 422
    assert _history(client, spirit, token, limit=0).status_code == 422
    assert _history(client, spirit, token, limit=dialogue_log.MAX_PAGE_SIZE).status_code == 200


# ── 讀取：清單 ─────────────────────────────────────────────────────────


def test_threads_requires_session_token(client):
    assert client.get("/api/v1/dialogue-history/spirits").status_code == 401


def test_threads_lists_only_spirits_actually_talked_to(
    client, db_session, spirit, other_spirit, player
):
    """
    沒聊過的靈魂不會出現。這正是清單相對於下拉選單的價值：地標有十個，
    聊過的通常兩三個。
    """
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=[("嗨", "嗯")])

    ids = [t["spirit_id"] for t in _threads(client, token).json()["threads"]]
    assert ids == [spirit.spirit_id]
    assert other_spirit.spirit_id not in ids


def test_threads_shows_the_last_line_and_the_count(client, db_session, spirit, player):
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id,
          pairs=[("第一句", "回第一句"), ("第二句", "最後一句")])

    thread = _threads(client, token).json()["threads"][0]

    assert thread["last_message"] == "最後一句"
    assert thread["last_role"] == "spirit"
    assert thread["turn_count"] == 4
    assert thread["name"] == "測試地標"


def test_threads_are_newest_first(client, db_session, spirit, other_spirit, player):
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=[("先聊甲", "甲回")])
    _seed(db_session, player_id=pid, spirit_id=other_spirit.spirit_id, pairs=[("後聊乙", "乙回")])

    ids = [t["spirit_id"] for t in _threads(client, token).json()["threads"]]
    assert ids == [other_spirit.spirit_id, spirit.spirit_id]


def test_threads_never_leak_another_player(client, db_session, spirit, player, other_player):
    """🔒 同 `test_history_never_leaks_another_players_turns`，這一層也要擋。"""
    mine, my_token = player
    theirs, _ = other_player

    _seed(db_session, player_id=theirs, spirit_id=spirit.spirit_id, pairs=[("他的", "回他")])

    assert _threads(client, my_token).json()["threads"] == []


def test_threads_timestamp_is_timezone_aware(client, db_session, spirit, player):
    """
    ⚠️ `last_at` 必須帶時區。

    客戶端要把它換算成台北時間才能顯示「今天／昨天」。少了時區資訊，客戶端只能
    猜——而猜錯的症狀是半夜聊的天顯示成前一天下午，看起來像資料壞掉而不是時區問題。
    """
    pid, token = player
    _seed(db_session, player_id=pid, spirit_id=spirit.spirit_id, pairs=[("嗨", "嗯")])

    last_at = datetime.fromisoformat(_threads(client, token).json()["threads"][0]["last_at"])

    assert last_at.tzinfo is not None
    assert last_at.astimezone(timezone.utc) <= datetime.now(timezone.utc)
