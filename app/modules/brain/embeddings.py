"""
B2 需要的「使用者輸入 → embedding」介面，供 `memory.retrieve_similar_memories`
（B6）的 Top-K 檢索使用。

`memory.py` 的模組註解已經講清楚：embedding 模型本身還沒拍板（SDD 第10節
「待定案」），把「用哪個模型把文字轉成向量」擋在 B6 外面；B2 是那句話說的
「更上層」。所以這裡只定義抽象介面 ＋ 測試用 fake，跟 B1（`gemini.py`）的
`GeminiClient` / `FakeGeminiClient` 是同一種取捨：真實實作留給模型選定
之後再補，不在這張票（#12）的範圍內。
"""
from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingClient(ABC):
    @abstractmethod
    def embed(self, text: str) -> Sequence[float]:
        """把文字轉成向量，維度必須跟 `brain.models.EMBEDDING_DIM` 一致。"""


class FakeEmbeddingClient(EmbeddingClient):
    """
    測試用。永遠回傳同一個固定向量——B2 的測試驗證的是組裝順序與資料隔離
    （player_id／spirit_id 過濾），不是 embedding 語意品質，向量內容不需要
    跟輸入文字有任何關係。

    `queries` 記下每次呼叫的文字，方便測試斷言「真的是拿玩家輸入去查」。
    """

    def __init__(self, vector: Sequence[float] | None = None):
        from app.modules.brain.models import EMBEDDING_DIM

        self._vector = list(vector) if vector is not None else [0.0] * EMBEDDING_DIM
        self.queries: list[str] = []

    def embed(self, text: str) -> Sequence[float]:
        self.queries.append(text)
        return self._vector
