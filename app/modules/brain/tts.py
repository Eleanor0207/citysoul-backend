"""
B10．TTS 串接（#21，SDD v2.1 §10.1，已依 v2.1 §6.1 縮減）。

Google Cloud TTS 不提供 viseme／phoneme 時間軸，對嘴改由客戶端 uLipSync
即時 MFCC 分析處理（v2.1 §8，屬 F5，在 citysoul-client repo）。`TTSResult`
因此只含 `audio_url`——移除 `viseme_timeline` 按 §11.2.1「只加不減」屬
破壞性變更，必須趕在 Phase 1 契約凍結前落地，見 #30。

跟 B1（`gemini.py`）同一種取捨：呼叫端只依賴抽象介面，測試注入 fake，
不需要 GCP 憑證。

⚠️ **真實實作（`GoogleCloudTTSClient`）刻意還沒寫。** Google Cloud TTS 的
`synthesize_speech` 回傳的是原始音檔位元組，不是 URL——要變成 `audio_url`，
需要先把位元組放到某個客戶端能打開的位置（GCS bucket？簽章 URL？效期多久？
還是走我們自己的 API 伺服＿），而這個 repo 目前沒有任何物件儲存設定
（沒有 bucket 名稱、沒有 `google-cloud-storage` 依賴）。bucket 要先在 GCP
Console 開好、儲存策略要先拍板，這是超出這張票、需要人做決定的基礎設施
問題（已與人確認：先只做抽象介面＋fake，真實實作留給決定儲存策略之後）。
"""
from abc import ABC, abstractmethod

from pydantic import BaseModel


class TTSResult(BaseModel):
    """
    v2.1 §10.1 的 `tts` 欄位形狀。**只含 `audio_url`**——不含任何
    viseme／phoneme 欄位，這同時是 #30 的契約檢查點：契約快照會固定住
    這個形狀，之後不小心加回 viseme 相關欄位會被 CI 的破壞性變更閘門攔下來。
    """

    audio_url: str


class TTSClient(ABC):
    """
    合成語音的抽象介面。真實實作串接 Google Cloud TTS；測試注入
    `FakeTTSClient`，完全不需要 GCP 憑證。
    """

    @abstractmethod
    def synthesize(self, text: str) -> TTSResult | None:
        """
        合成失敗（連線錯誤、逾時、API 錯誤）回傳 `None`，不拋例外——呼叫端
        據此只回文字，對話不因為語音服務打嗝而中斷（同 B1 `GeminiClient`
        的取捨；也跟客戶端 F5 的降級行為銜接：音檔載入失敗時角色維持
        靜止口型，對話文字照常顯示）。
        """


class FakeTTSClient(TTSClient):
    """
    測試用。`response` 是 `None` 時模擬合成失敗（不管失敗原因是連線錯誤還是
    逾時——`synthesize` 的契約本來就不區分，呼叫端也不需要區分）；否則回傳
    `TTSResult(audio_url=response)`。

    `calls` 記下每次呼叫的文字，方便測試斷言真的有把使用者要合成的內容傳進去。
    """

    def __init__(self, response: str | None = "https://example.test/audio/fake.mp3"):
        self.response = response
        self.calls: list[str] = []

    def synthesize(self, text: str) -> TTSResult | None:
        self.calls.append(text)
        if self.response is None:
            return None
        return TTSResult(audio_url=self.response)
