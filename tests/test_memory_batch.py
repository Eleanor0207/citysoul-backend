"""
#40．B8 記憶摘要 nightly batch。

模型以 fake 注入——**不需要 GCP 憑證**。對真實 Postgres 跑。

⚠️ 最重要的兩組是：**列數確實下降**（不是只新增摘要）與**不跨玩家混用**。
"""
import uuid

import pytest

from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient
from app.modules.brain.memory import write_memory
from app.modules.brain.memory_batch import (
    SOURCE_BATCH,
    SOURCE_DIALOGUE,
    run_memory_batch,
)
from app.modules.brain.models import EMBEDDING_DIM, MemoryEmbedding

_SUMMARY = "這位玩家反覆問起廟埕的石獅子與早期市集。"


@pytest.fixture(autouse=True)
def _isolate_memories(db_session):
    """
    批次是**全域**操作（處理所有玩家），所以測試之間必須完全乾淨——
    別的測試留下的記憶會被這裡的批次一起處理掉。
    """
    db_session.query(MemoryEmbedding).delete()
    db_session.commit()
    yield
    db_session.query(MemoryEmbedding).delete()
    db_session.commit()


@pytest.fixture
def player_ids(client):
    """三個真實玩家（memory_embeddings 沒有 FK，但用真的比較貼近實際）。"""
    ids = []
    for _ in range(3):
        body = client.post(
            "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
        ).json()
        ids.append(uuid.UUID(body["player_id"]))
    return ids


def _write(db_session, player_id, spirit_id, text, *, source=SOURCE_DIALOGUE):
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=spirit_id,
        summary_text=text,
        embedding=[0.1] * EMBEDDING_DIM,
        source=source,
    )


def _count(db_session, player_id=None, source=None) -> int:
    db_session.expire_all()
    query = db_session.query(MemoryEmbedding)
    if player_id is not None:
        query = query.filter_by(player_id=player_id)
    if source is not None:
        query = query.filter_by(source=source)
    return query.count()


# ── 處理範圍 ───────────────────────────────────────────────────────────

def test_only_players_with_new_memories_are_processed(db_session, player_ids):
    """
    AC：P 有 12 筆、Q 有 5 筆、R 有 0 筆 → P 與 Q 各處理一次，**R 完全沒被處理**。

    沒有新記憶就不該浪費一次模型呼叫。
    """
    p, q, r = player_ids
    for i in range(12):
        _write(db_session, p, "spirit-a", f"P 的記憶 {i}")
    for i in range(5):
        _write(db_session, q, "spirit-a", f"Q 的記憶 {i}")

    client = FakeGeminiClient(response=_SUMMARY)
    result = run_memory_batch(db_session, client)

    assert result.groups_processed == 2
    assert client.call_count == 2
    assert _count(db_session, player_id=r) == 0


def test_summary_is_written_with_the_batch_source(db_session, player_ids):
    """AC：摘要以 `source='nightly_batch'` 寫入，可與 `dialogue_summary` 區分。"""
    p = player_ids[0]
    for i in range(5):
        _write(db_session, p, "spirit-a", f"記憶 {i}")

    run_memory_batch(db_session, FakeGeminiClient(response=_SUMMARY))

    assert _count(db_session, player_id=p, source=SOURCE_BATCH) == 1
    assert _count(db_session, player_id=p, source=SOURCE_DIALOGUE) == 0


# ── 壓縮：列數確實下降 ─────────────────────────────────────────────────

def test_row_count_drops_after_compaction(db_session, player_ids):
    """
    🔒 AC：20 筆 → 明顯少於 20。

    ⚠️ 這條刻意不只驗「批次有跑完」。一個**只新增摘要卻不清理舊記錄**的實作
    也會跑完，但記憶仍然無限成長——那正是這個工作包要解決的問題。

    AC 指定要做 mutation 驗證的其中一條。
    """
    p = player_ids[0]
    for i in range(20):
        _write(db_session, p, "spirit-a", f"記憶 {i}")

    before = _count(db_session, player_id=p)
    result = run_memory_batch(db_session, FakeGeminiClient(response=_SUMMARY))
    after = _count(db_session, player_id=p)

    assert before == 20
    assert after == 1
    assert after < before
    assert result.rows_before > result.rows_after


def test_memories_are_compacted_per_spirit(db_session, player_ids):
    """
    分組鍵是 `(player_id, spirit_id)`——同一個玩家對不同靈魂的記憶不該被摘進
    同一段文字。玩家對天文館講過的話，不是他對龍山寺的記憶。
    """
    p = player_ids[0]
    for i in range(3):
        _write(db_session, p, "spirit-a", f"A 的記憶 {i}")
        _write(db_session, p, "spirit-b", f"B 的記憶 {i}")

    result = run_memory_batch(db_session, FakeGeminiClient(response=_SUMMARY))

    assert result.groups_processed == 2
    assert _count(db_session, player_id=p) == 2  # 兩個靈魂各一列摘要


def test_summary_embedding_has_the_right_dimension(db_session, player_ids):
    """
    摘要向量用來源記憶的質心（embedding 模型尚未接上，見模組註解）。
    維度必須對，否則寫不進 pgvector 欄位。
    """
    p = player_ids[0]
    for i in range(3):
        _write(db_session, p, "spirit-a", f"記憶 {i}")

    run_memory_batch(db_session, FakeGeminiClient(response=_SUMMARY))

    db_session.expire_all()
    row = db_session.query(MemoryEmbedding).filter_by(player_id=p).one()
    assert len(list(row.embedding)) == EMBEDDING_DIM


# ── 隱私：不跨玩家混用 ─────────────────────────────────────────────────

def test_summaries_never_mix_players(db_session, player_ids):
    """
    🔒 AC：P 與 Q 對**同一個靈魂**各有記憶，P 的含 AAA、Q 的含 BBB
    → P 的摘要不含 BBB。

    同一靈魂是刻意設計的陷阱——若分組只用 `spirit_id` 而漏了 `player_id`，
    兩個玩家的記憶會被摘進同一段文字，而那是隱私邊界的違反，不只是資料錯誤。
    """
    p, q, _ = player_ids
    for i in range(3):
        _write(db_session, p, "spirit-a", f"AAA-{i}")
        _write(db_session, q, "spirit-a", f"BBB-{i}")

    class _EchoClient(FakeGeminiClient):
        """把收到的 prompt 原樣回傳，讓測試看得出摘要看過哪些內容。"""

        def generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return prompt

    run_memory_batch(db_session, _EchoClient())

    db_session.expire_all()
    p_summary = db_session.query(MemoryEmbedding).filter_by(player_id=p).one().summary_text
    q_summary = db_session.query(MemoryEmbedding).filter_by(player_id=q).one().summary_text

    assert "AAA" in p_summary and "BBB" not in p_summary
    assert "BBB" in q_summary and "AAA" not in q_summary


def test_prompt_only_contains_one_players_memories(db_session, player_ids):
    """從 prompt 這一端再確認一次——摘要看到的內容就不含別人的。"""
    p, q, _ = player_ids
    _write(db_session, p, "spirit-a", "AAA-only")
    _write(db_session, q, "spirit-a", "BBB-only")

    client = FakeGeminiClient(response=_SUMMARY)
    run_memory_batch(db_session, client)

    for prompt in client.prompts:
        assert not ("AAA-only" in prompt and "BBB-only" in prompt)


# ── 失敗不中斷整批 ─────────────────────────────────────────────────────

def test_one_failing_player_does_not_stop_the_batch(db_session, player_ids):
    """
    🔒 AC：只在處理 Q 時拋例外 → P 與 R 正常完成、Q 被跳過、批次正常結束。

    一個玩家的摘要失敗不該讓當晚所有人的記憶都沒被壓縮。

    AC 指定要做 mutation 驗證的其中一條。
    """
    p, q, r = player_ids
    _write(db_session, p, "spirit-a", "P 的記憶")
    _write(db_session, q, "spirit-a", "Q 的記憶")
    _write(db_session, r, "spirit-a", "R 的記憶")

    class _FailsForQ(FakeGeminiClient):
        def generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "Q 的記憶" in prompt:
                raise RuntimeError("模型對這一組炸了")
            return _SUMMARY

    result = run_memory_batch(db_session, _FailsForQ())

    assert result.groups_processed == 2
    assert result.groups_skipped == 1
    assert len(result.failures) == 1

    # P 與 R 被壓縮了，Q 的原始記錄還在（沒有被吞掉）。
    assert _count(db_session, player_id=p, source=SOURCE_BATCH) == 1
    assert _count(db_session, player_id=r, source=SOURCE_BATCH) == 1
    assert _count(db_session, player_id=q, source=SOURCE_DIALOGUE) == 1
    assert _count(db_session, player_id=q, source=SOURCE_BATCH) == 0


def test_generation_fallback_does_not_become_a_permanent_memory(db_session, player_ids):
    """
    🔒 模型回退時**不寫 fallback 文字進長期記憶**，而是保留原始記錄。

    寫進去的話，「城市靈魂安靜地看著你」會變成這位玩家的永久記憶，而且原始
    記錄已經被刪掉，救不回來。
    """
    p = player_ids[0]
    _write(db_session, p, "spirit-a", "原始記憶")

    result = run_memory_batch(db_session, FakeGeminiClient(response=FALLBACK_REPLY))

    assert result.groups_skipped == 1
    assert _count(db_session, player_id=p, source=SOURCE_DIALOGUE) == 1
    assert _count(db_session, player_id=p, source=SOURCE_BATCH) == 0


# ── 冪等 ───────────────────────────────────────────────────────────────

def test_running_twice_does_not_create_duplicate_summaries(db_session, player_ids):
    """
    AC：同一天連續執行兩次，第二次沒有新增重複摘要。

    冪等是結構性的：只處理 `source='dialogue_summary'` 的記錄，壓縮後它們已經
    不存在，所以第二次找不到東西可做。排程重試因此安全，不需要另外記
    「今天跑過了沒」。
    """
    p = player_ids[0]
    for i in range(5):
        _write(db_session, p, "spirit-a", f"記憶 {i}")

    run_memory_batch(db_session, FakeGeminiClient(response=_SUMMARY))
    after_first = _count(db_session, player_id=p)

    client = FakeGeminiClient(response=_SUMMARY)
    result = run_memory_batch(db_session, client)

    assert _count(db_session, player_id=p) == after_first
    assert result.groups_processed == 0
    assert client.call_count == 0, "第二次執行仍然呼叫了模型"


def test_empty_database_is_a_no_op(db_session):
    """完全沒有記憶時正常結束，不呼叫模型。"""
    client = FakeGeminiClient()

    result = run_memory_batch(db_session, client)

    assert result.groups_processed == 0
    assert client.call_count == 0
