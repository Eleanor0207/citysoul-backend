"""
S11．訊號推播服務模組（SDD 第3.1節 / CONTEXT.md）。

功能：
1. 提供獨立可測試之 PushNotifier 介面與 FakePushNotifier 實作。
2. dispatch_push_notifications：過濾僅對 is_subscribed=True 之有效訂閱玩家發送推播。
3. 嚴格隱私保護：Payload 絕不安裝/夾帶 GPS 座標或第三者玩家資訊。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.body import models

logger = logging.getLogger(__name__)


class PushNotifier(ABC):
    """推播服務介面。"""

    @abstractmethod
    def send_push(self, push_token: str, title: str, body: str, data: dict[str, Any] | None = None) -> bool:
        """發送單筆推播。"""


class FakePushNotifier(PushNotifier):
    """測試與模擬使用的推播服務。"""

    def __init__(self):
        self.sent_messages: list[dict[str, Any]] = []

    def send_push(self, push_token: str, title: str, body: str, data: dict[str, Any] | None = None) -> bool:
        self.sent_messages.append(
            {
                "push_token": push_token,
                "title": title,
                "body": body,
                "data": data or {},
            }
        )
        return True


def dispatch_push_notifications(
    db: Session,
    player_ids: list[uuid.UUID],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    notifier: PushNotifier | None = None,
) -> int:
    """
    對指定玩家清單發送訊號推播（自動跳過未訂閱或退訂玩家）。

    Returns:
        int: 成功送出之推播數量。
    """
    if notifier is None:
        notifier = FakePushNotifier()

    if not player_ids:
        return 0

    # 僅撈出 is_subscribed = True 的有效訂閱紀錄
    subscriptions = (
        db.query(models.PushSubscription)
        .filter(
            models.PushSubscription.player_id.in_(player_ids),
            models.PushSubscription.is_subscribed.is_(True),
        )
        .all()
    )

    sent_count = 0
    for sub in subscriptions:
        if sub.push_token:
            success = notifier.send_push(
                push_token=sub.push_token,
                title=title,
                body=body,
                data=data,
            )
            if success:
                sent_count += 1

    return sent_count
