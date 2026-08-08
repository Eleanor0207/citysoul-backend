"""
#39．S11 訊號推播（後端側）。

推播平台以 fake 注入——**測試不會真的送出任何推播**。對真實 Postgres 跑。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.push import (
    FakePushSender,
    PushPayload,
    build_signal_payload,
    register_push_token,
    send_signal_to_subscribers,
    unsubscribe,
)

_LAT, _LON = 25.0373983, 121.4997318


@pytest.fixture(autouse=True)
def _isolate_subscriptions(db_session):
    """
    群發是**全域**操作，所以測試之間必須乾淨——別的測試留下的訂閱會被一起送。
    """
    db_session.query(models.PushSubscription).delete()
    db_session.commit()
    yield
    db_session.query(models.PushSubscription).delete()
    db_session.commit()


def _make_player(client):
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture
def player(client):
    return _make_player(client)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _subscription(db_session, player_id):
    db_session.expire_all()
    return db_session.query(models.PushSubscription).filter_by(player_id=player_id).first()


# ── 表結構 ─────────────────────────────────────────────────────────────

def test_table_matches_the_sdd_schema(db_session):
    """AC：欄位為 player_id(PK, FK→players)、push_token、is_subscribed、updated_at。"""
    from sqlalchemy import inspect

    inspector = inspect(db_session.bind)
    columns = {c["name"]: c for c in inspector.get_columns("push_subscriptions")}
    pk = set(inspector.get_pk_constraint("push_subscriptions")["constrained_columns"])
    fks = inspector.get_foreign_keys("push_subscriptions")

    assert set(columns) == {"player_id", "push_token", "is_subscribed", "updated_at"}
    assert pk == {"player_id"}
    assert any(fk["referred_table"] == "players" for fk in fks)
    assert columns["push_token"]["nullable"] is False
    assert columns["is_subscribed"]["nullable"] is False


def test_table_has_no_location_columns(db_session):
    """
    🔒 推播是通知不是內容——這張表不需要知道玩家在哪裡。

    CONTEXT.md：原始 GPS 只在驗證期間使用後即丟棄。
    """
    columns = {c.name for c in models.PushSubscription.__table__.columns}

    for banned in ["latitude", "longitude", "lat", "lon", "location", "coordinates"]:
        assert banned not in columns


# ── 註冊 ───────────────────────────────────────────────────────────────

def test_register_requires_a_session_token(client):
    assert client.post("/api/v1/push/register", json={"push_token": "tok-abc"}).status_code == 401


def test_register_creates_a_subscription(client, db_session, player):
    pid, sess = player

    response = client.post(
        "/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess)
    )

    assert response.status_code == 200
    assert response.json() == {"is_subscribed": True}

    row = _subscription(db_session, pid)
    assert row.push_token == "tok-abc"
    assert row.is_subscribed is True


def test_registering_twice_updates_instead_of_adding_a_row(client, db_session, player):
    """
    🔒 AC：先註冊 tok-abc 再註冊 tok-xyz → **只有 1 列**、token 是 tok-xyz。

    主鍵是 `player_id`，換手機／token 輪替時必須是覆蓋而非累積。累積的話，
    群發時得自己挑「最新的那個」，而那個判斷遲早會出錯然後把推播送到別人的
    舊裝置上。
    """
    pid, sess = player

    client.post("/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess))
    before = _subscription(db_session, pid).updated_at

    client.post("/api/v1/push/register", json={"push_token": "tok-xyz"}, headers=_auth(sess))

    db_session.expire_all()
    rows = db_session.query(models.PushSubscription).filter_by(player_id=pid).all()

    assert len(rows) == 1
    assert rows[0].push_token == "tok-xyz"
    assert rows[0].updated_at >= before


def test_response_does_not_echo_the_token(client, player):
    """
    客戶端本來就知道自己送了什麼。回傳 token 只是讓這個值多存在於一個地方
    （log、快取、錯誤回報）。
    """
    _, sess = player

    body = client.post(
        "/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess)
    ).json()

    assert set(body) == {"is_subscribed"}


def test_register_rejects_a_blank_token(client, player):
    _, sess = player

    assert client.post(
        "/api/v1/push/register", json={"push_token": ""}, headers=_auth(sess)
    ).status_code == 422


# ── 退訂 ───────────────────────────────────────────────────────────────

def test_unsubscribe_requires_a_session_token(client):
    assert client.post("/api/v1/push/unsubscribe").status_code == 401


def test_unsubscribe_flips_the_flag_without_deleting_the_row(client, db_session, player):
    """
    退訂用 `is_subscribed=false` 而不是刪列：token 還有用，重新訂閱時不需要
    重新註冊裝置。
    """
    pid, sess = player
    client.post("/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess))

    response = client.post("/api/v1/push/unsubscribe", headers=_auth(sess))

    assert response.status_code == 200
    assert response.json() == {"is_subscribed": False}

    row = _subscription(db_session, pid)
    assert row is not None, "退訂把整列刪掉了——重新訂閱會需要重新註冊裝置"
    assert row.is_subscribed is False
    assert row.push_token == "tok-abc"


def test_unsubscribe_without_registering_is_not_an_error(client, player):
    """
    「我不想收推播」對一個從沒註冊過的人來說已經成立。回 404 只會讓客戶端要為
    一個不是問題的狀況寫處理分支。
    """
    _, sess = player

    assert client.post("/api/v1/push/unsubscribe", headers=_auth(sess)).status_code == 200


def test_re_registering_after_unsubscribe_resubscribes(client, db_session, player):
    """重新註冊視為重新訂閱——玩家會這樣做通常就是因為想再收到通知。"""
    pid, sess = player
    client.post("/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess))
    client.post("/api/v1/push/unsubscribe", headers=_auth(sess))

    client.post("/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess))

    assert _subscription(db_session, pid).is_subscribed is True


# ── 群發 ───────────────────────────────────────────────────────────────

def test_unsubscribed_players_receive_nothing(client, db_session, player):
    """
    🔒 AC：退訂後觸發推播，fake **沒有收到任何送出請求**。

    只把 `is_subscribed` 改成 false 但照送，這條就會紅。
    """
    _, sess = player
    client.post("/api/v1/push/register", json={"push_token": "tok-abc"}, headers=_auth(sess))
    client.post("/api/v1/push/unsubscribe", headers=_auth(sess))

    sender = FakePushSender()
    result = send_signal_to_subscribers(
        db_session, sender, payload=build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")
    )

    assert sender.call_count == 0
    assert result.sent == 0


def test_only_subscribed_players_are_sent_to(client, db_session):
    """
    🔒 AC：P（已訂閱）、Q（已退訂）、R（未註冊）→ fake 只收到 **P** 一筆。
    """
    p_id, p_sess = _make_player(client)
    q_id, q_sess = _make_player(client)
    _make_player(client)  # R：完全不註冊

    client.post("/api/v1/push/register", json={"push_token": "tok-p"}, headers=_auth(p_sess))
    client.post("/api/v1/push/register", json={"push_token": "tok-q"}, headers=_auth(q_sess))
    client.post("/api/v1/push/unsubscribe", headers=_auth(q_sess))

    sender = FakePushSender()
    send_signal_to_subscribers(
        db_session, sender, payload=build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")
    )

    assert [token for token, _ in sender.sent] == ["tok-p"]


def test_one_failed_send_does_not_stop_the_batch(db_session, client):
    """
    一個裝置的 token 失效不該讓其他人都收不到（同 B8 的原則）。
    """
    for i in range(3):
        pid, sess = _make_player(client)
        client.post("/api/v1/push/register", json={"push_token": f"tok-{i}"}, headers=_auth(sess))

    class _FailsOnSecond(FakePushSender):
        def send(self, push_token: str, payload: PushPayload) -> bool:
            self.sent.append((push_token, payload))
            if push_token == "tok-1":
                raise RuntimeError("推播平台拒絕了這個 token")
            return True

    sender = _FailsOnSecond()
    result = send_signal_to_subscribers(
        db_session, sender, payload=build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")
    )

    assert result.sent == 2
    assert result.failed == 1


# ── payload 隱私 ──────────────────────────────────────────────────────

def test_payload_contains_no_coordinates_or_other_players():
    """
    🔒 AC：payload 全文找不到任何經緯度數值、也沒有其他 `player_id`。

    對照 CONTEXT.md：原始 GPS 只在驗證期間使用後即丟棄。
    """
    payload = build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")

    text = f"{payload.title}{payload.body}{payload.spirit_id}"

    assert str(_LAT) not in text
    assert str(_LON) not in text
    assert "25.0" not in text and "121." not in text
    assert set(payload.__dataclass_fields__) == {"title", "body", "spirit_id"}


def test_payload_builder_cannot_receive_coordinates():
    """
    `build_signal_payload()` **不收經緯度參數**——它沒有能力洩漏位置，
    所以不用靠紀律去記得別放。
    """
    import inspect

    params = set(inspect.signature(build_signal_payload).parameters)

    assert params == {"spirit_name", "spirit_id"}


def test_payload_carries_no_narrative_body():
    """
    推播是通知不是內容。敘事全文會經過第三方推播平台（FCM／APNs）並留在那裡的
    紀錄裡——玩家點進來後才呼叫既有端點取內容。
    """
    payload = build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")

    assert len(payload.body) < 40


def test_fake_sender_does_not_record_player_ids():
    """連測試替身都不該多存一份玩家識別。"""
    sender = FakePushSender()

    assert not hasattr(sender, "player_ids")


# ── 服務層可獨立測試 ───────────────────────────────────────────────────

def test_service_functions_work_without_http(db_session, client):
    """AC：推播觸發服務函式可獨立測試。"""
    pid, _ = _make_player(client)

    register_push_token(db_session, player_id=pid, push_token="tok-direct")
    sender = FakePushSender()
    send_signal_to_subscribers(
        db_session, sender, payload=build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")
    )
    assert sender.call_count == 1

    unsubscribe(db_session, player_id=pid)
    sender2 = FakePushSender()
    send_signal_to_subscribers(
        db_session, sender2, payload=build_signal_payload(spirit_name="龍山寺", spirit_id="longshan")
    )
    assert sender2.call_count == 0
