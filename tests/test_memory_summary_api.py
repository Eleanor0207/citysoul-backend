"""
Ticket #37．GET /players/me/memory-summary 記憶摘要查詢。

驗收標準對照見 GitHub issue #37。用既有 B6 的 `write_memory` 造資料，
向量內容本身不重要（這支端點不做語意檢索，只是列出來），固定用同一個
方向即可。
"""
import uuid

import pytest

from app.modules.brain.memory import EMBEDDING_DIM, write_memory
from app.modules.brain.models import MemoryEmbedding


def _embedding() -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[0] = 1.0
    return v


@pytest.fixture
def player(client, db_session):
    body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    player_id = uuid.UUID(body["player_id"])
    yield player_id, body["session_token"]
    db_session.query(MemoryEmbedding).filter_by(player_id=player_id).delete()
    db_session.commit()


def _get(client, token):
    return client.get(
        "/api/v1/players/me/memory-summary", headers={"Authorization": f"Bearer {token}"}
    )


# ── 憑證（issue #37 AC1）─────────────────────────────────────────────────

def test_no_auth_header_returns_401(client):
    assert client.get("/api/v1/players/me/memory-summary").status_code == 401


def test_garbage_bearer_returns_401(client):
    response = client.get(
        "/api/v1/players/me/memory-summary", headers={"Authorization": "Bearer garbage"}
    )
    assert response.status_code == 401


# ── 依靈魂分組（issue #37 AC2）───────────────────────────────────────────

def test_groups_memories_by_spirit(client, db_session, player):
    pid, token = player
    spirit_a, spirit_b = f"test-spirit-a-{uuid.uuid4()}", f"test-spirit-b-{uuid.uuid4()}"

    write_memory(
        db_session, player_id=pid, spirit_id=spirit_a,
        summary_text="第一次見面聊了天氣。", embedding=_embedding(), source="dialogue_summary",
    )
    write_memory(
        db_session, player_id=pid, spirit_id=spirit_a,
        summary_text="問了廟的歷史。", embedding=_embedding(), source="dialogue_summary",
    )
    write_memory(
        db_session, player_id=pid, spirit_id=spirit_b,
        summary_text="第一次跟另一位靈魂對話。", embedding=_embedding(), source="dialogue_summary",
    )

    body = _get(client, token).json()

    assert set(body["memories_by_spirit"]) == {spirit_a, spirit_b}
    assert len(body["memories_by_spirit"][spirit_a]) == 2
    assert len(body["memories_by_spirit"][spirit_b]) == 1
    assert body["memories_by_spirit"][spirit_a][0]["summary_text"] == "第一次見面聊了天氣。"
    assert "created_at" in body["memories_by_spirit"][spirit_a][0]


# ── 隱私隔離（issue #37 AC3）─────────────────────────────────────────────

def test_only_returns_own_memories(client, db_session, player):
    pid, token = player
    spirit_id = f"test-spirit-{uuid.uuid4()}"
    other_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    other_pid = uuid.UUID(other_body["player_id"])

    write_memory(
        db_session, player_id=pid, spirit_id=spirit_id,
        summary_text="P的記憶。", embedding=_embedding(), source="dialogue_summary",
    )
    for i in range(5):
        write_memory(
            db_session, player_id=other_pid, spirit_id=spirit_id,
            summary_text=f"Q的記憶{i}。", embedding=_embedding(), source="dialogue_summary",
        )

    try:
        body = _get(client, token).json()
        assert list(body["memories_by_spirit"].keys()) == [spirit_id]
        assert len(body["memories_by_spirit"][spirit_id]) == 1
        assert body["memories_by_spirit"][spirit_id][0]["summary_text"] == "P的記憶。"
    finally:
        db_session.query(MemoryEmbedding).filter_by(player_id=other_pid).delete()
        db_session.commit()


# ── 冷啟動（issue #37 AC4）───────────────────────────────────────────────

def test_no_memories_returns_empty_result_not_404(client, player):
    _, token = player
    response = _get(client, token)
    assert response.status_code == 200
    assert response.json() == {"memories_by_spirit": {}}


# ── 不含 embedding（issue #37 AC5）───────────────────────────────────────

def test_response_does_not_contain_embedding_vectors(client, db_session, player):
    pid, token = player
    write_memory(
        db_session, player_id=pid, spirit_id=f"test-spirit-{uuid.uuid4()}",
        summary_text="測試記憶。", embedding=_embedding(), source="dialogue_summary",
    )

    body = _get(client, token).json()

    assert "embedding" not in str(body).lower()
    # 保險：確認也沒有任何 768 個元素的陣列混進回應裡。
    def _no_long_float_array(value) -> bool:
        if isinstance(value, list) and len(value) > 10 and all(isinstance(v, (int, float)) for v in value):
            return False
        if isinstance(value, dict):
            return all(_no_long_float_array(v) for v in value.values())
        if isinstance(value, list):
            return all(_no_long_float_array(v) for v in value)
        return True

    assert _no_long_float_array(body)
