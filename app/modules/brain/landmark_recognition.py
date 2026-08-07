"""
B13．地標視覺辨識（#22，雲端 Gemini 多模態，即用即丟）。

**隱私是這支模組的核心約束**（SDD §7.7）：`image_bytes` 只在記憶體處理，
辨識完成（成功或失敗）立即捨棄——不寫入任何持久化儲存（含 Cloud
Storage）、不留作訓練資料、不做任何模組層級的快取或清單持有。這支模組
從頭到尾沒有寫檔案、沒有上傳物件儲存的程式碼；圖片位元組只活在單次函式
呼叫的區域變數裡。

Gemini 呼叫失敗／逾時回傳 `False`（fallback），**不阻擋任務完成**——這裡
只回一個 bool，不拋例外，讓呼叫端（quest 完成流程）自己決定「辨識失敗只
是拿不到特別徽章，不影響任務本身」，這支函式不需要知道也不需要管任務
邏輯。
"""
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.body.models import Spirit

# 獨立於 B1 的執行緒池：辨識呼叫量遠低於對話，沒有理由跟對話生成搶同一批
# 執行緒；用量互相影響只會讓兩邊的逾時判斷都失去意義。
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="landmark-recognition")

_PROMPT_TEMPLATE = "這張照片拍的是不是「{landmark_name}」？只回答 YES 或 NO，不要有其他文字。"


class LandmarkRecognitionClient(ABC):
    """
    地標視覺辨識的抽象介面。真實實作呼叫 Vertex AI 多模態；測試注入
    `FakeLandmarkRecognitionClient`，完全不需要 GCP 憑證。
    """

    @abstractmethod
    def recognize(self, image_bytes: bytes, landmark_name: str) -> bool:
        """判斷 `image_bytes` 是否為 `landmark_name`。失敗一律回 `False`，不拋例外。"""


class VertexAILandmarkRecognitionClient(LandmarkRecognitionClient):
    """
    真實實作。跟 B1（`gemini.VertexAIGeminiClient`）共用同一套 ADC client
    建構邏輯（`gemini.create_genai_client`）——差別只在這裡送的是多模態內容
    （文字＋圖片），不是純文字，所以不能直接重用 `GeminiClient.generate`
    那個純文字介面。
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        client_factory: Callable[[], object] | None = None,
    ):
        from app.modules.brain.gemini import create_genai_client

        # 重用 B1 已經拍板的模型與逾時設定（ADR-0001：單一快速模型），
        # 不另開一套設定——辨識跟對話生成面對的是同一個「玩家站在現場等」
        # 的延遲壓力。
        self._model_name = model_name or settings.gemini_model
        self._timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._client_factory = client_factory or create_genai_client
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def recognize(self, image_bytes: bytes, landmark_name: str) -> bool:
        try:
            from google.genai import types

            client = self._ensure_client()
            prompt = _PROMPT_TEMPLATE.format(landmark_name=landmark_name)
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

            future = _EXECUTOR.submit(
                client.models.generate_content,
                model=self._model_name,
                contents=[prompt, image_part],
            )
            done, _ = futures_wait([future], timeout=self._timeout_seconds)
            if not done:
                return False

            response = future.result()
            text = (getattr(response, "text", None) or "").strip().upper()
            return text.startswith("YES")
        except Exception:  # noqa: BLE001
            # 刻意攔截所有例外——辨識失敗只是拿不到徽章，不該讓例外往外拋、
            # 中斷任務完成流程（同 B1 的取捨）。
            return False


class FakeLandmarkRecognitionClient(LandmarkRecognitionClient):
    """測試用。`result` 固定回傳；`calls` 記下每次呼叫傳入的 landmark_name。"""

    def __init__(self, result: bool = True):
        self.result = result
        self.calls: list[str] = []

    def recognize(self, image_bytes: bytes, landmark_name: str) -> bool:
        self.calls.append(landmark_name)
        return self.result


def recognize_landmark(
    db: Session,
    image_bytes: bytes,
    spirit_id: str,
    *,
    client: LandmarkRecognitionClient | None = None,
) -> bool:
    """
    判斷 `image_bytes` 是否為 `spirit_id` 對應的地標。

    找不到地標，或底層 client 判斷失敗／逾時，一律回 `False`，不拋例外。
    """
    spirit = db.query(Spirit).filter_by(spirit_id=spirit_id).first()
    if spirit is None or not spirit.display_name:
        return False

    active_client = client or VertexAILandmarkRecognitionClient()
    return active_client.recognize(image_bytes, spirit.display_name)
