"""
B13．地標視覺辨識（SDD 第7.7節 / CONTEXT.md）。

多模態地標辨識（即用即丟）：
- 隱私核心約束：image_bytes 僅於記憶體處理，辨識完成後立即釋放。
- 不寫入任何持久化儲存（包含 Cloud Storage）。
- 辨識失敗或逾時回傳 False，不阻擋任務完成。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any

from app.modules.brain.gemini import GeminiClient

logger = logging.getLogger(__name__)


class LandmarkRecognizer(ABC):
    """地標視覺辨識抽象介面。"""

    @abstractmethod
    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        """
        辨識傳入的照片是否符合目標地標。

        Returns:
            bool: 辨識成功回傳 True，不符或失敗回傳 False（不拋例外）。
        """


class FakeLandmarkRecognizer(LandmarkRecognizer):
    """
    測試與離線開發使用的假地標辨識器。
    不需要 GCP 憑證與真實照片。
    """

    def __init__(self, default_recognized: bool = True):
        self.default_recognized = default_recognized
        self.call_count = 0

    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        self.call_count += 1
        if not image_bytes:
            return False
        return self.default_recognized


class GeminiLandmarkRecognizer(LandmarkRecognizer):
    """
    使用 Gemini 多模態模型進行地標視覺辨識。
    """

    def __init__(self, gemini_client: GeminiClient | None = None):
        self.gemini_client = gemini_client

    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        if not image_bytes:
            return False

        try:
            from google import genai
            from google.genai import types

            client = genai.Client()
            prompt = (
                f"請判斷這張圖片是否為台北市地標 '{spirit_id}' 的建築或景象。"
                "若是請僅回答 'YES'，否則請僅回答 'NO'。"
            )
            image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[prompt, image_part],
            )
            text = (getattr(response, "text", "") or "").strip().upper()
            return "YES" in text
        except Exception as err:
            logger.warning("Gemini 地標辨識呼叫失敗，降級回傳 False: %s", err)
            return False
