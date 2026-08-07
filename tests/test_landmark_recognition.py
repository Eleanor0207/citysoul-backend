"""
Ticket #22．地標視覺辨識（B13，雲端 Gemini 多模態，即用即丟）。

驗收標準對照見 GitHub issue #22。跟 `tests/test_gemini_client.py` 同一種
風格：真實 SDK 從來不載入，模型物件由 `client_factory` 注入，完全不需要
GCP 憑證。

AC6（回傳 False 時呼叫端仍可完成任務）沒有對應的整合測試——任務完成端點
（#34）還沒落地，這裡只驗證這支函式本身「失敗不拋例外」，「不阻擋任務」
的責任在呼叫端，等 #34 或 #43 接上時再驗整合行為。
"""
import threading
import time

import pytest

from app.modules.body.models import Spirit
from app.modules.brain.landmark_recognition import (
    FakeLandmarkRecognitionClient,
    LandmarkRecognitionClient,
    VertexAILandmarkRecognitionClient,
    recognize_landmark,
)


class _StubModels:
    def __init__(self, *, matches, raises=None):
        self._matches = matches
        self._raises = raises
        self.calls = []

    def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "contents": contents})
        if self._raises is not None:
            raise self._raises
        return _StubResponse("YES" if self._matches else "NO")


class _StubResponse:
    def __init__(self, text):
        self.text = text


class _StubClient:
    def __init__(self, *, matches=True, raises=None):
        self.models = _StubModels(matches=matches, raises=raises)


class _HangingModels:
    def __init__(self, released):
        self._released = released
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        self._released.wait(timeout=10)
        return _StubResponse("YES")


class _HangingClient:
    """永遠不回應的 client，用來驗證逾時。10 秒後自行放行，不留卡死的執行緒。"""

    def __init__(self):
        self.released = threading.Event()
        self.models = _HangingModels(self.released)


def _real_client(stub, **kwargs):
    return VertexAILandmarkRecognitionClient(client_factory=lambda: stub, **kwargs)


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = Spirit(
        spirit_id=unique_spirit_id,
        display_name="艋舺龍山寺",
        latitude=25.0,
        longitude=121.5,
        summon_radius_meters=50,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.delete(row)
    db_session.commit()


# ── 介面（AC1） ──────────────────────────────────────────────────────────


def test_both_implementations_satisfy_the_interface():
    assert isinstance(_real_client(_StubClient()), LandmarkRecognitionClient)
    assert isinstance(FakeLandmarkRecognitionClient(), LandmarkRecognitionClient)


def test_abstract_base_cannot_be_instantiated():
    with pytest.raises(TypeError):
        LandmarkRecognitionClient()


# ── 成功／不符（AC2） ────────────────────────────────────────────────────


def test_recognize_landmark_returns_true_when_it_matches(db_session, spirit):
    fake = FakeLandmarkRecognitionClient(result=True)

    result = recognize_landmark(db_session, b"fake-image-bytes", spirit.spirit_id, client=fake)

    assert result is True
    assert fake.calls == ["艋舺龍山寺"]


def test_recognize_landmark_returns_false_when_it_does_not_match(db_session, spirit):
    fake = FakeLandmarkRecognitionClient(result=False)

    result = recognize_landmark(db_session, b"fake-image-bytes", spirit.spirit_id, client=fake)

    assert result is False


def test_unknown_spirit_returns_false(db_session, unique_spirit_id):
    fake = FakeLandmarkRecognitionClient(result=True)

    result = recognize_landmark(db_session, b"fake-image-bytes", unique_spirit_id, client=fake)

    assert result is False
    assert fake.calls == []  # 連 client 都不該被呼叫——沒有地標名稱可以比對


# ── 失敗／逾時回 False，不拋例外（AC5） ──────────────────────────────────


@pytest.mark.parametrize(
    "failure",
    [ConnectionError("connection reset"), TimeoutError("deadline exceeded")],
)
def test_client_failures_fall_back_to_false_without_raising(failure):
    client = _real_client(_StubClient(raises=failure))

    result = client.recognize(b"fake-image-bytes", "艋舺龍山寺")

    assert result is False


def test_an_unexpected_exception_type_also_falls_back():
    client = _real_client(_StubClient(raises=RuntimeError("something unexpected")))

    assert client.recognize(b"fake-image-bytes", "艋舺龍山寺") is False


def test_a_hanging_call_falls_back_instead_of_blocking_forever():
    hanging = _HangingClient()
    client = _real_client(hanging, timeout_seconds=0.05)

    result = client.recognize(b"fake-image-bytes", "艋舺龍山寺")

    assert result is False
    hanging.released.set()  # 讓背景執行緒收尾，不留著卡住


def test_model_construction_failure_also_falls_back():
    def explode():
        raise RuntimeError("ADC 憑證找不到")

    client = VertexAILandmarkRecognitionClient(client_factory=explode)

    assert client.recognize(b"fake-image-bytes", "艋舺龍山寺") is False


# ── Fake 記錄呼叫（AC7 的一部分） ─────────────────────────────────────────


def test_fake_records_landmark_names_it_was_asked_about():
    fake = FakeLandmarkRecognitionClient()

    fake.recognize(b"a", "艋舺龍山寺")
    fake.recognize(b"b", "台北101")

    assert fake.calls == ["艋舺龍山寺", "台北101"]


# ── 不持久化、不殘留（AC3／AC4） ──────────────────────────────────────────


def test_module_source_contains_no_persistent_write_operations():
    """
    AC3：辨識過程不該把 `image_bytes` 寫進任何持久化儲存。用簡單字串比對
    掃過模組原始碼——真的完整測這件事要靠檔案系統前後比對（下一個測試），
    這裡是 issue AC 明確要求的靜態檢查那一半。
    """
    from pathlib import Path

    source = Path(
        __import__("app.modules.brain.landmark_recognition", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")

    forbidden_patterns = ["open(", "'wb'", '"wb"', "upload_from_", "blob.upload"]
    found = [p for p in forbidden_patterns if p in source]

    assert found == [], f"發現可疑的持久化寫入模式：{found}"


def test_recognition_does_not_write_any_new_files(tmp_path, db_session, spirit, monkeypatch):
    """
    AC3 的動態一半：實際跑一次辨識，確認呼叫過程中沒有任何檔案被建立。
    在 `tmp_path`（每個測試自己的暫存目錄）底下跑，就算真的寫了檔案也不會
    汙染到別的地方，而我們能直接看到多出來的檔案。
    """
    monkeypatch.chdir(tmp_path)
    fake = FakeLandmarkRecognitionClient(result=True)

    recognize_landmark(db_session, b"fake-image-bytes" * 1000, spirit.spirit_id, client=fake)

    assert list(tmp_path.rglob("*")) == []


def test_return_value_is_a_plain_bool_not_the_image_bytes(db_session, spirit):
    """AC4：回傳值不夾帶影像——純 bool，不是任何形式的容器。"""
    fake = FakeLandmarkRecognitionClient(result=True)
    image_bytes = b"fake-image-bytes"

    result = recognize_landmark(db_session, image_bytes, spirit.spirit_id, client=fake)

    assert type(result) is bool
    assert image_bytes not in fake.__dict__.values()


def test_no_module_level_state_retains_the_image_bytes(db_session, spirit):
    """AC4：模組層級沒有任何快取／清單持有過 image_bytes。"""
    import app.modules.brain.landmark_recognition as module

    fake = FakeLandmarkRecognitionClient(result=True)
    image_bytes = b"a-very-specific-marker-nobody-else-would-have"

    recognize_landmark(db_session, image_bytes, spirit.spirit_id, client=fake)

    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        assert value != image_bytes, f"模組層級變數 {name} 持有了 image_bytes"
