"""
B7．短期記憶（Redis）存取層。

Sprint1 先只做「連得上、能存取單一 key」這一層，還不實作對話 session 的
組裝邏輯（那是 B2 Prompt 組裝引擎要做的事，排在 Sprint3）。

Key 命名慣例（沿用整合文件第1節）：
    session:{player_id}:{spirit_id} -> 近期對話輪次（JSON string），依 session 逾時設定 TTL
"""
import json
from typing import Any

import redis

from app.core.config import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)

SESSION_TTL_SECONDS = 60 * 30  # 30 分鐘沒互動就過期，避免無限累積


def _session_key(player_id: str, spirit_id: str) -> str:
    return f"session:{player_id}:{spirit_id}"


def get_session(player_id: str, spirit_id: str) -> list[dict[str, Any]]:
    """讀取近期對話輪次，沒有就回傳空 list。"""
    raw = redis_client.get(_session_key(player_id, spirit_id))
    if raw is None:
        return []
    return json.loads(raw)


def append_session_turn(player_id: str, spirit_id: str, turn: dict[str, Any]) -> None:
    """
    寫入一輪對話（例如 {"role": "user", "text": "..."}）。
    每次寫入都重設 TTL，維持「30分鐘沒互動才清空」的行為。
    """
    turns = get_session(player_id, spirit_id)
    turns.append(turn)
    redis_client.set(
        _session_key(player_id, spirit_id),
        json.dumps(turns, ensure_ascii=False),
        ex=SESSION_TTL_SECONDS,
    )
