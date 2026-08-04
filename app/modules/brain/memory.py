"""
B6．長期記憶寫入與 Top-K 語意檢索。

這支模組刻意**只處理已經算好的向量**：`embedding` 是輸入參數，不是這裡算出來的。
SDD 第10節記載 embedding 模型與 Top-K 數字都還沒定案，把「用哪個模型把文字轉成
向量」擋在模組外面，之後換模型時這裡一行都不用改。

未來 B2 Prompt 組裝引擎需要「文字查詢→embedding→Top-K 檢索」的完整串接時，
會在更上層決定模型並組裝，不屬於這裡。
"""
import uuid
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.modules.brain.models import EMBEDDING_DIM, MemoryEmbedding


class EmbeddingDimensionError(ValueError):
    """向量維度跟 schema 對不上。"""


def _validate(embedding: Sequence[float]) -> list[float]:
    """
    維度不符時給出講清楚的錯誤，而不是讓 psycopg 丟一段看不懂的
    "expected 768 dimensions, not N"。換 embedding 模型是最可能觸發這條的情境，
    錯誤訊息要能直接指向原因。
    """
    values = list(embedding)
    if len(values) != EMBEDDING_DIM:
        raise EmbeddingDimensionError(
            f"embedding 維度必須是 {EMBEDDING_DIM}，收到 {len(values)}"
        )
    return values


def write_memory(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    summary_text: str,
    embedding: Sequence[float],
    source: str,
) -> MemoryEmbedding:
    """
    寫入一筆長期記憶。

    刻意不做去重或覆寫：同一玩家對同一靈魂可以有多筆記憶，這正是 Top-K 檢索
    要挑選的對象。「哪些對話值得被記成長期記憶」是呼叫端（B9 摘要／夜間批次）
    的判斷，不是這裡的。
    """
    row = MemoryEmbedding(
        player_id=uuid.UUID(str(player_id)),
        spirit_id=spirit_id,
        summary_text=summary_text,
        embedding=_validate(embedding),
        source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def retrieve_similar_memories(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    query_embedding: Sequence[float],
    k: int,
) -> list[MemoryEmbedding]:
    """
    取回該玩家對該靈魂、與查詢向量 cosine 距離最近的前 K 筆記憶。

    `player_id` + `spirit_id` 是硬性前置過濾，不是排序加權：玩家對天文館靈魂
    講過的話，不該在跟另一個靈魂對話時被檢索出來。這是資料隔離，不是相關性問題。

    回傳依相似度由近而遠排序（cosine 距離小的在前）。查無資料時回傳空 list，
    呼叫端要能處理「這個玩家還沒有任何長期記憶」的冷啟動情境。
    """
    if k <= 0:
        return []

    return (
        db.query(MemoryEmbedding)
        .filter(
            MemoryEmbedding.player_id == uuid.UUID(str(player_id)),
            MemoryEmbedding.spirit_id == spirit_id,
        )
        .order_by(MemoryEmbedding.embedding.cosine_distance(_validate(query_embedding)))
        .limit(k)
        .all()
    )
