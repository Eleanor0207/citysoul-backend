"""
B13．地標視覺辨識（issue #22）。

真實 SDK 由 `client_factory` 注入 stub——**不需要 GCP 憑證**。

⚠️ 這個檔案有一組測試專門驗證**隱私**（SDD §7.7）。它們不是形式主義：
「即用即丟」如果只寫在註解裡，第一個為了除錯而加的 `_last_image = ...`
就會讓它失效，而不會有任何東西變紅。
"""
import gc
import os
from pathlib import Path

import pytest

from app.modules.brain.landmark_recognition import (
    FakeLandmarkRecognizer,
    LandmarkRecognizer,
    VertexAILandmarkRecognizer,
    recognize_landmark,
)

_SPIRIT = "taipei_longshan"
# 一段可辨識的假影像位元組。內容不重要——重點是它是**可追蹤的獨特位元組**，
# 這樣才驗得出它有沒有被留下來。
_IMAGE = b"\xff\xd8\xff\xe0FAKE-JPEG-PAYLOAD-8f3a91c7\xff\xd9"


class _StubResponse:
    def __init__(self, text):
        self.text = text


class _StubModels:
    def __init__(self, *, text="yes", raises=None, delay=0.0):
        self._text = text
        self._raises = raises
        self._delay = delay
        self.calls = []

    def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "contents": contents})
        if self._delay:
            import time

            time.sleep(self._delay)
        if self._raises:
            raise self._raises
        return _StubResponse(self._text)


class _StubClient:
    def __init__(self, **kwargs):
        self.models = _StubModels(**kwargs)


def _recognizer(**kwargs) -> tuple[VertexAILandmarkRecognizer, _StubClient]:
    stub = _StubClient(**kwargs)
    return VertexAILandmarkRecognizer(client_factory=lambda: stub), stub


# ── 介面與基本判定 ─────────────────────────────────────────────────────

def test_returns_a_plain_bool():
    """
    AC：回傳 `bool`。

    刻意不是結果物件——一個 dataclass 很容易在某次「順手多回傳一點資訊」時
    把影像夾帶出去。
    """
    recognizer, _ = _recognizer(text="yes")

    result = recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    assert result is True
    assert type(result) is bool


def test_match_returns_true():
    recognizer, _ = _recognizer(text="yes")

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is True


def test_mismatch_returns_false():
    recognizer, _ = _recognizer(text="no")

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is False


@pytest.mark.parametrize("text", ["no", "maybe", "不確定", "", "I think so"])
def test_anything_but_a_clear_yes_is_false(text):
    """
    **往保守的方向倒**：只有明確的 yes 才算成功。

    認不出來是拿不到徽章，代價很小；誤判成功則是給了不該給的獎勵。
    """
    recognizer, _ = _recognizer(text=text)

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is False


def test_spirit_id_reaches_the_prompt():
    recognizer, stub = _recognizer(text="yes")

    recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    contents = stub.models.calls[0]["contents"]
    assert any(_SPIRIT in str(part) for part in contents)


def test_empty_image_returns_false_without_calling_the_api():
    recognizer, stub = _recognizer(text="yes")

    assert recognize_landmark(recognizer, b"", _SPIRIT) is False
    assert stub.models.calls == []


# ── 失敗一律回 False ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "failure",
    [ConnectionError("connection refused"), RuntimeError("500 internal"), ValueError("weird")],
)
def test_failures_return_false_without_raising(failure):
    """AC (a) 連線錯誤、(c) API 5xx——皆回 False、不拋例外。"""
    recognizer, _ = _recognizer(raises=failure)

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is False
    assert recognizer.last_failure_reason


def test_timeout_returns_false():
    """AC (b) 逾時。"""
    stub = _StubClient(delay=0.3)
    recognizer = VertexAILandmarkRecognizer(client_factory=lambda: stub, timeout_seconds=0.05)

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is False
    assert "未回應" in recognizer.last_failure_reason


def test_empty_model_response_returns_false():
    recognizer, _ = _recognizer(text="")

    assert recognize_landmark(recognizer, _IMAGE, _SPIRIT) is False


# ══════════════════════════════════════════════════════════════════════
# 隱私（SDD §7.7）—— 這組不能只靠註解宣稱
# ══════════════════════════════════════════════════════════════════════

def _snapshot(directory: Path) -> set:
    return set(p for p in directory.rglob("*") if p.is_file())


def test_no_files_are_created_anywhere(tmp_path, monkeypatch):
    """
    🔒 AC：`image_bytes` 不寫入任何持久化儲存。

    ⚠️ **實際比對檔案系統**，不是讀程式碼判斷。AC 明說「這條不能只靠註解宣稱」。

    比對三個地方：專案目錄、系統暫存目錄、以及一個乾淨的 cwd。
    AC 指定要做 mutation 驗證的那一條——加一行把 image_bytes 寫檔，這裡必須紅。
    """
    project_dir = Path(__file__).resolve().parent.parent
    temp_dir = Path(tmp_path)

    # 把 cwd 換到空目錄，這樣相對路徑的寫檔會落在我們看得到的地方。
    monkeypatch.chdir(temp_dir)
    monkeypatch.setenv("TMPDIR", str(temp_dir))

    before_project = _snapshot(project_dir)
    before_temp = _snapshot(temp_dir)

    recognizer, _ = _recognizer(text="yes")
    recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    new_project = _snapshot(project_dir) - before_project
    new_temp = _snapshot(temp_dir) - before_temp

    # __pycache__ 是 Python 自己產的，跟影像無關。
    new_project = {p for p in new_project if "__pycache__" not in str(p)}

    assert not new_project, f"辨識過程在專案目錄留下了檔案：{new_project}"
    assert not new_temp, f"辨識過程在暫存目錄留下了檔案：{new_temp}"


def test_source_has_no_persistence_calls():
    """
    AC：grep 實作中是否有 `open(...,'wb')`、Cloud Storage 上傳等寫入操作。

    這條跟上面那條互補：檔案系統比對抓的是**這次執行**寫了什麼，這條抓的是
    **程式碼裡存在**的寫入路徑——包含那些只在特定分支才會走到、測試沒觸發的。
    """
    import ast

    source_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "modules"
        / "brain"
        / "landmark_recognition.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    forbidden = {"open", "write_bytes", "write_text", "upload_from_string", "upload_from_file"}
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in forbidden:
                found.append(f"{name}（第 {node.lineno} 行）")

    assert not found, f"實作裡有持久化寫入：{found}"


def test_module_holds_no_mutable_state():
    """
    🔒 AC：「即用即丟」包含**不留在記憶體結構裡**，不只是不寫檔。

    一個為了除錯而加的 `_last_image = image_bytes`，或一個「最近 N 次辨識」的
    list，都會讓玩家的照片在進程記憶體裡活到重啟為止。這條掃模組層級的變數，
    確保沒有任何容器可以裝得下影像。
    """
    import app.modules.brain.landmark_recognition as module

    containers = {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("__") and isinstance(value, (list, dict, set, bytes, bytearray))
    }

    assert not containers, f"模組層級有可變容器，影像可能被留下來：{list(containers)}"


def test_recognizer_instance_does_not_retain_the_image():
    """
    AC：辨識結束後不再持有影像。

    掃 recognizer 實例的所有屬性——包含 `last_failure_reason`，因為「把出錯的
    那張照片記下來看看」正是最容易發生的違規。
    """
    recognizer, _ = _recognizer(raises=ConnectionError("boom"))

    recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    for name, value in vars(recognizer).items():
        as_bytes = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
        assert _IMAGE not in as_bytes, f"辨識器的 {name} 仍然持有影像"


def test_fake_does_not_retain_the_image_either():
    """
    連 fake 都不記錄影像。

    如果只有正式實作守紀律，那份紀律就只存在於一個地方——而測試替身常常是
    後來被複製去別處的那一個。
    """
    fake = FakeLandmarkRecognizer()

    fake.recognize(_IMAGE, _SPIRIT)

    for name, value in vars(fake).items():
        as_bytes = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
        assert _IMAGE not in as_bytes, f"fake 的 {name} 仍然持有影像"


def test_return_value_carries_no_image():
    """回傳值是純 bool，不夾帶影像。"""
    recognizer, _ = _recognizer(text="yes")

    result = recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    assert isinstance(result, bool)
    assert not isinstance(result, (bytes, bytearray))


# ── 失敗不阻擋任務完成 ─────────────────────────────────────────────────

def test_false_result_is_not_an_error_condition():
    """
    AC：回傳 False 時呼叫端仍可完成任務。

    CONTEXT.md：本機地標辨識成功時給予特別徽章，**失敗不阻擋完成**。

    這一層能保證的是「False 是一個正常的回傳值，不是例外」——真正的整合驗證
    在 #44（landmark-photo 端點）。
    """
    recognizer = FakeLandmarkRecognizer(result=False)

    result = recognize_landmark(recognizer, _IMAGE, _SPIRIT)

    assert result is False  # 沒有拋任何東西


def test_both_implementations_satisfy_the_interface():
    assert isinstance(FakeLandmarkRecognizer(), LandmarkRecognizer)
    assert isinstance(VertexAILandmarkRecognizer(), LandmarkRecognizer)
