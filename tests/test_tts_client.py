"""
B10．Google Cloud TTS 串接單元測試。
"""
import pytest

from app.modules.brain.tts import (
    DEFAULT_LANGUAGE_CODE,
    FakeTTSClient,
    GoogleCloudTTSClient,
    TTSResult,
)


def test_tts_result_schema():
    """驗證 TTSResult schema 恰好只有 audio_url 欄位，不含 viseme。"""
    schema = TTSResult.model_json_schema()
    properties = set(schema["properties"].keys())
    assert properties == {"audio_url"}, f"TTSResult 欄位應恰為 {{audio_url}}，實際為 {properties}"


def test_fake_tts_client_success():
    """驗證 FakeTTSClient 成功回傳可播放音檔 URL。"""
    client = FakeTTSClient(preset_url="https://example.test/audio/spirit_voice.mp3")
    result = client.synthesize("今夜的香火比平常更盛一些。")
    assert result is not None
    assert result.audio_url == "https://example.test/audio/spirit_voice.mp3"


def test_fake_tts_client_failure_graceful_fallback():
    """驗證 FakeTTSClient 失敗時回傳 None 進行純文字降級，不拋出例外。"""
    client = FakeTTSClient(fail=True)
    result = client.synthesize("任何測試文字")
    assert result is None


def test_google_cloud_tts_client_language_code():
    """驗證預設語言代碼為台灣繁體中文 zh-TW。"""
    client = GoogleCloudTTSClient()
    assert client.language_code == DEFAULT_LANGUAGE_CODE == "zh-TW"


def test_google_cloud_tts_real_call_skipped_without_credentials():
    """真實 GCP TTS 呼叫驗證：無憑證時自動 skip。"""
    client = GoogleCloudTTSClient(timeout_seconds=2.0)
    result = client.synthesize("測試語音")
    if result is None:
        pytest.skip("無可用 GCP 憑證或連線不可用，自動 skip 真實呼叫測試")
    assert result.audio_url.startswith("data:audio/mp3;base64,")
