"""
B1．Vertex AI Gemini 串接與失敗回退（ADR-0001、ADR-0003）。

## 這個模組最重要的性質

**`generate()` 永遠回傳一個非空字串，永遠不拋例外。**

CONTEXT.md：「無合格輸入或生成失敗時使用人工預寫台詞」。模型暫時不可用不得
中斷召喚流程——玩家已經走到廟埕了，他不該因為我們的雲端服務打嗝而看到錯誤畫面。

所以呼叫端**不需要** try/except：

    reply = client.generate(prompt)   # 這行不會炸

代價是「模型失敗」與「模型回了這句話」在型別上無法區分。這是刻意的取捨；
真的需要區分時（例如觀察成功率）看 log 或 `last_failure_reason`，不要改成拋例外。

## 憑證

沒有任何憑證參數，見 ADR-0003。走 Application Default Credentials：
本機 `gcloud auth application-default login`，正式環境是 Cloud Run 綁定的
service account。程式碼在兩邊完全相同。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

# 逾時用的執行緒池。
#
# ⚠️ Vertex AI SDK 的 `generate_content()` **沒有** timeout 參數（1.71 實測），
# 所以上限只能加在外面。代價很明確：逾時之後那個執行緒仍在等 HTTP 回應，
# 我們只是不再理會它——真正的取消要等 SDK 支援。
#
# 這樣做仍然值得：玩家站在廟埕前，等 30 秒跟沒有回應是一樣的。與其讓他盯著
# 轉圈圈，不如 8 秒後給他一句角色會說的話。
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="gemini")

# 人工預寫的回退台詞。
#
# 刻意寫得像角色會說的話，而不是「系統忙碌中，請稍後再試」——玩家不該被拉出
# 情境。它同時是「我沒有答案」的合理表達，所以就算模型其實是好的、只是這次
# 逾時，這句話出現也不突兀。
#
# 這句話跟 router.py 的 FALLBACK_REPLY 是同一句，但**刻意各自持有**：
# 那邊是「沒命中預寫招呼」的回答，這邊是「模型失敗」的回答。兩者現在恰好
# 相同，但它們會因為不同的理由被改寫。
FALLBACK_REPLY = "（城市靈魂安靜地看著你）……這件事我還沒想清楚。要不要先跟我說說你眼前看到的？"


class GeminiClient(ABC):
    """
    對話生成的抽象介面。

    呼叫端（B2 對話組裝、B9 當日情境）只依賴這個介面，因此測試可以注入
    <see cref="FakeGeminiClient"/> 而**完全不需要 GCP 憑證**。
    """

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """產生回應。失敗時回傳 `FALLBACK_REPLY`，不拋例外。"""


class VertexAIGeminiClient(GeminiClient):
    """
    真實實作。ADR-0001：MVP 只用單一快速模型，不做 Flash/Pro 分流。

    SDK 在 `__init__` 才 import，不在模組頂端——這樣沒裝 `google-cloud-aiplatform`
    的環境（例如只跑單元測試的 CI）仍然可以 import 這個模組並使用 fake。
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        model_factory: Callable[[], Any] | None = None,
    ):
        """
        `model_factory` 讓測試注入一個會拋例外的假模型，藉此驗證**這個類別的**
        回退邏輯。用一個自己就回傳 fallback 的假 client 是測不到這條路徑的
        ——那只會驗證假物件本身。
        """
        self._model_name = model_name or settings.gemini_model
        self._max_output_tokens = max_output_tokens or settings.gemini_max_output_tokens
        self._timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._model_factory = model_factory or self._create_vertex_model
        self._model = None
        self.last_failure_reason: str | None = None

    def _create_vertex_model(self):
        import vertexai
        from vertexai.generative_models import GenerativeModel

        # 沒有傳 credentials：ADC 會自己找（ADR-0003）。
        vertexai.init(project=settings.gcp_project_id, location=settings.gcp_location)
        return GenerativeModel(self._model_name)

    def _ensure_model(self):
        if self._model is None:
            self._model = self._model_factory()
        return self._model

    def generate(self, prompt: str) -> str:
        self.last_failure_reason = None

        try:
            model = self._ensure_model()

            future = _EXECUTOR.submit(
                model.generate_content,
                prompt,
                generation_config={
                    # 長度上限在**呼叫參數**層級，不是靠 prompt 請模型自律。
                    # 這個值同時是單次呼叫的成本上限。
                    "max_output_tokens": self._max_output_tokens,
                },
            )

            # 刻意不用 `future.result(timeout=...)`：Python 3.11 起
            # `concurrent.futures.TimeoutError` **就是**內建的 `TimeoutError`，
            # 所以模型自己拋 TimeoutError 時會跟「我們主動放棄」混在一起，
            # last_failure_reason 會記錯。回退行為相同，但觀測數據會說謊。
            done, _ = futures_wait([future], timeout=self._timeout_seconds)

            if not done:
                return self._fall_back(f"超過 {self._timeout_seconds} 秒未回應")

            # 模型自己拋的例外在這裡重新浮現，交給外層的 except 處理。
            response = future.result()

            text = (getattr(response, "text", None) or "").strip()

            if not text:
                # 空回應在型別上是「成功」，但對玩家而言跟失敗沒有差別。
                # 常見原因是被安全過濾器擋掉——那也應該走回退。
                return self._fall_back("模型回傳空字串（可能被安全過濾器擋下）")

            return text

        except Exception as exc:  # noqa: BLE001
            # 刻意攔截所有例外。這裡不該有「哪些例外算預期」的清單——
            # 任何未預期的例外都不是讓召喚流程中斷的理由，而清單一定會漏。
            return self._fall_back(f"{type(exc).__name__}: {exc}")

    def _fall_back(self, reason: str) -> str:
        self.last_failure_reason = reason
        logger.warning("Gemini 呼叫失敗，回退人工預寫台詞：%s", reason)
        return FALLBACK_REPLY


class FakeGeminiClient(GeminiClient):
    """
    測試用。可以設定回應，也可以設定「下一次呼叫要丟什麼例外」。

    放在正式程式碼而不是 tests/ 底下，是因為 B2、B9、對話端點的測試都會用到它——
    放在 tests/ 會變成跨測試檔案 import，那種相依很快就會亂掉。
    """

    def __init__(self, response: str = "（測試用回應）"):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response

    @property
    def call_count(self) -> int:
        return len(self.prompts)
