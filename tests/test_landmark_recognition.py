"""
Ticket #22．地標視覺辨識（B13，雲端 Gemini 多模態，即用即丟）。

驗收標準對照見 GitHub issue #22。跟 `tests/test_gemini_client.py` 同一種
風格：真實 SDK 從來不載入，模型物件由 `client_factory` 注入，完全不需要
GCP 憑證。

AC6（回傳 False 時呼叫端仍可完成任務）現在有真的整合測試了——這張票剛做完
的時候 `POST /quests/{questId}/complete`（#34）還不存在，只能先驗「這支
函式自己不拋例外」；#34 落地之後才驗得到「辨識失敗的玩家確實還是能完成
任務」。見檔案最後一節。
"""
import threading

import pytest

from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.models import (
    QuestProgress,
    Resonance,
    ResonanceEvent,
    Spirit,
)
from app.modules.body.quests import quest_id_for_spirit
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

    # 任務完成的整合測試（AC6）會寫 resonance／resonance_events，兩張表都有
    # 外鍵指向 spirits——不先清掉，下面那行 delete 會撞 FK 而不是安靜收尾。
    db_session.query(ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(Resonance).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(QuestProgress).filter_by(
        quest_id=quest_id_for_spirit(unique_spirit_id)
    ).delete()
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


# ── 辨識失敗不阻擋任務完成（AC6，整合） ───────────────────────────────────
#
# CONTEXT.md：本機地標辨識成功時給予特別徽章，**失敗不阻擋完成**。
# 這一節要驗的不是 `recognize_landmark` 自己不拋例外（上面已經驗過了），
# 而是「辨識回 False 的那個玩家，走完整條 API 流程仍然拿得到任務完成與
# 共鳴值」——那是兩件不同的事，只有整合起來才看得到。


@pytest.fixture
def player(client, unique_device_id):
    return client.post("/api/v1/players", json={"device_id": unique_device_id}).json()


def _complete_quest(client, player, spirit):
    return client.post(
        f"/api/v1/quests/{quest_id_for_spirit(spirit.spirit_id)}/complete",
        json={"completion_evidence": {}},
        headers={
            "Authorization": f"Bearer {player['session_token']}",
            ENCOUNTER_TOKEN_HEADER: issue_encounter_token(
                player["player_id"], spirit.spirit_id
            ),
        },
    )


def test_failed_recognition_does_not_block_quest_completion(
    client, db_session, player, spirit
):
    """
    辨識回 False（照片拍錯、模型看不出來、Gemini 掛掉都算），玩家仍然
    完成得了任務、共鳴值照常入帳——少的只有特別徽章。
    """
    recognized = recognize_landmark(
        db_session,
        b"a-photo-of-something-else",
        spirit.spirit_id,
        client=FakeLandmarkRecognitionClient(result=False),
    )
    assert recognized is False

    resp = _complete_quest(client, player, spirit)

    assert resp.status_code == 200
    assert resp.json()["resonance_value"] == 20

    progress = (
        db_session.query(QuestProgress)
        .filter_by(quest_id=quest_id_for_spirit(spirit.spirit_id))
        .one()
    )
    assert progress.status == "completed"


def test_recognition_outcome_does_not_change_the_completion_result(
    client, db_session, player, spirit
):
    """
    辨識成功與失敗，任務完成的結果**完全相同**——徽章是額外的獎勵，不是
    完成條件的一部分。兩者若有差別，就代表辨識偷偷變成了完成門檻。

    （徽章本身還沒有實作；等它出現時，這個測試要改成「兩邊的完成結果相同，
    但只有成功那邊拿到徽章」，而不是刪掉。）
    """
    assert (
        recognize_landmark(
            db_session,
            b"a-good-photo",
            spirit.spirit_id,
            client=FakeLandmarkRecognitionClient(result=True),
        )
        is True
    )

    resp = _complete_quest(client, player, spirit)

    assert resp.status_code == 200
    assert resp.json()["resonance_value"] == 20


def test_quest_completes_even_when_the_recognition_client_is_broken(
    client, db_session, player, spirit
):
    """
    連辨識服務本身壞掉（連線錯誤）都不該影響任務——`recognize_landmark`
    把例外吞成 False，完成流程根本不會知道發生過什麼事。
    """
    recognized = recognize_landmark(
        db_session,
        b"a-photo",
        spirit.spirit_id,
        client=_real_client(_StubClient(raises=ConnectionError("connection reset"))),
    )
    assert recognized is False

    assert _complete_quest(client, player, spirit).status_code == 200
