"""
Ticket #3．短期記憶 Redis 讀寫可驗證（B7）。

驗收標準對照見 GitHub issue #3。對真實本機 Redis（docker-compose）跑。
"""
import uuid

import pytest

from app.core.redis_client import (
    SESSION_TTL_SECONDS,
    _session_key,
    append_session_turn,
    get_session,
    redis_client,
)


@pytest.fixture
def unique_player_id():
    return f"test-player-{uuid.uuid4()}"


@pytest.fixture
def unique_spirit_id_redis():
    return f"test-spirit-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def _cleanup(unique_player_id, unique_spirit_id_redis):
    yield
    redis_client.delete(_session_key(unique_player_id, unique_spirit_id_redis))


def test_get_session_returns_empty_list_when_no_record_exists(
    unique_player_id, unique_spirit_id_redis
):
    assert get_session(unique_player_id, unique_spirit_id_redis) == []


def test_append_then_get_returns_the_written_turn(unique_player_id, unique_spirit_id_redis):
    append_session_turn(
        unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "你好"}
    )

    assert get_session(unique_player_id, unique_spirit_id_redis) == [
        {"role": "user", "text": "你好"}
    ]


def test_multiple_appends_accumulate_without_overwriting(
    unique_player_id, unique_spirit_id_redis
):
    append_session_turn(unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "第一句"})
    append_session_turn(
        unique_player_id, unique_spirit_id_redis, {"role": "assistant", "text": "回覆"}
    )
    append_session_turn(unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "第二句"})

    turns = get_session(unique_player_id, unique_spirit_id_redis)

    assert turns == [
        {"role": "user", "text": "第一句"},
        {"role": "assistant", "text": "回覆"},
        {"role": "user", "text": "第二句"},
    ]


def test_append_resets_ttl_to_thirty_minutes(unique_player_id, unique_spirit_id_redis):
    append_session_turn(unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "a"})
    key = _session_key(unique_player_id, unique_spirit_id_redis)
    ttl_after_first = redis_client.ttl(key)
    assert 0 < ttl_after_first <= SESSION_TTL_SECONDS

    # 手動把 TTL 調低，模擬「快過期」的情境，確認下一次 append 真的把它重設回滿額
    redis_client.expire(key, 5)
    assert redis_client.ttl(key) <= 5

    append_session_turn(unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "b"})
    ttl_after_second = redis_client.ttl(key)
    assert ttl_after_second > 5


def test_sessions_are_isolated_per_player_and_spirit(unique_player_id, unique_spirit_id_redis):
    other_player_id = f"test-player-{uuid.uuid4()}"
    other_spirit_id = f"test-spirit-{uuid.uuid4()}"

    append_session_turn(unique_player_id, unique_spirit_id_redis, {"role": "user", "text": "mine"})
    append_session_turn(other_player_id, unique_spirit_id_redis, {"role": "user", "text": "other player"})
    append_session_turn(unique_player_id, other_spirit_id, {"role": "user", "text": "other spirit"})

    assert get_session(unique_player_id, unique_spirit_id_redis) == [
        {"role": "user", "text": "mine"}
    ]

    redis_client.delete(_session_key(other_player_id, unique_spirit_id_redis))
    redis_client.delete(_session_key(unique_player_id, other_spirit_id))
