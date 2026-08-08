"""
B13．地標視覺辨識（issue #22）。

玩家在現場拍一張照，我們判斷「這張照片拍的是不是這個地標」。成功給特別徽章，
**失敗不阻擋任務完成**（CONTEXT.md）——辨識失敗只是拿不到徽章，不是流程錯誤。

## 隱私是這個模組的核心約束（SDD §7.7）

`image_bytes` **只在記憶體處理，辨識完成後立即捨棄**：

- 不寫入任何持久化儲存（含 Cloud Storage）
- 不留作訓練資料
- **不放進任何模組層級的變數、快取或清單**

最後那條特別容易被違反。「即用即丟」不只是不寫檔——一個為了除錯而加的
`_last_image = image_bytes`，或一個「最近 N 次辨識」的 list，都會讓玩家的照片
在進程記憶體裡活到重啟為止。所以這個模組**刻意沒有任何模組層級的可變狀態**，
連統計用的計數器都沒有。

`recognize_landmark()` 的回傳型別是 `bool` 而不是某個結果物件，也是同一個理由：
一個 dataclass 很容易在某次「順手多回傳一點資訊」時把影像夾帶出去。

## 這裡不使用 B1 的 GeminiClient

`GeminiClient.generate(prompt: str)` 只收文字。多模態要送 `Part` 物件，介面對
不上，硬套只會逼 B1 為了這裡改簽章。所以這裡有自己的 client 抽象——兩者共用的
只有「失敗就回退、不拋例外」這個原則。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

from app.core.config import settings

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="landmark")

# 判定用的提示。要求只回一個詞，理由同 B4 的分類器：這一層要的是可判定的結果，
# 不是可讀的說明。回應愈短，被截斷或漂移的空間愈小，成本也愈低。
_RECOGNISE_PROMPT = """這張照片拍的是不是「{landmark_name}」這個地標？

只回答一個詞：
- yes：是這個地標
- no：不是，或無法確定

答案："""


class LandmarkRecognizer(ABC):
    """
    抽象介面。呼叫端只依賴這個，所以測試注入 fake 就能跑，**不需要 GCP 憑證**。
    """

    @abstractmethod
    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        """判斷影像是否為該地標。失敗時回 `False`，不拋例外。"""


class VertexAILandmarkRecognizer(LandmarkRecognizer):
    """
    真實實作。SDK 在需要時才 import，理由同 `gemini.py`。

    憑證走 ADC，沒有任何金鑰參數（ADR-0003）。
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        client_factory=None,
    ):
        self._model_name = model_name or settings.gemini_model
        self._timeout_seconds = timeout_seconds or settings.gemini_timeout_seconds
        self._client_factory = client_factory or self._create_client
        self._client = None

        # ⚠️ 這裡**只記失敗原因，絕不記影像**。observability 的誘惑是「把出錯的
        # 那張照片留下來看看」——那正是 §7.7 禁止的事。
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

        if not image_bytes:
            # 空影像不值得花一次呼叫，也不是錯誤——就是辨識不出來。
            return False

        try:
            from google import genai

            client = self._ensure_client()

            future = _EXECUTOR.submit(
                client.models.generate_content,
                model=self._model_name,
                contents=[
                    genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    _RECOGNISE_PROMPT.format(landmark_name=spirit_id),
                ],
            )

            done, _ = futures_wait([future], timeout=self._timeout_seconds)
            if not done:
                return self._fail(f"超過 {self._timeout_seconds} 秒未回應")

            response = future.result()
            text = (getattr(response, "text", None) or "").strip().lower()

            if not text:
                return self._fail("模型回傳空字串")

            # 寬鬆比對，但**往保守的方向倒**：只有明確的 yes 才算辨識成功。
            # 認不出來就是拿不到徽章，代價很小；誤判成功則是給了不該給的獎勵。
            return text.startswith("yes") or text == "是"

        except Exception as exc:  # noqa: BLE001
            # 攔截所有例外。辨識失敗不該讓任務完成流程中斷（CONTEXT.md），
            # 而「哪些例外算預期」的清單一定會漏。
            return self._fail(f"{type(exc).__name__}: {exc}")

    def _fail(self, reason: str) -> bool:
        self.last_failure_reason = reason
        logger.warning("地標辨識失敗，本次不給徽章：%s", reason)
        return False


class FakeLandmarkRecognizer(LandmarkRecognizer):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同 `FakeGeminiClient`。

    ⚠️ **刻意不記錄收到的 `image_bytes`**，只記 `spirit_id` 與呼叫次數。
    如果連 fake 都留著影像，那份「即用即丟」的紀律就只存在於正式實作裡——
    而測試替身常常是後來被複製去別處的那一個。
    """

    def __init__(self, result: bool = True, raises: Exception | None = None):
        self.result = result
        self.raises = raises
        self.spirit_ids: list[str] = []

    def recognize(self, image_bytes: bytes, spirit_id: str) -> bool:
        self.spirit_ids.append(spirit_id)
        if self.raises is not None:
            # fake 也遵守「不拋例外」的契約——這裡是模擬底層失敗，
            # 真實實作會把它吞掉。
            return False
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.spirit_ids)


def recognize_landmark(
    recognizer: LandmarkRecognizer, image_bytes: bytes, spirit_id: str
) -> bool:
    """
    模組的公開入口。回傳純 `bool`，**不夾帶影像**。

    收 `recognizer` 而不是自己建一個，是為了讓呼叫端（#44 的 landmark-photo
    端點）能注入 fake。
    """
    return recognizer.recognize(image_bytes, spirit_id)
