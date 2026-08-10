"""
B10．Google Cloud TTS 串接（issue #21）。

⚠️ **v2.1 §6.1 已縮減本工作包**：原設計要求這裡順便產出 viseme 時間軸，供
客戶端對嘴使用。那個設計不可實作——Google Cloud TTS 不提供 viseme／phoneme
時間軸。對嘴改由客戶端 uLipSync 對音檔做即時 MFCC 分析（v2.1 §8，屬 F5，在
citysoul-client repo）。這裡因此**只做**「文字進、音檔 URL 出」，`TTSResult`
刻意只有 `audio_url` 一個欄位。

## 為什麼合成失敗回傳 `None`，不像 `GeminiClient` 回傳固定的 fallback 字串

`GeminiClient.generate()` 失敗時有「人工預寫台詞」這個安全的替代內容可以
回退（CONTEXT.md：「無合格輸入或生成失敗時使用人工預寫台詞」）。TTS 沒有
等價的東西——不存在一句「預錄語音」可以頂替任意一段 `reply_text` 的語音。
唯一站得住腳的降級是「這一輪沒有語音，純文字照常顯示」，而「有沒有語音」
是呼叫端（dialogue 端點）該決定要不要把 `tts` 欄位放進回應的事，不該由這支
模組偽造一個指向不存在音檔的假 URL 來假裝成功。

## 語音合成之後去哪裡

Google Cloud TTS 的 API 回傳的是音檔位元組，不是 URL——「回傳可播放的音檔
URL」這件事需要有個地方存放它。SDD 把 TTS 的媒體層定義為「Google Cloud TTS
+ Cloud Storage」，所以真實實作內含一次上傳到 GCS 的動作，不是只包一層
TTS SDK。
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable

from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)


class TTSResult(BaseModel):
    """
    v2.1 §10.1：`DialogueResponse.tts` 的形狀，**只有** `audio_url`。用
    pydantic model（不是 dataclass）是因為它會直接嵌進 API response 序列化
    出去，跟其他 response model 用同一種宣告方式。

    不含任何 viseme／phoneme 欄位是刻意的契約——見模組開頭說明；#30 的契約
    快照會固定住這個形狀，之後想加欄位要走那邊的流程，不是在這裡隨手加。
    """

    audio_url: str


class TTSClient(ABC):
    """
    語音合成的抽象介面。呼叫端只依賴這個介面，測試可以注入 `FakeTTSClient`
    而完全不需要 GCP 憑證。
    """

    @abstractmethod
    def synthesize(self, text: str) -> TTSResult | None:
        """合成語音。失敗時回傳 `None`，不拋例外——理由見模組開頭說明。"""


class GoogleCloudTTSClient(TTSClient):
    """
    真實實作。憑證走 ADC（ADR-0003），跟 B1 同一套路徑，沒有金鑰檔。

    兩個 SDK client（TTS、Storage）都延遲建立、延遲 import：沒裝對應套件的
    環境仍然可以 import 這個模組並使用 fake，跟 `gemini.py` 的做法一致。
    """

    def __init__(
        self,
        *,
        language_code: str | None = None,
        voice_name: str | None = None,
        bucket_name: str | None = None,
        timeout_seconds: float | None = None,
        tts_client_factory: Callable[[], Any] | None = None,
        storage_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        `tts_client_factory`／`storage_client_factory` 讓測試注入會拋例外的
        假 client，藉此驗證**這個類別的**降級邏輯——用一個自己就回傳結果的
        假 client 測不到「呼叫失敗時怎麼辦」這條路徑（同 `VertexAIGeminiClient`
        的說明）。
        """
        # 語言／語音代碼是設定值，不是從文字內容自動偵測（issue #21 AC）：
        # MVP 語言固定台灣繁體中文，這是一個看得見、可審核的決定。
        self._language_code = language_code or settings.tts_language_code
        self._voice_name = voice_name or settings.tts_voice_name
        self._bucket_name = bucket_name or settings.gcs_tts_bucket
        self._timeout_seconds = timeout_seconds or settings.tts_timeout_seconds
        self._tts_client_factory = tts_client_factory or self._create_tts_client
        self._storage_client_factory = storage_client_factory or self._create_storage_client
        self._tts_client = None
        self._storage_client = None

        self.last_failure_reason: str | None = None

    def _create_tts_client(self):
        from google.cloud import texttospeech

        return texttospeech.TextToSpeechClient()

    def _create_storage_client(self):
        from google.cloud import storage

        return storage.Client(project=settings.gcp_project_id)

    def _ensure_tts_client(self):
        if self._tts_client is None:
            self._tts_client = self._tts_client_factory()
        return self._tts_client

    def _ensure_storage_client(self):
        if self._storage_client is None:
            self._storage_client = self._storage_client_factory()
        return self._storage_client

    def synthesize(self, text: str) -> TTSResult | None:
        self.last_failure_reason = None

        try:
            from google.cloud import texttospeech

            client = self._ensure_tts_client()
            response = client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=text),
                voice=texttospeech.VoiceSelectionParams(
                    language_code=self._language_code, name=self._voice_name
                ),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3
                ),
                timeout=self._timeout_seconds,
            )
            audio_url = self._upload(text, response.audio_content)
            return TTSResult(audio_url=audio_url)
        except Exception as exc:  # noqa: BLE001
            # 跟 GeminiClient 同樣的理由：任何未預期的例外都不該讓對話流程
            # 中斷，這裡不列「哪些例外算預期」的清單，那種清單一定會漏。
            self.last_failure_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("TTS 合成失敗，降級為純文字：%s", self.last_failure_reason)
            return None

    def _upload(self, text: str, audio_bytes: bytes) -> str:
        """
        物件名取文字內容的 hash，不取隨機值：同一句話（例如某個常見問句的
        Gemini 回覆剛好重複）重複合成時直接命中既有物件，省一次合成與一次
        上傳。這不是正確性要求，只是幾乎不花額外程式碼就拿到的效能。

        桶要設定成可公開讀取（或前面掛 CDN，見 SDD 的「Cloud Storage +
        CDN」）才能讓 `public_url` 直接可播放——那是部署設定，不是這裡的
        程式碼能保證的事。
        """
        object_name = f"tts/{hashlib.sha256(text.encode('utf-8')).hexdigest()}.mp3"
        bucket = self._ensure_storage_client().bucket(self._bucket_name)
        blob = bucket.blob(object_name)
        if not blob.exists():
            blob.upload_from_string(audio_bytes, content_type="audio/mpeg")
        return blob.public_url


class FakeTTSClient(TTSClient):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同 `gemini.FakeGeminiClient`
    ——dialogue 端點（#42／#45）的測試也會用到它。

    預設合成成功；建構時傳 `fail=True` 模擬「合成失敗，降級為純文字」那條路徑
    （對應連線錯誤／逾時，issue #21 AC 沒有要求區分失敗原因，呼叫端只在乎
    成功或 `None` 這兩種結果）。
    """

    def __init__(self, result: TTSResult | None = None, *, fail: bool = False) -> None:
        self.result = result or TTSResult(audio_url="https://example.test/audio/fake.mp3")
        self.fail = fail
        self.texts: list[str] = []

    def synthesize(self, text: str) -> TTSResult | None:
        self.texts.append(text)
        if self.fail:
            return None
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.texts)
