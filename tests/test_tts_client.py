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
    VoiceProfile,
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


# ── GCS 存放：成功路徑與簽章方式 ───────────────────────────────────────
#
# 這一組補的是一個真實發生過的缺口：原本只有「沒設 bucket」與「建 client 失敗」
# 兩個測試，兩者都在碰到 blob 之前就 return 了。也就是說
# `upload_from_string()` 與 `generate_signed_url()` 從來沒有被執行過，而 bug
# 正好在後者——線上簽章失敗、例外被 store() 吞掉、降級成沒有語音，測試全綠。
#
# 所以這裡驗的不只是「有沒有例外」，而是「有沒有用對的方式簽」。


class _StubBlob:
    def __init__(self):
        self.uploaded = None
        self.content_type = None
        self.signed_with = None

    def upload_from_string(self, data, content_type=None):
        self.uploaded = data
        self.content_type = content_type

    def generate_signed_url(self, **kwargs):
        self.signed_with = kwargs
        return "https://signed.test/audio.mp3"


class _StubBucket:
    def __init__(self, blob):
        self._blob = blob
        self.blob_names = []

    def blob(self, name):
        self.blob_names.append(name)
        return self._blob


class _StubStorageClient:
    def __init__(self, bucket):
        self._bucket = bucket
        self.bucket_names = []

    def bucket(self, name):
        self.bucket_names.append(name)
        return self._bucket


REAL_EMAIL = "citysoul-run@citysoul.iam.gserviceaccount.com"


class _ComputeCredentials:
    """
    Cloud Run 上的憑證形狀：有 token、沒有私鑰，而且**剛建好時
    `service_account_email` 是字面值 "default"**——那是 metadata server 的別名。
    真正的信箱要等 `refresh()` 內部的 `_retrieve_info()` 才會填進來。

    這個 stub 刻意照著那個順序模擬。之前的版本一開始就給真信箱，於是
    「先讀 email 後 refresh」的錯誤順序測不出來，線上換回
    「Invalid form of account ID default」400。
    """

    def __init__(self, valid=False):
        self.valid = valid
        self.token = "ya29.stub-token"
        self.service_account_email = "default"
        self.refresh_count = 0

    def refresh(self, request):  # noqa: ARG002
        self.refresh_count += 1
        self.valid = True
        self.service_account_email = REAL_EMAIL


def _gcs(credentials):
    blob = _StubBlob()
    bucket = _StubBucket(blob)
    client = _StubStorageClient(bucket)
    storage = GcsAudioStorage(
        bucket_name="citysoul-tts-audio",
        url_ttl_seconds=3600,
        client_factory=lambda: client,
        credentials_factory=lambda: credentials,
    )
    return storage, client, bucket, blob


def test_gcs_storage_uploads_and_returns_the_signed_url():
    storage, client, bucket, blob = _gcs(_ComputeCredentials())

    url = storage.store(b"audio-bytes")

    assert url == "https://signed.test/audio.mp3"
    assert client.bucket_names == ["citysoul-tts-audio"]
    assert blob.uploaded == b"audio-bytes"
    assert blob.content_type == "audio/mpeg"

    # 音檔放在 tts/ 底下，檔名隨機，不含台詞內容。
    assert len(bucket.blob_names) == 1
    assert bucket.blob_names[0].startswith("tts/")
    assert bucket.blob_names[0].endswith(".mp3")


def test_gcs_storage_signs_through_iam_when_credentials_have_no_private_key():
    """
    這是線上真正壞掉的地方。沒有這個測試，改回本地簽章不會有任何一個測試轉紅，
    只會安靜地不發聲音。
    """
    credentials = _ComputeCredentials()
    storage, _, _, blob = _gcs(credentials)

    storage.store(b"audio-bytes")

    assert blob.signed_with["service_account_email"] == REAL_EMAIL
    assert blob.signed_with["access_token"] == "ya29.stub-token"


def test_gcs_storage_resolves_the_real_email_before_signing():
    """
    憑證已經 valid、但 email 還停在 "default" 別名上時，要再 refresh 一次把它
    換成真的信箱。送 "default" 進 IAM signBytes 會換回 400，而那個例外會被
    store() 吞掉——log 上只會看到「音檔存放失敗」，聽的人只會覺得沒有聲音。
    """
    credentials = _ComputeCredentials(valid=True)
    storage, _, _, blob = _gcs(credentials)

    storage.store(b"audio-bytes")

    assert credentials.refresh_count == 1
    assert blob.signed_with["service_account_email"] == REAL_EMAIL
    assert blob.signed_with["service_account_email"] != "default"


def test_gcs_storage_refreshes_an_expired_token_before_signing():
    credentials = _ComputeCredentials(valid=False)
    storage, _, _, blob = _gcs(credentials)

    storage.store(b"audio-bytes")

    assert credentials.refresh_count == 1
    assert blob.signed_with["access_token"] == "ya29.stub-token"
    assert blob.signed_with["service_account_email"] == REAL_EMAIL


def test_gcs_storage_leaves_signing_to_the_sdk_for_local_user_credentials():
    """
    本機的使用者憑證既沒有私鑰、也沒有服務帳號 email。兩條路都走不了時要安靜
    降級——在這裡丟例外只會把「沒有語音」變成當機。
    """

    class _UserCredentials:
        valid = True

    storage, _, _, blob = _gcs(_UserCredentials())

    storage.store(b"audio-bytes")

    assert "service_account_email" not in blob.signed_with
    assert "access_token" not in blob.signed_with


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


# ── VoiceProfile：每隻靈魂一把嗓音（0031）───────────────────────────────


def test_voice_profile_reaches_the_sdk():
    """
    🔒 嗓音、語速、音高三者都要真的傳到 SDK。

    這是整條路徑最容易安靜壞掉的地方：少傳 `pitch` 不會有任何錯誤訊息，
    只是九隻靈魂又變回同一個聲音，而那要靠耳朵才聽得出來。
    """
    client, _storage, stub = _client()

    client.synthesize(
        "你好",
        voice=VoiceProfile(name="cmn-TW-Wavenet-C", speaking_rate=0.88, pitch=-2.0),
    )

    call = stub.calls[0]
    assert call["voice"].name == "cmn-TW-Wavenet-C"
    assert call["audio_config"].speaking_rate == pytest.approx(0.88)
    assert call["audio_config"].pitch == pytest.approx(-2.0)


def test_voice_profile_overrides_the_constructor_default():
    """
    profile 優先於建構時的嗓音。

    整個行程共用一個 client 實例（`router.get_tts_client`），而嗓音是每隻靈魂
    各自的——建構時那一個只能是「沒有指定時的退路」，不能贏過呼叫端。
    """
    stub = _StubTTSClient()
    client = GoogleCloudTTSClient(
        InMemoryAudioStorage(),
        voice_name="cmn-TW-Wavenet-A",
        client_factory=lambda: stub,
    )

    client.synthesize("你好", voice=VoiceProfile(name="cmn-TW-Wavenet-B"))

    assert stub.calls[0]["voice"].name == "cmn-TW-Wavenet-B"


def test_no_voice_keeps_the_old_behaviour():
    """
    🔒 回歸保護：不給 profile 時的行為與加這個參數之前完全一致。

    也就是沿用建構時的嗓音，而且**不送** speaking_rate／pitch——送預設值看起來
    無害，但那會讓「沒有人指定過」與「有人指定成 1.0」在 API 呼叫上長得一樣。
    """
    stub = _StubTTSClient()
    client = GoogleCloudTTSClient(
        InMemoryAudioStorage(),
        voice_name="cmn-TW-Wavenet-A",
        client_factory=lambda: stub,
    )

    client.synthesize("你好")

    call = stub.calls[0]
    assert call["voice"].name == "cmn-TW-Wavenet-A"
    assert call["audio_config"].speaking_rate == 0.0
    assert call["audio_config"].pitch == 0.0


def test_fake_client_records_the_voice():
    """FakeTTSClient 要記下嗓音，否則端點測試無法斷言「這隻用了哪一把」。"""
    fake = FakeTTSClient()
    profile = VoiceProfile(name="cmn-TW-Wavenet-B", pitch=1.5)

    fake.synthesize("你好", voice=profile)
    fake.synthesize("再見")

    assert fake.voices == [profile, None]
