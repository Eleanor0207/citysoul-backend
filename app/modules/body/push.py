"""
S11．訊號推播（後端側，issue #39）。

「城市靈魂的訊號」——當日情境或解鎖事件的通知。

## 推播是通知，不是內容

payload 只帶標題、內文與一個 `spirit_id`。玩家點進來之後才呼叫既有端點
（`/spirits/{placeId}/daily-event` 等）取實際內容。

這個分界不只是設計偏好：推播會經過第三方推播平台（FCM／APNs），內容在那裡
留有紀錄。把敘事全文塞進 payload，等於把玩家的遊戲內容複製一份到我們控制不了
的地方。

## 🔒 payload 不含位置，也不含其他玩家

CONTEXT.md：原始 GPS 只在驗證期間使用後即丟棄。推播不需要知道玩家在哪裡——
它送的是「這個地標今天有事」，不是「你附近有事」。

`build_signal_payload()` 因此**不收經緯度參數**。它沒有能力洩漏位置，就不用
靠紀律去記得別放。

## 退訂是硬邊界

`send_signal_to_subscribers()` 只查 `is_subscribed=true` 的列。退訂的玩家不會
出現在名單裡——不是「送出前再檢查一次」，是從一開始就不在集合中。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.modules.body.models import PushSubscription

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PushPayload:
    """
    推播內容。frozen——組好之後不該在送出前被改寫。

    🔒 欄位刻意只有這三個。沒有座標、沒有其他玩家的任何東西、沒有敘事全文。
    """

    title: str
    body: str
    spirit_id: str


class PushSender(ABC):
    """
    推播平台的抽象介面（FCM／APNs）。

    呼叫端只依賴這個，所以測試注入 fake **不會真的送出任何推播**——那是這個
    抽象存在最重要的理由，不是為了將來換平台。
    """

    @abstractmethod
    def send(self, push_token: str, payload: PushPayload) -> bool:
        """送出一則推播。失敗回 False，不拋例外。"""


class FakePushSender(PushSender):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同其他 fake。

    ⚠️ 記錄的是 `(push_token, payload)`，**不記 player_id**——連測試替身都不該
    多存一份玩家識別。
    """

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.sent: list[tuple[str, PushPayload]] = []

    def send(self, push_token: str, payload: PushPayload) -> bool:
        self.sent.append((push_token, payload))
        return self.succeed

    @property
    def call_count(self) -> int:
        return len(self.sent)


@dataclass
class PushResult:
    sent: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def register_push_token(db: Session, *, player_id, push_token: str) -> PushSubscription:
    """
    註冊或更新推播 token。**重複註冊是更新，不是新增列。**

    主鍵是 `player_id`，所以這裡是「有就改、沒有就建」。換手機或 token 輪替時
    舊的直接被覆蓋，不會留下失效的列。
    """
    row = db.query(PushSubscription).filter_by(player_id=player_id).first()

    if row is None:
        row = PushSubscription(player_id=player_id, push_token=push_token, is_subscribed=True)
        db.add(row)
    else:
        row.push_token = push_token
        # 重新註冊視為重新訂閱——玩家會這樣做通常就是因為想再收到通知。
        row.is_subscribed = True

    db.commit()
    db.refresh(row)
    return row


def unsubscribe(db: Session, *, player_id) -> bool:
    """
    退訂。回傳是否真的有一列被改（沒註冊過的玩家回 False，不是錯誤）。

    用 `is_subscribed=false` 而不是刪列：token 還有用，重新訂閱時不需要重新
    註冊裝置。
    """
    row = db.query(PushSubscription).filter_by(player_id=player_id).first()
    if row is None:
        return False

    row.is_subscribed = False
    db.commit()
    return True


def build_signal_payload(*, spirit_name: str, spirit_id: str) -> PushPayload:
    """
    組出「城市靈魂的訊號」通知。

    ⚠️ **不收經緯度參數**，所以它沒有能力洩漏位置——不用靠紀律去記得別放。
    也不帶敘事全文：那會被複製到第三方推播平台的紀錄裡。
    """
    return PushPayload(
        title=f"{spirit_name}有訊號",
        body="城市靈魂今天有話想說。點開看看。",
        spirit_id=spirit_id,
    )


def send_signal_to_subscribers(
    db: Session, sender: PushSender, *, payload: PushPayload
) -> PushResult:
    """
    對所有**已訂閱**的玩家群發。

    🔒 退訂的玩家從一開始就不在查詢結果裡——不是「送出前再檢查一次」。
    那個差別在於：後者只要有人漏寫一次檢查就會送出去，前者不可能。

    單一送出失敗不中斷整批，理由同 B8：一個裝置的 token 失效不該讓其他人都
    收不到。
    """
    result = PushResult()

    subscriptions = db.query(PushSubscription).filter_by(is_subscribed=True).all()

    for subscription in subscriptions:
        try:
            if sender.send(subscription.push_token, payload):
                result.sent += 1
            else:
                result.failed += 1
                result.failures.append(subscription.push_token)
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            result.failures.append(subscription.push_token)
            logger.warning("推播送出失敗：%s: %s", type(exc).__name__, exc)

    return result
