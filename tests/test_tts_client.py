"""
Ticket #21．TTS 串接（B10，v2.1 已移除 viseme 時間軸）。

驗收標準對照見 GitHub issue #21。跟 `tests/test_gemini_client.py` 同一個
結構：`GoogleCloudTTSClient` 全部用假的 `tts_client_factory`／
`storage_client_factory` 注入，唯一會碰到真實服務的是最後那個 live 測試，
沒有憑證時自動 skip。
"""
import os

import pytest

from app.modules.brain.tts import FakeTTSClient, GoogleCloudTTSClient, TTSClient, TTSResult


# ── TTSResult 的形狀（AC2）───────────────────────────────────────────────

def test_tts_result_schema_is_only_audio_url():
    schema = TTSResult.model_json_schema()
    assert set(schema["properties"]) == {"audio_url"}
    assert schema["required"] == ["audio_url"]


def test_no_viseme_implementation_code_in_the_repo():
    """
    grep 整個 app/ 是否還有 viseme 字樣——除了說明「為何移除」的註解（本檔案、
    `tts.py` 自己），不該有任何 viseme 實作程式碼。這裡只驗證程式碼（非
    docstring 散文說明本身也含這個詞是預期中的，重點是沒有 viseme **欄位**
    或 **邏輯**），所以只檢查 `TTSResult` 與 `DialogueResponse` 的 schema。
    """
    from app.modules.body.schemas import DialogueResponse

    assert "viseme" not in TTSResult.model_json_schema().get("properties", {})

    # 只查欄位名（含巢狀的 tts 子物件），不查整個 schema 字串——後者連
    # description 都會掃進去，而 description 裡解釋「為何拿掉 viseme」本來
    # 就會提到這個詞，那是文件不是實作。
    def _field_names(schema: dict) -> set[str]:
        names = set(schema.get("properties", {}))
        for definition in schema.get("$defs", {}).values():
            names |= set(definition.get("properties", {}))
        return names

    assert "viseme" not in {name.lower() for name in _field_names(DialogueResponse.model_json_schema())}


# ── FakeTTSClient 滿足抽象介面（AC1）─────────────────────────────────────

def test_fake_client_satisfies_the_abstract_interface():
    assert isinstance(FakeTTSClient(), TTSClient)


# ── 合成成功（AC3）───────────────────────────────────────────────────────

def test_fake_synthesize_returns_configured_result():
    fake = FakeTTSClient(TTSResult(audio_url="https://example.test/audio/abc.mp3"))

    result = fake.synthesize("今夜的香火比平常更盛一些。")

    assert result == TTSResult(audio_url="https://example.test/audio/abc.mp3")


def test_real_client_returns_result_on_success():
    class _FakeResponse:
        audio_content = b"fake-mp3-bytes"

    class _FakeTTSSDKClient:
        def synthesize_speech(self, **kwargs):
            return _FakeResponse()

    class _FakeBlob:
        def __init__(self):
            self.uploaded = None

        def exists(self):
            return False

        def upload_from_string(self, data, content_type):
            self.uploaded = (data, content_type)

        @property
        def public_url(self):
            return "https://storage.googleapis.com/test-bucket/tts/abc.mp3"

    class _FakeBucket:
        def blob(self, name):
            return _FakeBlob()

    class _FakeStorageClient:
        def bucket(self, name):
            return _FakeBucket()

    client = GoogleCloudTTSClient(
        tts_client_factory=lambda: _FakeTTSSDKClient(),
        storage_client_factory=lambda: _FakeStorageClient(),
    )

    result = client.synthesize("這座廟最早是什麼時候蓋的？")

    assert result == TTSResult(audio_url="https://storage.googleapis.com/test-bucket/tts/abc.mp3")


def test_real_client_skips_upload_when_object_already_exists():
    """同一句話重複合成時，命中既有物件，不重新上傳。"""
    upload_calls = []

    class _FakeResponse:
        audio_content = b"fake-mp3-bytes"

    class _FakeTTSSDKClient:
        def synthesize_speech(self, **kwargs):
            return _FakeResponse()

    class _FakeBlob:
        def exists(self):
            return True

        def upload_from_string(self, data, content_type):
            upload_calls.append(data)

        @property
        def public_url(self):
            return "https://storage.googleapis.com/test-bucket/tts/cached.mp3"

    class _FakeBucket:
        def blob(self, name):
            return _FakeBlob()

    class _FakeStorageClient:
        def bucket(self, name):
            return _FakeBucket()

    client = GoogleCloudTTSClient(
        tts_client_factory=lambda: _FakeTTSSDKClient(),
        storage_client_factory=lambda: _FakeStorageClient(),
    )

    result = client.synthesize("你好")

    assert result.audio_url == "https://storage.googleapis.com/test-bucket/tts/cached.mp3"
    assert upload_calls == []


# ── 合成失敗時降級為純文字，不拋例外（AC4）────────────────────────────────

def test_fake_client_can_simulate_failure():
    fake = FakeTTSClient(fail=True)

    assert fake.synthesize("你好") is None


@pytest.mark.parametrize(
    "broken_factory",
    [
        lambda: (_ for _ in ()).throw(ConnectionError("連線錯誤")),
        lambda: (_ for _ in ()).throw(TimeoutError("逾時")),
    ],
)
def test_real_client_returns_none_on_failure_without_raising(broken_factory):
    client = GoogleCloudTTSClient(
        tts_client_factory=broken_factory,
        storage_client_factory=lambda: None,
    )

    result = client.synthesize("你好")

    assert result is None
    assert client.last_failure_reason is not None


def test_upload_failure_also_degrades_to_none():
    """合成本身成功，但上傳到 GCS 失敗，同樣要降級,不拋例外。"""

    class _FakeResponse:
        audio_content = b"fake-mp3-bytes"

    class _FakeTTSSDKClient:
        def synthesize_speech(self, **kwargs):
            return _FakeResponse()

    class _BrokenStorageClient:
        def bucket(self, name):
            raise ConnectionError("GCS 連線錯誤")

    client = GoogleCloudTTSClient(
        tts_client_factory=lambda: _FakeTTSSDKClient(),
        storage_client_factory=lambda: _BrokenStorageClient(),
    )

    assert client.synthesize("你好") is None


# ── 語言固定台灣繁體中文，設定在呼叫參數層級（AC5）────────────────────────

def test_language_and_voice_are_passed_as_explicit_call_parameters():
    captured = {}

    class _FakeResponse:
        audio_content = b"x"

    class _FakeTTSSDKClient:
        def synthesize_speech(self, *, input, voice, audio_config, timeout):
            captured["language_code"] = voice.language_code
            captured["voice_name"] = voice.name
            return _FakeResponse()

    class _FakeBlob:
        def exists(self):
            return True

        @property
        def public_url(self):
            return "https://example.test/x.mp3"

    class _FakeStorageClient:
        def bucket(self, name):
            return type("B", (), {"blob": lambda self, n: _FakeBlob()})()

    client = GoogleCloudTTSClient(
        tts_client_factory=lambda: _FakeTTSSDKClient(),
        storage_client_factory=lambda: _FakeStorageClient(),
    )
    client.synthesize("你好")

    assert captured["language_code"] == "cmn-TW"
    assert captured["voice_name"].startswith("cmn-TW")


def test_language_is_configurable_not_hardcoded():
    """跟預設值不同的語言代碼也要能生效，證明不是寫死在呼叫邏輯裡。"""
    captured = {}

    class _FakeResponse:
        audio_content = b"x"

    class _FakeTTSSDKClient:
        def synthesize_speech(self, *, input, voice, audio_config, timeout):
            captured["language_code"] = voice.language_code
            return _FakeResponse()

    class _FakeBlob:
        def exists(self):
            return True

        @property
        def public_url(self):
            return "https://example.test/x.mp3"

    class _FakeStorageClient:
        def bucket(self, name):
            return type("B", (), {"blob": lambda self, n: _FakeBlob()})()

    client = GoogleCloudTTSClient(
        language_code="en-US",
        voice_name="en-US-Wavenet-A",
        tts_client_factory=lambda: _FakeTTSSDKClient(),
        storage_client_factory=lambda: _FakeStorageClient(),
    )
    client.synthesize("hello")

    assert captured["language_code"] == "en-US"


# ── 真實呼叫：沒有憑證時自動 skip（AC7）───────────────────────────────────

def _has_adc() -> bool:
    try:
        import google.auth

        google.auth.default()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    os.getenv("CITYSOUL_RUN_LIVE_TTS") != "1",
    reason="真實呼叫要花錢也要網路。設 CITYSOUL_RUN_LIVE_TTS=1 才跑。",
)
def test_live_google_cloud_tts_call():
    """
    手動執行的真實驗證：

        CITYSOUL_RUN_LIVE_TTS=1 uv run python -m pytest tests/test_tts_client.py -k live

    ⚠️ 失敗時先確認是不是環境問題再看程式碼（同 test_gemini_client.py）：
    - `could not find default credentials` → 跑 `gcloud auth application-default login`
    - 憑證驗證錯誤 → 本機 Avast 的 HTTPS 掃描攔截 TLS
    - 上傳失敗 → 確認 `gcs_tts_bucket` 這個桶存在、且執行者對它有寫入權限
    """
    if not _has_adc():
        pytest.skip("找不到 ADC，請先跑 gcloud auth application-default login")

    result = GoogleCloudTTSClient().synthesize("你好，這是一段測試語音。")

    assert result is not None, "拿到 None 代表真實呼叫失敗了，檢查 last_failure_reason"
    assert result.audio_url
