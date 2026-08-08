"""
B8．記憶摘要 nightly batch（issue #40）。

把玩家累積的對話記憶壓縮成摘要，避免 `brain.memory_embeddings` 無限成長。

## 壓縮，不是只新增

⚠️ 這個模組**會刪掉被摘要的來源記錄**。只新增摘要卻不清理的實作也會「跑完」，
但記憶仍然無限成長——那正是這個工作包要解決的問題。

## 隱私邊界

🔒 摘要只包含**該玩家自己**的對話內容（CONTEXT.md「玩家記憶僅屬單一玩家」）。
分組鍵是 `(player_id, spirit_id)`，兩個都要——同一個靈魂底下有很多玩家的記憶，
只用 `spirit_id` 分組會把不同玩家的內容摘進同一段文字。

世界記憶不含任何玩家輸入，兩者絕不混入同一筆摘要。

## 單一玩家失敗不中斷整批

生成要呼叫模型，而模型會失敗。失敗時**只記 log 並跳過那一組**，其餘照常處理。
批次正常結束（非崩潰退出）——一個玩家的摘要失敗不該讓當晚所有人的記憶都沒被
壓縮。

## 冪等

只處理**還沒被壓縮過**的記錄（`source='dialogue_summary'`）。壓縮後那些列被
換成一列 `nightly_batch`，所以第二次執行找不到東西可做，自然不會產生重複摘要。
排程重試因此是安全的，不需要另外記「今天跑過了沒」。

## ⚠️ 摘要向量用來源記憶的質心

新的摘要列需要一個 `embedding`（NOT NULL），但 **repo 裡還沒有產生 embedding
的模組**——#9 只做了 schema 與檢索。

這裡用被摘要那些記憶的**向量平均（質心）**，而不是為此發明一個新的外部相依。
質心在語意上是合理的近似：它落在被摘要內容的中心，檢索時仍然找得到。

接上真正的 embedding 模型之後，`_summary_embedding()` 是唯一要改的地方。
"""
from __future__ import annotations

import logging
import uuid as uuid_module
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient
from app.modules.brain.models import EMBEDDING_DIM, MemoryEmbedding

logger = logging.getLogger(__name__)

SOURCE_DIALOGUE = "dialogue_summary"
SOURCE_BATCH = "nightly_batch"

_SUMMARY_PROMPT = """以下是一位玩家與同一個城市靈魂的多段對話記憶。

把它們壓縮成一段 100 字以內的摘要，保留這位玩家關心過什麼、問過什麼、
提過的個人細節。用第三人稱陳述，不要加入原文沒有的內容。

記憶：
{memories}

摘要："""


@dataclass
class BatchResult:
    """一次批次執行的結果，供排程與測試觀察。"""

    groups_processed: int = 0
    groups_skipped: int = 0
    rows_before: int = 0
    rows_after: int = 0
    failures: list[str] = field(default_factory=list)


def _summary_embedding(rows: list[MemoryEmbedding]) -> list[float]:
    """
    摘要的向量：來源記憶的質心。

    ⚠️ 這是 embedding 模型接上之前的近似做法（見模組註解）。質心落在被摘要
    內容的中心，檢索時仍然找得到；接上真正的模型之後，**這是唯一要改的地方**。
    """
    vectors = [list(row.embedding) for row in rows if row.embedding is not None]
    if not vectors:
        return [0.0] * EMBEDDING_DIM

    count = len(vectors)
    return [sum(values) / count for values in zip(*vectors)]


def _pending_groups(db: Session) -> dict[tuple, list[MemoryEmbedding]]:
    """
    還沒被壓縮過的記憶，依 `(player_id, spirit_id)` 分組。

    🔒 兩個鍵都要。只用 `spirit_id` 分組會把不同玩家的內容摘進同一段文字，
    而那是隱私邊界的違反，不只是資料錯誤。
    """
    rows = (
        db.query(MemoryEmbedding)
        .filter(MemoryEmbedding.source == SOURCE_DIALOGUE)
        .order_by(MemoryEmbedding.created_at)
        .all()
    )

    grouped: dict[tuple, list[MemoryEmbedding]] = {}
    for row in rows:
        grouped.setdefault((row.player_id, row.spirit_id), []).append(row)
    return grouped


def run_memory_batch(db: Session, client: GeminiClient) -> BatchResult:
    """
    執行一次批次。**永遠正常結束**——單組失敗只記 log 並跳過。
    """
    result = BatchResult()
    result.rows_before = db.query(MemoryEmbedding).count()

    groups = _pending_groups(db)

    for (player_id, spirit_id), rows in groups.items():
        try:
            _compact_group(db, client, player_id=player_id, spirit_id=spirit_id, rows=rows)
            result.groups_processed += 1
        except Exception as exc:  # noqa: BLE001
            # 刻意攔截所有例外。一個玩家的摘要失敗不該讓當晚所有人的記憶都沒
            # 被壓縮，而「哪些例外算預期」的清單一定會漏。
            db.rollback()
            result.groups_skipped += 1
            result.failures.append(f"{player_id}/{spirit_id}: {type(exc).__name__}: {exc}")
            logger.warning(
                "記憶摘要失敗，跳過這一組（player=%s spirit=%s）：%s: %s",
                player_id,
                spirit_id,
                type(exc).__name__,
                exc,
            )

    result.rows_after = db.query(MemoryEmbedding).count()
    return result


def _compact_group(
    db: Session,
    client: GeminiClient,
    *,
    player_id,
    spirit_id: str,
    rows: list[MemoryEmbedding],
) -> None:
    """
    把一組記憶壓成一列摘要，並刪掉來源記錄。

    ⚠️ **只摘要傳進來的那些列**，不重新查詢——重新查的話就有機會漏掉
    `player_id` 過濾，而那是隱私邊界的違反。
    """
    joined = "\n".join(f"- {row.summary_text}" for row in rows)
    text = client.generate(_SUMMARY_PROMPT.format(memories=joined))

    if not text or text == FALLBACK_REPLY:
        # 生成失敗當成這一組失敗——**不要**寫一個 fallback 文字進長期記憶。
        # 那會讓「城市靈魂安靜地看著你」變成這位玩家的永久記憶，而且原始記錄
        # 已經被刪掉，救不回來。
        raise RuntimeError("摘要生成失敗，保留原始記錄")

    summary = MemoryEmbedding(
        memory_id=uuid_module.uuid4(),
        player_id=player_id,
        spirit_id=spirit_id,
        summary_text=text,
        embedding=_summary_embedding(rows),
        source=SOURCE_BATCH,
    )
    db.add(summary)

    # 壓縮的意義在這裡：來源記錄被換掉，不是被複製。
    for row in rows:
        db.delete(row)

    db.commit()
