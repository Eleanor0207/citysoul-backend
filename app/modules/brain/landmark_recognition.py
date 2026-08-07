"""
B13．地標視覺辨識（issue #22；SDD §7.7；雲端 Gemini 多模態，即用即丟）。

## 隱私是這張票的核心約束

`image_bytes` 只在記憶體處理：組成這次多模態呼叫的請求內容、送出、拿到
辨識結果，**辨識完成（成功或失敗）立即捨棄**——不寫入任何持久化儲存
（含本機檔案、Cloud Storage），不留作訓練資料，也不留在任何模組層級的
變數或快取裡。呼叫本身會把影像位元組送到 Google 的雲端服務（這是「雲端
Gemini 多模態」的必要代價，SDD 已經接受這個前提），但**這支程式碼自己**
不能是第二個持久化的來源。

`recognize()` 的回傳型別刻意只有 `bool`：不回傳、不夾帶任何影像資料，
呼叫端拿到的是一個是非判斷，沒有辦法從回傳值反推出原始影像。

## 失敗不阻擋任務完成

CONTEXT.md：本機地標辨識成功時給予特別徽章，失敗不阻擋任務完成。呼叫失敗
（連線錯誤、逾時、5xx）一律回傳 `False`，不拋例外——玩家頂多是拿不到那個
徽章，不會因為辨識服務打嗝就卡住整個任務流程。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="landmark-recognition")


class LandmarkRecognizer(ABC):
    """
    地標視覺辨識的抽象介面。呼叫端只依賴這個介面，測試可以注入
    `FakeLandmarkRecognizer` 而完全不需要 GCP 憑證。
    """

    @abstractmethod
    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        """判斷 `image_bytes` 是不是 `spirit_id` 這個地標。失敗時回 `False`，不拋例外。"""


class VertexAILandmarkRecognizer(LandmarkRecognizer):
    """
    真實實作。SDK 延遲 import，沒裝 `google-genai` 的環境仍可 import 這個
    模組並使用 fake（同 `gemini.py` 的做法）。
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        `client_factory` 讓測試注入一個會拋例外的假 client，驗證**這個類別的**
        回退邏輯——用一個自己就回傳 `False` 的假 recognizer 測不到這條路徑。
        """
        self._model_name = model_name or settings.gemini_model
        self._timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._client_factory = client_factory or self._create_client
        self._client = None

        self.last_failure_reason: str | None = None

    def _create_client(self):
        import google.auth
        from google import genai

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return genai.Client(
            enterprise=True,
            project=settings.gcp_project_id,
            location=settings.gcp_location,
            credentials=credentials,
        )

    def _ensure_client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        self.last_failure_reason = None

        try:
            from google.genai import types

            client = self._ensure_client()

            # `image_bytes` 只活在這個函式呼叫的堆疊裡：組成請求內容、送出、
            # 拿到文字結果，函式結束後這個區域變數本身也不再被任何東西持有
            # ——沒有把它指派給 `self` 或任何模組層級變數。
            future = _EXECUTOR.submit(
                client.models.generate_content,
                model=self._model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    f"這張照片是不是「{spirit_id}」這個地標？只能回答 true 或 false，"
                    "不要加任何其他文字或標點。",
                ],
                config=types.GenerateContentConfig(max_output_tokens=8),
            )

            done, _ = futures_wait([future], timeout=self._timeout_seconds)
            if not done:
                return self._fall_back(f"超過 {self._timeout_seconds} 秒未回應")

            response = future.result()
            text = (getattr(response, "text", None) or "").strip().lower()
            return text.startswith("true")

        except Exception as exc:  # noqa: BLE001
            # 跟 GeminiClient／TTSClient 同樣的理由：任何未預期的例外都不該
            # 讓任務完成流程中斷，這裡不列「哪些例外算預期」的清單。
            return self._fall_back(f"{type(exc).__name__}: {exc}")

    def _fall_back(self, reason: str) -> bool:
        self.last_failure_reason = reason
        logger.warning("地標視覺辨識失敗，回傳 False（不阻擋任務完成）：%s", reason)
        return False


class FakeLandmarkRecognizer(LandmarkRecognizer):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同
    `gemini.FakeGeminiClient`——其他模組（例如 #44 紀念照片）的測試也會
    用到它。

    刻意不保留 `image_bytes` 本身，只記呼叫次數與收過的 `spirit_id`——連
    測試用的 fake 都不留一份影像，「即用即丟」不能只在真實實作裡做到。
    """

    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.spirit_ids_seen: list[str] = []

    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        self.spirit_ids_seen.append(spirit_id)
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.spirit_ids_seen)
