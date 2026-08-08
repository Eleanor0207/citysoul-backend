"""
#21．B10 Google Cloud TTS 串接。

這個檔案**完全不需要 GCP 憑證**：真實 SDK 由 `client_factory` 注入 stub。
唯一會碰到真實服務的是最後那個測試，沒有憑證時自動 skip（同 #8 的處理）。
"""
import os
import time

import pytest

from app.core.config import settings
from app.modules.brain.tts import (
    FakeTTSClient,
    GcsAudioStorage,
    GoogleCloudTTSClient,
    InMemoryAudioStorage,
    TTSClient,
    TTSResult,
)


class _StubResponse:
    def __init__(self, audio_content: bytes):
        self.audio_content = audio_content


class _StubTTSClient:
    """對應 google-cloud-texttospeech 的 client。記下呼叫參數，或依設定失敗。"""

    def __init__(self, *, audio=b"\x00fake-mp3", raises=None, delay=0.0):
        self._audio = audio
        self._raises = raises
        self._delay = delay
        self.calls = []

    def synthesize_speech(self, *, input, voice, audio_config):  # noqa: A002
        self.calls.append({"input": input, "voice": voice, "audio_config": audio_config})
        if self._delay:
            time.sleep(self._delay)
        if self._raises:
            raise self._raises
        return _StubResponse(self._audio)


def _client(**kwargs) -> tuple[GoogleCloudTTSClient, InMemoryAudioStorage, _StubTTSClient]:
    stub = _StubTTSClient(**kwargs)
    storage = InMemoryAudioStorage()
    client = GoogleCloudTTSClient(storage, client_factory=lambda: stub)
    return client, storage, stub


# ── TTSResult 的形狀（契約）─────────────────────────────────────────────

def test_tts_result_has_exactly_one_field():
    """
    🔒 AC：欄位恰為 `{"audio_url"}`。

    多一個永遠是 null 的 `viseme_timeline` 會讓客戶端寫出無用的處理分支，
    並讓「對嘴是誰的責任」重新變得模糊。
    """
    fields = set(TTSResult.model_json_schema()["properties"])

    assert fields == {"audio_url"}


def test_no_viseme_field_anywhere_in_the_model():
    """
    AC：不含任何 viseme／phoneme 欄位。

    ⚠️ 這不是「暫時還沒做」，是**那個 API 不存在**——Google Cloud TTS 不提供
    viseme／phoneme 時間軸。對嘴由客戶端 uLipSync 即時 MFCC 分析處理
    （v2.1 §8，屬 F5）。這條測試防止有人日後「補回」一個做不出來的欄位。
    """
    schema = str(TTSResult.model_json_schema()).lower()

    assert "viseme" not in schema
    assert "phoneme" not in schema


def test_repo_has_no_viseme_implementation():
    """
    AC：repo 中除了說明「為何移除」的註解外，沒有任何 viseme 實作程式碼。

    用 `ast` 而不是逐行字串比對。第一版是後者，結果被自己的說明註解判成違規——
    註解與 docstring **應該**解釋為什麼沒有這個欄位，那正是我們希望留下的東西。
    `ast` 天生看不到註解，docstring 也能明確排除，剩下的才是真正的程式碼。
    """
    import ast
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = []

    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))

        # 先收集所有 docstring 節點，稍後排除。
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))

        for node in ast.walk(tree):
            identifier = None
            if isinstance(node, ast.Name):
                identifier = node.id
            elif isinstance(node, ast.Attribute):
                identifier = node.attr
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifier = node.name
            elif isinstance(node, ast.arg):
                identifier = node.arg
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    identifier = node.value

            if identifier and "viseme" in identifier.lower():
                offenders.append(f"{path.name}:{getattr(node, 'lineno', '?')}")

    assert not offenders, f"發現 viseme 實作程式碼：{offenders}"


# ── 合成成功 ───────────────────────────────────────────────────────────

def test_successful_synthesis_returns_an_audio_url():
    client, storage, _ = _client()

    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert isinstance(result, TTSResult)
    assert result.audio_url.startswith("https://example.test/audio/")
    assert len(storage.stored) == 1


def test_fake_returns_the_configured_url():
    """AC 明列的 fake 行為。"""
    fake = FakeTTSClient(TTSResult(audio_url="https://example.test/audio/abc.mp3"))

    result = fake.synthesize("今夜的香火比平常更盛一些。")

    assert result == TTSResult(audio_url="https://example.test/audio/abc.mp3")
    assert fake.texts == ["今夜的香火比平常更盛一些。"]


# ── 語言固定 zh-TW ─────────────────────────────────────────────────────

def test_language_is_configured_not_detected_from_text():
    """
    AC：語言固定台灣繁體中文，設定在呼叫參數層級看得到。

    自動偵測會讓一句混了英文地名的台詞被判成英文，然後用英文腔唸出整句中文——
    玩家聽到角色破音，而我們在 log 上看不到任何錯誤。
    """
    client, _, stub = _client()

    client.synthesize("龍山寺 Longshan Temple 就在前面。")

    assert stub.calls[0]["voice"].language_code == "zh-TW"


def test_language_default_comes_from_settings():
    assert settings.tts_language_code == "zh-TW"


# ── 失敗一律降級為純文字 ───────────────────────────────────────────────

def test_connection_error_degrades_to_no_audio():
    """AC (a) 連線錯誤。"""
    client, _, _ = _client(raises=ConnectionError("connection refused"))

    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert result is None
    assert client.last_failure_reason


def test_timeout_degrades_to_no_audio():
    """AC (b) 逾時。"""
    stub = _StubTTSClient(delay=0.3)
    client = GoogleCloudTTSClient(
        InMemoryAudioStorage(), client_factory=lambda: stub, timeout_seconds=0.05
    )

    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert result is None
    assert "未回應" in client.last_failure_reason


def test_empty_audio_degrades_to_no_audio():
    client, _, _ = _client(audio=b"")

    assert client.synthesize("今夜的香火比平常更盛一些。") is None


def test_storage_failure_degrades_to_no_audio():
    """
    合成成功但存不進去，對玩家而言跟合成失敗沒有差別——沒有 URL 就沒有語音。
    """
    stub = _StubTTSClient()
    storage = InMemoryAudioStorage()
    storage.fail = True
    client = GoogleCloudTTSClient(storage, client_factory=lambda: stub)

    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert result is None
    assert "存放失敗" in client.last_failure_reason


def test_blank_text_returns_none_without_calling_the_api():
    client, _, stub = _client()

    assert client.synthesize("   ") is None
    assert stub.calls == []


@pytest.mark.parametrize(
    "failure",
    [ConnectionError("boom"), RuntimeError("unexpected"), ValueError("weird sdk error")],
)
def test_no_exception_ever_escapes(failure):
    """
    🔒 **本模組的核心性質**：`synthesize()` 永遠不拋例外。

    語音是加分項，文字才是對話本身。TTS 掛掉時玩家該看到文字，而不是錯誤畫面。
    這條同時是 AC 指定要做 mutation 驗證的那一條——把失敗處理改成 re-raise，
    這裡必須變紅。
    """
    client, _, _ = _client(raises=failure)

    assert client.synthesize("今夜的香火比平常更盛一些。") is None


# ── GCS 存放：沒設定 bucket 時安靜降級 ─────────────────────────────────

def test_gcs_storage_without_a_bucket_returns_none():
    """
    本機開發不該為了讓程式跑起來而被迫先開一個 bucket，而「沒有語音」本來就是
    這條流程支援的狀態。
    """
    storage = GcsAudioStorage(bucket_name=None)

    assert storage.store(b"\x00audio") is None


def test_gcs_storage_failure_does_not_raise():
    def _explode():
        raise RuntimeError("no credentials")

    storage = GcsAudioStorage(bucket_name="some-bucket", client_factory=_explode)

    assert storage.store(b"\x00audio") is None


# ── 抽象介面 ───────────────────────────────────────────────────────────

def test_both_implementations_satisfy_the_interface():
    assert isinstance(FakeTTSClient(), TTSClient)
    assert isinstance(GoogleCloudTTSClient(InMemoryAudioStorage()), TTSClient)


# ── 真實呼叫：沒有憑證時自動 skip ─────────────────────────────────────

def _adc_available() -> bool:
    """
    ADC 是否可用（ADR-0003）。這裡不讀任何金鑰檔——沒有金鑰檔可讀。
    """
    try:
        import google.auth

        google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(
    not os.environ.get("RUN_REAL_GCP_TESTS"),
    reason="需要 RUN_REAL_GCP_TESTS=1 才跑真實 GCP 呼叫",
)
def test_real_synthesis_against_google_cloud_tts():
    """
    手動執行的真實驗證。

        RUN_REAL_GCP_TESTS=1 uv run python -m pytest tests/test_tts_client.py -k real

    常見失敗：
    - `could not find default credentials` → 跑 `gcloud auth application-default login`
    - TLS 相關錯誤 → 本機 Avast 的 TLS 攔截，同 #8
    """
    if not _adc_available():
        pytest.skip("找不到 ADC，請先跑 gcloud auth application-default login")

    client = GoogleCloudTTSClient(InMemoryAudioStorage())
    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert result is not None, f"合成失敗：{client.last_failure_reason}"
    assert result.audio_url
