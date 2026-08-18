"""
玩家自身資料的唯讀查詢（#33／#35／#36／#37）。

`GET /quests/daily`、`GET /resonance/{spiritId}`、`GET /profile`、
`GET /players/me/memory-summary` 共用這裡的計算。

## 為什麼集中在一個模組

`GET /profile` 是 Profile 畫面一次拿齊資料的彙總查詢，它跟另外兩支單面向查詢
回報的**必須是同一組數字**。各自重算的話，哪天有人改了門檻規則卻只改到一邊，
玩家會在兩個畫面看到不同的階段——而且不會有任何錯誤。

所以 `stage` 一律走 S5 的 `stage_for_value()`，三支端點都只是把它的結果轉成
各自的回應形狀。

## 唯讀，不推進狀態機

⚠️ 這裡的函式**不會 commit、不會修改任何資料列**。

任務狀態機的推進（跨日歸零、清除過期憑證、開始新嘗試）只發生在 `/summon`
（`quests.evaluate_on_summon`）。查詢端點如果順手把跨日的 `attempts_today`
歸零寫回去，玩家只要打開任務列表就等於做了一次狀態轉移——那是很難追查的副作用。

跨日的歸零因此是**計算出來的**：`attempts_date` 不是今天就當作 0，但不寫回。
下一次 `/summon` 才真的寫。
"""
from __future__ import annotations

import uuid as uuid_module
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.body.models import QuestProgress, Resonance, Spirit
from app.modules.body.quests import (
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    spirit_id_for_quest,
    taipei_today,
)
from app.modules.body.resonance import next_threshold, stage_for_value
from app.modules.brain.models import MemoryEmbedding


class SpiritNotFoundError(LookupError):
    """地標不存在或已下架。呼叫端轉 404。"""


def _player_uuid(player_id) -> uuid_module.UUID:
    return uuid_module.UUID(str(player_id))


def quest_view(progress: QuestProgress, *, today) -> dict:
    """
    把一列 `quest_progress` 轉成回應用的形狀。

    資料庫與 API 回應的 `status` 都只有 `in_progress` / `completed` 兩個值。
    `attempts_today` 是觀測欄位，不會產生額外的狀態。

    ## 跨日的 attempts_today 是算出來的，不寫回

    `attempts_date` 不是今天 → 對外呈現 0。真正的歸零由下一次 `/summon` 寫入。
    查詢端點不該有副作用。

    ## spirit_id 從 quest_id 反推

    ⚠️ `quest_progress` **沒有 `spirit_id` 欄位**，主鍵是
    `(player_id, quest_id)`。目前靠 `quest_id_for_spirit()` 的命名慣例
    （`{spirit_id}:daily`）反推。等真的有任務目錄表時，**這一行是唯一要改的
    地方**——所以它刻意集中在這裡，而不是散在三支端點裡各寫一次。
    """
    is_today = progress.attempts_date == today
    attempts_today = progress.attempts_today if is_today else 0

    status = STATUS_COMPLETED if progress.status == STATUS_COMPLETED else STATUS_IN_PROGRESS

    try:
        spirit_id = spirit_id_for_quest(progress.quest_id)
    except Exception:  # noqa: BLE001
        # 命名慣例以外的 quest_id（例如日後的任務目錄表）不該讓整個列表壞掉。
        spirit_id = progress.quest_id

    return {
        "quest_id": progress.quest_id,
        "spirit_id": spirit_id,
        "status": status,
        "attempts_today": attempts_today,
    }


def daily_quests(db: Session, *, player_id, now: datetime | None = None) -> list[dict]:
    """該玩家所有任務進度。沒有任何任務時回空 list——冷啟動是正常狀態。"""
    today = taipei_today(now or datetime.now(timezone.utc))

    rows = (
        db.query(QuestProgress)
        .filter(QuestProgress.player_id == _player_uuid(player_id))
        .order_by(QuestProgress.quest_id)
        .all()
    )
    return [quest_view(row, today=today) for row in rows]


def _resonance_value_for(db: Session, player_id, spirit_id: str) -> int:
    row = (
        db.query(Resonance)
        .filter_by(player_id=_player_uuid(player_id), spirit_id=spirit_id)
        .first()
    )
    return row.resonance_value if row else 0


def resonance_progress(db: Session, *, player_id, spirit_id: str) -> dict:
    """
    單一靈魂的共鳴進度。

    地標不存在或已下架時拋 `SpiritNotFoundError`——即使該玩家已經有共鳴值。
    下架的靈魂對玩家來說就是不存在（對齊其他所有靈魂查詢端點）。

    「還沒開始」回 0 而不是 404：前端要能直接畫一條 0/10 的進度條。
    """
    spirit = db.query(Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None or not spirit.is_active:
        raise SpiritNotFoundError(spirit_id)

    value = _resonance_value_for(db, player_id, spirit_id)

    return {
        "spirit_id": spirit_id,
        "resonance_value": value,
        # 🔒 一律從 value 重算。`resonance` 表**沒有** stage 欄位——0003 把它刪了，
        # 因為 `stage_for_value()` 從來沒讀過它（一個永遠不被信任的快取欄位，
        # 存在的唯一效果是讓下一個人誤用它）。
        "stage": stage_for_value(value),
        "next_threshold": next_threshold(value),
    }


def all_resonance(db: Session, *, player_id) -> list[dict]:
    """
    該玩家對所有靈魂的共鳴進度（`GET /profile` 用）。

    ⚠️ 這裡**不過濾 `is_active`**，跟 `resonance_progress()` 不同。理由：
    Profile 是玩家自己的歷程，一個靈魂下架不該讓他過去累積的共鳴值從個人頁
    消失——那看起來像資料遺失。單一靈魂查詢回 404 是因為那是「去看那個靈魂」，
    而它已經不在了。
    """
    rows = (
        db.query(Resonance)
        .filter(Resonance.player_id == _player_uuid(player_id))
        .order_by(Resonance.spirit_id)
        .all()
    )
    return [
        {
            "spirit_id": row.spirit_id,
            "resonance_value": row.resonance_value,
            # 同一支 stage_for_value()，不重算——三支端點必須回報同一組數字。
            "stage": stage_for_value(row.resonance_value),
        }
        for row in rows
    ]


def memory_summaries(db: Session, *, player_id) -> list[dict]:
    """
    該玩家的記憶摘要，依靈魂分組（#37）。

    🔒 **只回傳這個玩家自己的記憶**（CONTEXT.md「玩家記憶僅屬單一玩家」）。
    過濾條件是 `player_id`，不是 `spirit_id`——同一個靈魂底下有很多玩家的記憶。

    ⚠️ **不回傳 embedding 向量**。它是內部實作，對玩家沒有意義，而且 768 維
    浮點數會讓回應暴增數十 KB。這裡只 select 需要的欄位，不是查完整列再挑——
    查完整列的話，哪天有人在回應模型多加一個欄位就會把向量帶出去。
    """
    rows = (
        db.query(
            MemoryEmbedding.spirit_id,
            MemoryEmbedding.summary_text,
            MemoryEmbedding.created_at,
        )
        .filter(MemoryEmbedding.player_id == _player_uuid(player_id))
        .order_by(MemoryEmbedding.spirit_id, MemoryEmbedding.created_at.desc())
        .all()
    )

    grouped: dict[str, list[dict]] = {}
    for spirit_id, summary_text, created_at in rows:
        grouped.setdefault(spirit_id, []).append(
            {"summary_text": summary_text, "created_at": created_at}
        )

    return [
        {"spirit_id": spirit_id, "memories": memories}
        for spirit_id, memories in grouped.items()
    ]
