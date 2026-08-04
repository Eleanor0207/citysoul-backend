"""
Ticket #9．長期記憶 pgvector schema 與語意檢索（B6）。

驗收標準對照見 GitHub issue #9。

關於 ivfflat：SDD 指定的索引是「近似」最近鄰，理論上可能漏掉真正的前 K 名。
這些測試的資料量小到 Postgres 一定選 seq scan（精確比對），所以驗證的是
**檢索邏輯與排序規則本身正確**，不是「ivfflat 在大量資料下的召回率」——
後者要等有真實資料量時另外量測，不是這張 ticket 能回答的。
"""
import uuid

import pytest

from app.modules.brain.memory import (
    EmbeddingDimensionError,
    retrieve_similar_memories,
    write_memory,
)
from app.modules.brain.models import EMBEDDING_DIM, MemoryEmbedding


def _vec(**components: float) -> list[float]:
    """
    手造一個 768 維向量：只有指定的幾個索引有值，其餘為 0。

    用 `_vec(**{"0": 1.0})` 這種稀疏寫法是為了讓 cosine 角度可以用手算驗證，
    測試斷言的排序才不是「跑出來是什麼就寫什麼」。
    """
    v = [0.0] * EMBEDDING_DIM
    for index, value in components.items():
        v[int(index)] = value
    return v


# 查詢向量指向「第 0 軸」。以下三個記憶向量與它的 cosine 相似度分別是：
#   NEAR  : 1 / sqrt(1 + 0.1^2)  ≈ 0.995
#   MIDDLE: 1 / sqrt(1 + 1^2)    ≈ 0.707
#   FAR   : 0                    （完全正交）
QUERY = _vec(**{"0": 1.0})
NEAR = _vec(**{"0": 1.0, "1": 0.1})
MIDDLE = _vec(**{"0": 1.0, "1": 1.0})
FAR = _vec(**{"1": 1.0})


@pytest.fixture
def player_id():
    return uuid.uuid4()


@pytest.fixture(autouse=True)
def _cleanup(db_session):
    """每個測試跑完清掉自己寫進去的記憶，避免互相污染排序結果。"""
    yield
    db_session.query(MemoryEmbedding).filter(
        MemoryEmbedding.spirit_id.like("test-spirit-%")
    ).delete(synchronize_session=False)
    db_session.commit()


# ── 寫入 ───────────────────────────────────────────────────────────────

def test_write_memory_persists_all_fields(db_session, player_id, unique_spirit_id):
    row = write_memory(
        db_session,
        player_id=player_id,
        spirit_id=unique_spirit_id,
        summary_text="玩家問了天文館的圓頂是什麼時候蓋的",
        embedding=NEAR,
        source="dialogue_summary",
    )

    assert row.memory_id is not None
    assert row.created_at is not None

    stored = db_session.query(MemoryEmbedding).filter_by(memory_id=row.memory_id).one()
    assert stored.player_id == player_id
    assert stored.spirit_id == unique_spirit_id
    assert stored.summary_text == "玩家問了天文館的圓頂是什麼時候蓋的"
    assert stored.source == "dialogue_summary"
    assert list(stored.embedding) == pytest.approx(NEAR)


def test_write_memory_allows_multiple_rows_per_player_spirit(
    db_session, player_id, unique_spirit_id
):
    """同一玩家對同一靈魂可以累積多筆記憶——不做去重／覆寫。"""
    for text in ("第一次對話", "第二次對話"):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=unique_spirit_id,
            summary_text=text,
            embedding=NEAR,
            source="dialogue_summary",
        )

    count = (
        db_session.query(MemoryEmbedding)
        .filter_by(player_id=player_id, spirit_id=unique_spirit_id)
        .count()
    )
    assert count == 2


def test_write_memory_does_not_require_existing_player_row(
    db_session, unique_spirit_id
):
    """
    WBS-API 決策4：player_id 只做值的邏輯關聯，不建跨 schema 實體外鍵。

    這裡刻意用一個 public.players 裡根本不存在的 player_id 寫入——會成功，
    就證明沒有外鍵約束。哪天有人「順手」加上 ForeignKey，這個測試會紅。
    """
    orphan_player_id = uuid.uuid4()
    row = write_memory(
        db_session,
        player_id=orphan_player_id,
        spirit_id=unique_spirit_id,
        summary_text="這個玩家不存在於 players 表",
        embedding=NEAR,
        source="nightly_batch",
    )
    assert row.player_id == orphan_player_id


@pytest.mark.parametrize("bad_length", [1, EMBEDDING_DIM - 1, EMBEDDING_DIM + 1])
def test_write_memory_rejects_wrong_dimension(
    db_session, player_id, unique_spirit_id, bad_length
):
    with pytest.raises(EmbeddingDimensionError):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=unique_spirit_id,
            summary_text="維度不對",
            embedding=[0.1] * bad_length,
            source="dialogue_summary",
        )


# ── 檢索排序 ───────────────────────────────────────────────────────────

@pytest.fixture
def three_memories(db_session, player_id, unique_spirit_id):
    """寫入順序刻意跟相似度順序不同，確認排序不是靠寫入先後或主鍵。"""
    for label, vector in (("far", FAR), ("near", NEAR), ("middle", MIDDLE)):
        write_memory(
            db_session,
            player_id=player_id,
            spirit_id=unique_spirit_id,
            summary_text=label,
            embedding=vector,
            source="dialogue_summary",
        )
    return player_id, unique_spirit_id


def test_retrieval_orders_by_cosine_similarity(db_session, three_memories):
    pid, sid = three_memories

    results = retrieve_similar_memories(
        db_session, player_id=pid, spirit_id=sid, query_embedding=QUERY, k=3
    )

    assert [r.summary_text for r in results] == ["near", "middle", "far"]


def test_retrieval_respects_k(db_session, three_memories):
    pid, sid = three_memories

    results = retrieve_similar_memories(
        db_session, player_id=pid, spirit_id=sid, query_embedding=QUERY, k=2
    )

    assert [r.summary_text for r in results] == ["near", "middle"]


def test_retrieval_ordering_flips_with_query_direction(db_session, three_memories):
    """
    把查詢向量轉向「第 1 軸」，排序就該整個反過來。

    這條是為了確認排序真的來自查詢向量與資料的夾角，而不是資料本身的某種
    固定順序（例如剛好照寫入順序或 summary_text 排）。
    """
    pid, sid = three_memories

    results = retrieve_similar_memories(
        db_session, player_id=pid, spirit_id=sid, query_embedding=_vec(**{"1": 1.0}), k=3
    )

    assert [r.summary_text for r in results] == ["far", "middle", "near"]


def test_retrieval_uses_cosine_not_euclidean(db_session, player_id, unique_spirit_id):
    """
    區分 cosine 與 L2（歐氏）距離。

    上面那組 near/middle/far 在 cosine 與 L2 之下**排序剛好一樣**，所以它們
    證明不了用的是哪種距離。這裡放一個方向與查詢完全相同、但長度是 10 倍的
    向量：cosine 距離是 0（最近），L2 距離卻有 9（比 near 的 0.1 遠得多）。
    索引若被改成 vector_l2_ops、或函式改用 l2_distance，這個測試就會紅。
    """
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=unique_spirit_id,
        summary_text="同方向但長度10倍",
        embedding=_vec(**{"0": 10.0}),
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=unique_spirit_id,
        summary_text="near",
        embedding=NEAR,
        source="dialogue_summary",
    )

    results = retrieve_similar_memories(
        db_session, player_id=player_id, spirit_id=unique_spirit_id, query_embedding=QUERY, k=2
    )

    assert [r.summary_text for r in results] == ["同方向但長度10倍", "near"]


def test_retrieval_k_larger_than_available_returns_all(db_session, three_memories):
    pid, sid = three_memories

    results = retrieve_similar_memories(
        db_session, player_id=pid, spirit_id=sid, query_embedding=QUERY, k=99
    )
    assert len(results) == 3


@pytest.mark.parametrize("k", [0, -1])
def test_retrieval_non_positive_k_returns_empty(db_session, three_memories, k):
    pid, sid = three_memories

    assert (
        retrieve_similar_memories(
            db_session, player_id=pid, spirit_id=sid, query_embedding=QUERY, k=k
        )
        == []
    )


def test_retrieval_with_no_memories_returns_empty(db_session, unique_spirit_id):
    """冷啟動：玩家還沒有任何長期記憶時回空 list，不是拋例外。"""
    results = retrieve_similar_memories(
        db_session,
        player_id=uuid.uuid4(),
        spirit_id=unique_spirit_id,
        query_embedding=QUERY,
        k=5,
    )
    assert results == []


def test_retrieval_rejects_wrong_dimension(db_session, three_memories):
    pid, sid = three_memories

    with pytest.raises(EmbeddingDimensionError):
        retrieve_similar_memories(
            db_session, player_id=pid, spirit_id=sid, query_embedding=[0.1] * 10, k=3
        )


# ── 隔離 ───────────────────────────────────────────────────────────────

def test_memories_are_isolated_by_spirit_id(db_session, player_id):
    """
    同一玩家、不同靈魂的記憶互不可見。

    刻意讓「另一個靈魂」那筆的向量跟查詢**更接近**——如果 spirit_id 只是
    排序上的加權而不是硬過濾，它就會擠進結果裡。
    """
    sid_a = f"test-spirit-{uuid.uuid4()}"
    sid_b = f"test-spirit-{uuid.uuid4()}"

    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=sid_a,
        summary_text="屬於靈魂A（角度較遠）",
        embedding=MIDDLE,
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=player_id,
        spirit_id=sid_b,
        summary_text="屬於靈魂B（角度最近）",
        embedding=NEAR,
        source="dialogue_summary",
    )

    results = retrieve_similar_memories(
        db_session, player_id=player_id, spirit_id=sid_a, query_embedding=QUERY, k=10
    )

    assert [r.summary_text for r in results] == ["屬於靈魂A（角度較遠）"]


def test_memories_are_isolated_by_player_id(db_session, unique_spirit_id):
    """同一靈魂、不同玩家的記憶互不可見（同樣讓別人那筆更接近查詢向量）。"""
    me = uuid.uuid4()
    someone_else = uuid.uuid4()

    write_memory(
        db_session,
        player_id=me,
        spirit_id=unique_spirit_id,
        summary_text="我的記憶（角度較遠）",
        embedding=MIDDLE,
        source="dialogue_summary",
    )
    write_memory(
        db_session,
        player_id=someone_else,
        spirit_id=unique_spirit_id,
        summary_text="別人的記憶（角度最近）",
        embedding=NEAR,
        source="dialogue_summary",
    )

    results = retrieve_similar_memories(
        db_session, player_id=me, spirit_id=unique_spirit_id, query_embedding=QUERY, k=10
    )

    assert [r.summary_text for r in results] == ["我的記憶（角度較遠）"]
