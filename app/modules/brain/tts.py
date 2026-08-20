"""
B10．Google Cloud TTS 串接（issue #21）。

## ⚠️ 本票已依 SDD v2.1 §6.1 縮減：沒有 viseme 時間軸

原設計要求後端產出 viseme 時間軸。**那個設計不可實作**——Google Cloud TTS
不提供 viseme／phoneme 時間軸，不是我們沒做，是那個 API 不存在。

對嘴改由客戶端 uLipSync 即時 MFCC 分析處理（v2.1 §8，屬 F5，在
`citysoul-client`）。所以 `TTSResult` **只有 `audio_url`**，而且不該有第二個
欄位——多一個永遠是 null 的 `viseme_timeline` 只會讓客戶端寫出無用的處理分支，
並讓「對嘴是誰的責任」重新變得模糊。

移除它按 §11.2.1 的「只加不減」屬破壞性變更，必須趕在 Phase 1 契約凍結之前
落地。目前沒有任何客戶端在用，所以安全。

## 這個模組永遠不讓對話掛掉

`synthesize()` **失敗時回傳 None，不拋例外**，跟 B1 的 `generate()` 同一個原則：
CONTEXT.md「無合格輸入或生成失敗時使用人工預寫台詞」。語音是加分項，文字才是
對話本身——TTS 掛掉時玩家該看到文字，而不是錯誤畫面。

這也跟客戶端的降級行為銜接：F5 在音檔載入失敗時讓角色維持靜止口型、對話文字
照常顯示。兩端對「沒有語音」的處置是一致的。

## 兩個接縫，不是一個

合成（`TTSClient`）與存放（`AudioStorage`）分開。TTS 回傳的是音檔位元組，而
客戶端要的是 URL——中間一定有存放這件事。把它藏在 TTS 實作裡的話，測試就非得
連上 GCS 才跑得動，而那跟「驗證我們有沒有正確呼叫 TTS」是兩件無關的事。
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as futures_wait

from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# 逾時用的執行緒池，理由同 gemini.py：SDK 沒有 per-call timeout。
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tts")


# ⚠️ `TTSResult` 的 docstring 刻意寫得很短。
#
# Pydantic 會把 docstring 放進 `model_json_schema()` 的 description，而那份
# schema 會進 contracts/openapi.json——寫在這裡的每一個字都會送到客戶端。
# 解釋「為什麼沒有時間軸欄位」屬於本模組的註解，不屬於對外契約。
class TTSResult(BaseModel):
    """語音合成結果（SDD v2.1 §10.1）。欄位恰為 audio_url。"""

    audio_url: str


class AudioStorage(ABC):
    """把合成好的音檔放到客戶端拿得到的地方，回傳 URL。"""

    @abstractmethod
    def store(self, audio: bytes, *, content_type: str = "audio/mpeg") -> str | None:
        """存放並回傳 URL。失敗時回傳 None，不拋例外。"""


class TTSClient(ABC):
    """
    抽象介面。呼叫端只依賴這個，所以測試注入 fake 就能跑，**不需要 GCP 憑證**。
    """

    @abstractmethod
    def synthesize(self, text: str) -> TTSResult | None:
        """合成語音。失敗時回傳 `None`，不拋例外。"""


class GoogleCloudTTSClient(TTSClient):
    """
    真實實作。SDK 在需要時才 import，不在模組頂端——沒裝
    `google-cloud-texttospeech` 的環境仍然可以 import 這個模組並使用 fake。

    憑證走 ADC，沒有任何金鑰參數（ADR-0003）。
    """

    def __init__(
        self,
        storage: AudioStorage,
        *,
        language_code: str | None = None,
        voice_name: str | None = None,
        timeout_seconds: float | None = None,
        client_factory=None,
    ):
        self._storage = storage
        self._language_code = language_code or settings.tts_language_code
        self._voice_name = voice_name if voice_name is not None else settings.tts_voice_name
        self._timeout_seconds = timeout_seconds or settings.tts_timeout_seconds
        self._client_factory = client_factory or self._create_client
        self._client = None

        self.last_failure_reason: str | None = None

    def _create_client(self):
        from google.cloud import texttospeech

        return texttospeech.TextToSpeechClient()

    def _ensure_client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def synthesize(self, text: str) -> TTSResult | None:
        self.last_failure_reason = None

        if not text or not text.strip():
            # 空文字沒有東西可以唸。這不是失敗，只是沒有語音。
            return None

        try:
            from google.cloud import texttospeech

            client = self._ensure_client()

            voice_kwargs = {"language_code": self._language_code}
            if self._voice_name:
                voice_kwargs["name"] = self._voice_name

            future = _EXECUTOR.submit(
                client.synthesize_speech,
                input=texttospeech.SynthesisInput(text=text),
                voice=texttospeech.VoiceSelectionParams(**voice_kwargs),
                audio_config=texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.MP3
                ),
            )

            # 與 gemini.py 同樣不用 future.result(timeout=)：那會讓 SDK 自己拋的
            # TimeoutError 跟「我們主動放棄」混在一起，last_failure_reason 會記錯。
            done, _ = futures_wait([future], timeout=self._timeout_seconds)
            if not done:
                return self._fall_back(f"超過 {self._timeout_seconds} 秒未回應")

            response = future.result()
            audio = getattr(response, "audio_content", None)

            if not audio:
                return self._fall_back("TTS 回傳空音檔")

            url = self._storage.store(audio)
            if not url:
                return self._fall_back("音檔存放失敗")

            return TTSResult(audio_url=url)

        except Exception as exc:  # noqa: BLE001
            # 刻意攔截所有例外，理由同 gemini.py：任何未預期的例外都不是讓對話
            # 中斷的理由，而「哪些例外算預期」的清單一定會漏。
            return self._fall_back(f"{type(exc).__name__}: {exc}")

    def _fall_back(self, reason: str) -> None:
        self.last_failure_reason = reason
        logger.warning("TTS 合成失敗，本次對話只回文字：%s", reason)
        return None


class GcsAudioStorage(AudioStorage):
    """
    存到 Cloud Storage，回傳有效期有限的簽章 URL。

    bucket 沒設定時回傳 None——本機開發不該為了讓程式跑起來而被迫先開一個
    bucket，而「沒有語音」本來就是這條流程支援的狀態。
    """

    def __init__(
        self,
        *,
        bucket_name: str | None = None,
        url_ttl_seconds: int | None = None,
        client_factory=None,
        credentials_factory=None,
    ):
        self._bucket_name = bucket_name if bucket_name is not None else settings.tts_audio_bucket
        self._url_ttl_seconds = url_ttl_seconds or settings.tts_audio_url_ttl_seconds
        self._client_factory = client_factory or self._create_client
        self._credentials_factory = credentials_factory or self._default_credentials
        self._client = None

    def _create_client(self):
        from google.cloud import storage

        return storage.Client()

    @staticmethod
    def _default_credentials():
        import google.auth

        credentials, _ = google.auth.default()
        return credentials

    def _signing_kwargs(self) -> dict:
        """
        簽章網址要怎麼簽，取決於當下這組憑證有沒有私鑰。

        Cloud Run 上的服務帳號憑證**只有一個 access token，沒有私鑰**，而
        `generate_signed_url()` 預設是拿私鑰在本地簽——所以它會以
        「you need a private key to sign credentials」失敗。那個例外會被
        `store()` 吞掉、降級成沒有語音，**測試與 log 都不會有紅字**，只有戴上
        耳機的人才會發現。這個方法存在就是為了不讓那件事再發生一次。

        帶了 `service_account_email` 與 `access_token` 之後，SDK 改走 IAM 的
        `signBlob` API 代簽，需要該服務帳號對自己有
        `roles/iam.serviceAccountTokenCreator`。

        有私鑰的憑證（金鑰檔）走原本的本地簽章即可，不必多繞一趟 IAM。
        """
        credentials = self._credentials_factory()

        from google.auth import credentials as auth_credentials

        if isinstance(credentials, auth_credentials.Signing):
            return {}

        from google.auth.transport import requests as auth_requests

        # ⚠️ 順序有意義：**先 refresh，再讀 email**。
        #
        # Cloud Run 的 compute 憑證剛建好時，`service_account_email` 是字面值
        # "default"——那是 metadata server 的別名，不是信箱。真正的位址要等
        # `refresh()` 內部呼叫 `_retrieve_info()` 才會填進來。倒過來寫的話會把
        # "default" 送進 IAM signBytes，換回
        # 「Invalid form of account ID default」400，而那個例外一樣會被
        # store() 吞掉、降級成沒有語音。
        if not credentials.valid:
            credentials.refresh(auth_requests.Request())

        email = getattr(credentials, "service_account_email", None)

        if email == "default":
            # 憑證已經是 valid（例如別處先 refresh 過）而沒走上面那條路時，
            # email 可能還停在別名上。再 refresh 一次把它換成真的信箱。
            credentials.refresh(auth_requests.Request())
            email = getattr(credentials, "service_account_email", None)

        if not email or email == "default":
            # 本機的使用者憑證兩條路都走不了。回空字典讓它照原本的方式失敗，
            # 由 store() 統一降級——在這裡丟例外只會把「沒有語音」變成當機。
            return {}

        return {"service_account_email": email, "access_token": credentials.token}

    def store(self, audio: bytes, *, content_type: str = "audio/mpeg") -> str | None:
        if not self._bucket_name:
            logger.info("未設定 tts_audio_bucket，略過音檔存放（對話仍以純文字進行）")
            return None

        try:
            from datetime import timedelta

            if self._client is None:
                self._client = self._client_factory()

            bucket = self._client.bucket(self._bucket_name)
            blob = bucket.blob(f"tts/{uuid.uuid4().hex}.mp3")
            blob.upload_from_string(audio, content_type=content_type)

            return blob.generate_signed_url(
                expiration=timedelta(seconds=self._url_ttl_seconds),
                **self._signing_kwargs(),
            )

        except Exception as exc:  # noqa: BLE001
            logger.warning("音檔存放失敗：%s: %s", type(exc).__name__, exc)
            return None


class InMemoryAudioStorage(AudioStorage):
    """測試與本機開發用。記下存過的音檔，回傳一個假的 URL。"""

    def __init__(self, base_url: str = "https://example.test/audio"):
        self.base_url = base_url
        self.stored: list[bytes] = []
        self.fail = False

    def store(self, audio: bytes, *, content_type: str = "audio/mpeg") -> str | None:
        if self.fail:
            return None
        self.stored.append(audio)
        return f"{self.base_url}/{len(self.stored)}.mp3"


class FakeTTSClient(TTSClient):
    """
    測試用。放在正式程式碼而不是 tests/ 底下，理由同 `FakeGeminiClient`。

    `result=None` 模擬合成失敗——呼叫端據此只回文字。
    """

    def __init__(self, result: TTSResult | None = TTSResult(audio_url="https://example.test/audio/abc.mp3")):
        self.result = result
        self.texts: list[str] = []

    def synthesize(self, text: str) -> TTSResult | None:
        self.texts.append(text)
        return self.result

    @property
    def call_count(self) -> int:
        return len(self.texts)
