"""
B10．Google Cloud TTS 串接與失敗回退（v2.1 §6.1 / §10.1）。

## 這個模組的核心性質

1. **`synthesize()` 在合成失敗、網路逾時或缺少憑證時回傳 `None`，不拋例外。**
   對應 SDD §8.5 與 F5（客戶端）：TTS 失敗時對話以純文字呈現，召喚流程不得中斷。
2. **`TTSResult` 只含 `audio_url`，不含 `viseme_timeline`。**
   Google Cloud TTS 不提供 viseme 時間軸，對嘴由 Unity 客戶端 `uLipSync` 處理。
3. **語音語言固定台灣繁體中文（`zh-TW`）。**
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
import google.auth
import google.auth.transport.requests
from pydantic import BaseModel, Field
import requests

logger = logging.getLogger(__name__)

TTS_API_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_LANGUAGE_CODE = "zh-TW"
DEFAULT_VOICE_NAME = "zh-TW-Neural2-A"


class TTSResult(BaseModel):
    """
    TTS 合成結果（v2.1 §10.1）。
    只含 audio_url，不含 viseme / phoneme 欄位。
    """

    audio_url: str = Field(description="可播放的語音音檔 URL")


class TTSClient(ABC):
    """
    語音合成的抽象介面。
    測試與離線開發注入 `FakeTTSClient`，無需 GCP 憑證。
    """

    @abstractmethod
    def synthesize(self, text: str) -> TTSResult | None:
        """將文字合成語音。失敗時回傳 None 進行純文字降級，不拋例外。"""


class GoogleCloudTTSClient(TTSClient):
    """
    Google Cloud Text-to-Speech 真實實作。
    使用 Application Default Credentials (ADC) 存取 REST API。
    """

    def __init__(
        self,
        language_code: str = DEFAULT_LANGUAGE_CODE,
        voice_name: str = DEFAULT_VOICE_NAME,
        timeout_seconds: float = 5.0,
    ):
        self.language_code = language_code
        self.voice_name = voice_name
        self.timeout_seconds = timeout_seconds

    def synthesize(self, text: str) -> TTSResult | None:
        if not text or not text.strip():
            return None

        try:
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_request = google.auth.transport.requests.Request()
            credentials.refresh(auth_request)
            token = credentials.token
        except Exception as err:
            logger.warning("Google Cloud TTS Credentials 不可用或取得失敗，進行純文字降級: %s", err)
            return None

        payload = {
            "input": {"text": text},
            "voice": {
                "languageCode": self.language_code,
                "name": self.voice_name,
            },
            "audioConfig": {
                "audioEncoding": "MP3",
            },
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                TTS_API_URL,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            if resp.status_code != 200:
                logger.warning("TTS API 呼叫失敗 [%d]: %s", resp.status_code, resp.text)
                return None

            data = resp.json()
            # GCP REST 回傳 audioContent (base64)，此處在展示/實作中可對接 GCP Storage
            # 或直接產生可用 URL。為了端到端相容，示範為產出音效服務連結。
            # 若包含 base64 audioContent 或音檔儲存 URL:
            audio_content = data.get("audioContent")
            if not audio_content:
                logger.warning("TTS API 回傳缺乏 audioContent")
                return None

            # 成功取得音訊 base64 後，封裝為 Data URL 或存儲 URL
            audio_url = f"data:audio/mp3;base64,{audio_content}"
            return TTSResult(audio_url=audio_url)
        except Exception as err:
            logger.warning("TTS 合成過程發生異常，進行純文字降級: %s", err)
            return None


class FakeTTSClient(TTSClient):
    """
    測試用 Fake 實作。
    """

    def __init__(
        self,
        preset_url: str | None = "https://example.test/audio/fake_reply.mp3",
        fail: bool = False,
    ):
        self.preset_url = preset_url
        self.fail = fail

    def synthesize(self, text: str) -> TTSResult | None:
        if self.fail or not self.preset_url:
            return None
        return TTSResult(audio_url=self.preset_url)
