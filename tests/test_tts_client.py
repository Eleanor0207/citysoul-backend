"""
Ticket #21．TTS 串接（B10，v2.1 已移除 viseme 時間軸）。

驗收標準對照見 GitHub issue #21。真實實作（`GoogleCloudTTSClient`）刻意
還沒寫——原因見 `app/modules/brain/tts.py` 模組 docstring：需要先決定音檔
儲存策略（GCS bucket？簽章 URL？），那是超出這張票的基礎設施問題，已與人
確認先只做抽象介面＋fake。因此 AC5（語言代碼 grep 真實實作）與 AC7（真實
呼叫的手動驗證）暫不適用，等真實實作補上時再補測試。
"""
import ast
from pathlib import Path

import pytest

from app.modules.brain.tts import FakeTTSClient, TTSClient, TTSResult

_APP_ROOT = Path(__file__).resolve().parent.parent / "app"


# ── 合成成功／失敗（AC3／AC4／AC6） ──────────────────────────────────────


def test_synthesize_success_returns_playable_audio_url():
    client = FakeTTSClient(response="https://example.test/audio/abc.mp3")

    result = client.synthesize("今夜的香火比平常更盛一些。")

    assert result == TTSResult(audio_url="https://example.test/audio/abc.mp3")


def test_synthesize_failure_returns_none_without_raising():
    """
    模擬 (a) 連線錯誤／(b) 逾時：`synthesize` 的契約本來就不區分失敗原因，
    兩者對呼叫端的意義相同——回 `None`，改用純文字呈現。
    """
    client = FakeTTSClient(response=None)

    result = client.synthesize("這句話合成失敗")

    assert result is None


def test_synthesize_records_the_text_it_was_asked_to_speak():
    client = FakeTTSClient()

    client.synthesize("第一句")
    client.synthesize("第二句")

    assert client.calls == ["第一句", "第二句"]


# ── TTSResult 形狀（AC2） ────────────────────────────────────────────────


def test_tts_result_schema_contains_only_audio_url():
    assert set(TTSResult.model_fields) == {"audio_url"}


def test_no_viseme_identifiers_anywhere_in_app_source():
    """
    Google Cloud TTS 不提供 viseme／phoneme 時間軸，v2.1 §6.1 把它從契約
    裡拿掉。用 AST 掃過 `app/` 底下所有程式碼的**識別碼**（變數、欄位、
    函式、類別名稱、屬性存取）——故意不掃字串常數與註解，解釋「為何移除」
    的說明文字本來就該留著，不該被這個測試逼著刪掉。
    """
    offenders = []
    for path in _APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
            elif isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.arg):
                name = node.arg
            elif isinstance(node, ast.Attribute):
                name = node.attr

            if name and "viseme" in name.lower():
                offenders.append(f"{path.relative_to(_APP_ROOT.parent)}:{node.lineno}:{name}")

    assert offenders == [], f"發現 viseme 相關識別碼，v2.1 已移除這個設計：{offenders}"


# ── 抽象介面（AC1） ──────────────────────────────────────────────────────


def test_tts_client_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        TTSClient()
