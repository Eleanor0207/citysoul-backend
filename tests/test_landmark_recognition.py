"""
Ticket #22．地標視覺辨識（B13）。

驗收標準對照見 GitHub issue #22。隱私是這張票的核心約束，AC3／AC4 的測試
刻意不只靠註解宣稱——實際比對檔案系統、檢查原始碼、檢查回傳型別。
"""
import inspect
import os
import tempfile
from pathlib import Path

import pytest

from app.modules.brain.landmark_recognition import (
    FakeLandmarkRecognizer,
    LandmarkRecognizer,
    VertexAILandmarkRecognizer,
)

_FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0" + b"fake-jpeg-content" * 100  # 可辨識的假影像位元組


# ── 介面與回傳型別（issue #22 AC1）───────────────────────────────────────

def test_fake_satisfies_the_abstract_interface():
    assert isinstance(FakeLandmarkRecognizer(), LandmarkRecognizer)


def test_returns_a_plain_bool():
    result = FakeLandmarkRecognizer(result=True).recognize(_FAKE_IMAGE_BYTES, "taipei_longshan")
    assert result is True
    assert type(result) is bool


# ── 成功／不符（issue #22 AC2）───────────────────────────────────────────

def test_matching_landmark_returns_true():
    fake = FakeLandmarkRecognizer(result=True)
    assert fake.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan") is True


def test_non_matching_landmark_returns_false():
    fake = FakeLandmarkRecognizer(result=False)
    assert fake.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan") is False


# ── image_bytes 不寫入任何持久化儲存（issue #22 AC3）──────────────────────

def _snapshot_files(*directories: Path) -> set[Path]:
    files = set()
    for directory in directories:
        if directory.exists():
            files |= {p for p in directory.rglob("*") if p.is_file()}
    return files


def test_recognize_does_not_create_any_new_files():
    project_dir = Path(__file__).resolve().parent.parent
    temp_dir = Path(tempfile.gettempdir())

    before = _snapshot_files(project_dir, temp_dir)

    VertexAILandmarkRecognizer(
        client_factory=lambda: _RaisingClient()
    ).recognize(_FAKE_IMAGE_BYTES, "taipei_longshan")

    after = _snapshot_files(project_dir, temp_dir)

    new_files = after - before
    # __pycache__ 是 import 的正常副作用，跟這支函式有沒有持久化影像無關。
    new_files = {f for f in new_files if "__pycache__" not in f.parts}
    assert new_files == set(), f"呼叫後多出這些檔案：{new_files}"


class _RaisingClient:
    class models:
        @staticmethod
        def generate_content(**kwargs):
            raise ConnectionError("模擬連線錯誤，不應該走到這裡才失敗")


def test_source_code_has_no_persistence_calls():
    """
    grep 原始碼是否有寫檔／上傳操作——mutation 驗證：加一行 `open(..., "wb")`
    寫入 `image_bytes`，這條測試必須變紅。
    """
    import app.modules.brain.landmark_recognition as module

    source = inspect.getsource(module)
    forbidden_patterns = ['"wb"', "'wb'", "storage.Client", "upload_from", ".save("]
    for pattern in forbidden_patterns:
        assert pattern not in source, f"原始碼中出現疑似持久化寫入的呼叫：{pattern}"


# ── 辨識結束後不再持有影像（issue #22 AC4）───────────────────────────────

def test_no_module_level_state_holds_image_bytes():
    import app.modules.brain.landmark_recognition as module

    VertexAILandmarkRecognizer(client_factory=lambda: _RaisingClient()).recognize(
        _FAKE_IMAGE_BYTES, "taipei_longshan"
    )

    for value in vars(module).values():
        if isinstance(value, bytes):
            assert _FAKE_IMAGE_BYTES != value
        if isinstance(value, (dict, list, set)):
            assert _FAKE_IMAGE_BYTES not in value


def test_fake_recognizer_does_not_retain_image_bytes():
    """連測試用的 fake 都不留一份影像——即用即丟不能只在真實實作裡做到。"""
    fake = FakeLandmarkRecognizer()
    fake.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan")

    assert not any(isinstance(v, bytes) for v in vars(fake).values())


# ── 呼叫失敗／逾時回 False，不拋例外（issue #22 AC5）──────────────────────

@pytest.mark.parametrize(
    "broken_factory",
    [
        lambda: _RaisingClient(),
    ],
)
def test_real_recognizer_returns_false_on_connection_error(broken_factory):
    recognizer = VertexAILandmarkRecognizer(client_factory=broken_factory)
    assert recognizer.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan") is False
    assert recognizer.last_failure_reason is not None


def test_real_recognizer_returns_false_on_timeout():
    class _HangingClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                import time

                time.sleep(5)
                return None

    recognizer = VertexAILandmarkRecognizer(
        client_factory=lambda: _HangingClient(), timeout_seconds=0.1,
    )
    assert recognizer.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan") is False
    assert "秒未回應" in recognizer.last_failure_reason


def test_real_recognizer_returns_false_on_5xx_like_error():
    class _ServerErrorClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise RuntimeError("500 Internal Server Error")

    recognizer = VertexAILandmarkRecognizer(client_factory=lambda: _ServerErrorClient())
    assert recognizer.recognize(_FAKE_IMAGE_BYTES, "taipei_longshan") is False


# ── 回傳 False 時任務仍可完成（issue #22 AC6）─────────────────────────────

def test_false_result_does_not_block_quest_completion(client, db_session):
    """
    地標辨識失敗只是拿不到徽章，不阻擋任務完成——見 POST /quests/{questId}/complete
    （#34）本身完全不把辨識結果當成完成的前提條件，這裡用一次端到端流程
    證明「辨識回 False」跟「任務能不能完成」是兩件獨立的事。
    """
    import uuid

    from app.modules.body import models
    from app.modules.body.encounter_tokens import issue_encounter_token
    from app.modules.body.quests import quest_id_for_spirit

    spirit_id = f"test-spirit-{uuid.uuid4()}"
    spirit = models.Spirit(
        spirit_id=spirit_id, display_name="測試地標",
        latitude=25.0, longitude=121.5, summon_radius_meters=50, is_active=True,
    )
    db_session.add(spirit)
    db_session.commit()

    player_body = client.post(
        "/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}
    ).json()
    player_id = uuid.UUID(player_body["player_id"])
    session_token = player_body["session_token"]

    client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit_id, "latitude": 25.0, "longitude": 121.5},
        headers={"Authorization": f"Bearer {session_token}"},
    )

    # 地標辨識回 False——不阻擋接下來的任務完成請求。
    recognized = FakeLandmarkRecognizer(result=False).recognize(_FAKE_IMAGE_BYTES, spirit_id)
    assert recognized is False

    enc = issue_encounter_token(player_id, spirit_id)
    response = client.post(
        f"/api/v1/quests/{quest_id_for_spirit(spirit_id)}/complete",
        json={"completion_evidence": {}},
        headers={"Authorization": f"Bearer {session_token}", "X-Encounter-Token": enc},
    )

    assert response.status_code == 200

    db_session.query(models.QuestProgress).filter_by(player_id=player_id).delete()
    db_session.query(models.ResonanceEvent).filter_by(player_id=player_id).delete()
    db_session.query(models.Resonance).filter_by(player_id=player_id).delete()
    db_session.delete(spirit)
    db_session.commit()
