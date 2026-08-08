"""
#44．S12 紀念照片後端：`POST /quests/{questId}/landmark-photo`。

B13 以 fake 注入——**不需要 GCP 憑證**。

⚠️ 這裡最重要的一組是**隱私**（SDD §7.7）：照片只在記憶體處理、辨識完立即
捨棄。那組測試實際比對檔案系統，不靠註解宣稱。
"""
import uuid
from pathlib import Path

import pytest

from app.core.redis_client import redis_client
from app.main import app
from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import quest_id_for_spirit
from app.modules.body.quota import RESOURCE_LANDMARK_RECOGNITION
from app.modules.body.router import get_landmark_recognizer
from app.modules.body.sense_tokens import issue_sense_token
from app.modules.brain.landmark_recognition import FakeLandmarkRecognizer

_LAT, _LON = 25.0373983, 121.4997318
# 可追蹤的獨特位元組——這樣才驗得出它有沒有被留下來。
_IMAGE = b"\xff\xd8\xff\xe0FAKE-JPEG-PAYLOAD-8f3a91c7\xff\xd9"


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    db_session.query(models.EncounterCollection).filter_by(place_id=unique_spirit_id).delete()
    db_session.query(models.ResonanceEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.Resonance).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.QuestProgress).filter_by(
        quest_id=quest_id_for_spirit(unique_spirit_id)
    ).delete()
    db_session.delete(row)
    db_session.commit()


@pytest.fixture
def player(client):
    body = client.post("/api/v1/players", json={"device_id": f"test-device-{uuid.uuid4()}"}).json()
    return uuid.UUID(body["player_id"]), body["session_token"]


@pytest.fixture(autouse=True)
def _clear_quota():
    yield
    for key in redis_client.scan_iter("quota:*"):
        redis_client.delete(key)


@pytest.fixture
def recognizer():
    spy = FakeLandmarkRecognizer(result=True)
    app.dependency_overrides[get_landmark_recognizer] = lambda: spy
    yield spy
    app.dependency_overrides.pop(get_landmark_recognizer, None)


def _post_photo(client, quest_id, session_token=None, encounter=None, sense=None, image=_IMAGE):
    headers = {}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter
    if sense:
        headers[ENCOUNTER_TOKEN_HEADER] = sense
    return client.post(
        f"/api/v1/quests/{quest_id}/landmark-photo",
        files={"photo": ("shot.jpg", image, "image/jpeg")},
        headers=headers,
    )


def _resonance_value(db_session, player_id, spirit_id) -> int:
    db_session.expire_all()
    row = (
        db_session.query(models.Resonance)
        .filter_by(player_id=player_id, spirit_id=spirit_id)
        .first()
    )
    return row.resonance_value if row else 0


def _set_recognition_limit(db_session, player_id, value: int) -> int:
    player = db_session.query(models.Player).filter_by(player_id=player_id).one()
    row = (
        db_session.query(models.UsageTierLimit)
        .filter_by(tier_id=player.usage_tier_id, resource_type=RESOURCE_LANDMARK_RECOGNITION)
        .one()
    )
    original = row.limit_value
    row.limit_value = value
    db_session.commit()
    return original


# ── 表結構 ─────────────────────────────────────────────────────────────

def test_table_has_the_required_columns_and_unique(db_session):
    """
    AC：欄位符合 SDD §3.1、存在 `UNIQUE(player_id, place_id)`、
    **沒有**任何照片／座標／雜湊欄位。
    """
    from sqlalchemy import inspect

    inspector = inspect(db_session.bind)
    columns = {c["name"] for c in inspector.get_columns("encounter_collections")}
    uniques = [set(u["column_names"]) for u in inspector.get_unique_constraints("encounter_collections")]

    assert {"collection_id", "player_id", "place_id", "collected_at", "landmark_recognized", "resonance_awarded"} <= columns
    assert {"player_id", "place_id"} in uniques
    for banned in ["photo", "image", "hash", "latitude", "longitude", "coordinates"]:
        assert not any(banned in c for c in columns), f"表裡出現了 {banned} 相關欄位"


# ── 上傳與辨識 ─────────────────────────────────────────────────────────

def test_accepts_multipart_upload(client, spirit, player, recognizer):
    """AC：接受 multipart/form-data，回應含 `landmark_recognized`。"""
    pid, sess = player

    response = _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 200
    assert response.json()["landmark_recognized"] is True
    assert recognizer.call_count == 1


def test_recognition_failure_returns_false_not_an_error(client, spirit, player):
    """
    AC：辨識失敗回 `false`，任務仍可完成——玩家只是拿不到特別徽章。
    """
    app.dependency_overrides[get_landmark_recognizer] = lambda: FakeLandmarkRecognizer(result=False)
    pid, sess = player

    try:
        response = _post_photo(
            client,
            quest_id_for_spirit(spirit.spirit_id),
            session_token=sess,
            encounter=issue_encounter_token(pid, spirit.spirit_id),
        )

        assert response.status_code == 200
        assert response.json()["landmark_recognized"] is False
    finally:
        app.dependency_overrides.pop(get_landmark_recognizer, None)


def test_quest_can_still_be_completed_after_failed_recognition(client, spirit, player, db_session):
    """
    AC：辨識失敗**不阻擋任務完成**（CONTEXT.md）。

    這條走完整條路：拍照失敗 → 召喚 → 完成任務。
    """
    app.dependency_overrides[get_landmark_recognizer] = lambda: FakeLandmarkRecognizer(result=False)
    pid, sess = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    try:
        _post_photo(client, quest_id, session_token=sess, encounter=enc)

        client.post(
            "/api/v1/summon",
            json={"spirit_id": spirit.spirit_id, "latitude": _LAT, "longitude": _LON},
            headers={"Authorization": f"Bearer {sess}"},
        )
        completion = client.post(
            f"/api/v1/quests/{quest_id}/complete",
            json={"completion_evidence": {}},
            headers={"Authorization": f"Bearer {sess}", ENCOUNTER_TOKEN_HEADER: enc},
        )

        assert completion.status_code == 200
    finally:
        app.dependency_overrides.pop(get_landmark_recognizer, None)


# ── 憑證 ───────────────────────────────────────────────────────────────

def test_missing_tokens_returns_401(client, spirit, player):
    _, sess = player

    assert _post_photo(
        client, quest_id_for_spirit(spirit.spirit_id), session_token=sess
    ).status_code == 401


def test_sense_token_cannot_upload_a_photo(client, spirit, player, db_session):
    """
    🔒 AC：持 Sense Token 不可呼叫（SDD §6：不可觸發相機疊圖相關動作）。

    兩者用不同金鑰簽章，所以塞進 `X-Encounter-Token` 會在驗章就失敗。
    """
    pid, sess = player

    response = _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        sense=issue_sense_token(pid, spirit.spirit_id),
    )

    assert response.status_code in (401, 403)
    db_session.expire_all()
    assert (
        db_session.query(models.EncounterCollection)
        .filter_by(player_id=pid, place_id=spirit.spirit_id)
        .count()
        == 0
    )


def test_encounter_token_for_another_spirit_returns_403(client, spirit, player):
    pid, sess = player

    response = _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, "some-other-spirit"),
    )

    assert response.status_code == 403


# ══════════════════════════════════════════════════════════════════════
# 隱私（SDD §7.7）—— 實際比對檔案系統，不靠註解宣稱
# ══════════════════════════════════════════════════════════════════════

def _snapshot(directory: Path) -> set:
    return {p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in str(p)}


def test_photo_is_never_written_to_disk(client, spirit, player, recognizer, tmp_path, monkeypatch):
    """
    🔒 AC：照片不寫入任何持久化儲存。**實際比對檔案系統。**

    AC 指定要做 mutation 驗證的其中一條。
    """
    project_dir = Path(__file__).resolve().parent.parent
    temp_dir = Path(tmp_path)
    monkeypatch.chdir(temp_dir)

    before_project = _snapshot(project_dir)
    before_temp = _snapshot(temp_dir)

    pid, sess = player
    _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert not (_snapshot(project_dir) - before_project), "拍照流程在專案目錄留下了檔案"
    assert not (_snapshot(temp_dir) - before_temp), "拍照流程在暫存目錄留下了檔案"


def test_endpoint_source_has_no_persistence_calls():
    """
    grep 實作是否有寫檔或 Cloud Storage 上傳。

    跟上面那條互補：檔案比對抓的是**這次執行**寫了什麼，這條抓的是**程式碼裡
    存在**的寫入路徑，包含測試沒觸發到的分支。
    """
    import ast

    source = (
        Path(__file__).resolve().parent.parent / "app" / "modules" / "body" / "router.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 只看 landmark_photo 與它的 helper。
    targets = {"landmark_photo", "_record_landmark_collection"}
    forbidden = {"open", "write_bytes", "write_text", "upload_from_string", "upload_from_file"}
    found = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = None
                    if isinstance(inner.func, ast.Name):
                        name = inner.func.id
                    elif isinstance(inner.func, ast.Attribute):
                        name = inner.func.attr
                    if name in forbidden:
                        found.append(f"{node.name} → {name}（第 {inner.lineno} 行）")

    assert not found, f"拍照端點有持久化寫入：{found}"


def test_response_carries_nothing_about_the_image(client, spirit, player, recognizer):
    """
    回應裡沒有 URL、沒有雜湊、沒有尺寸。

    照片辨識完就捨棄了，回應也不該留下它存在過的痕跡。
    """
    pid, sess = player

    body = _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert set(body) == {"landmark_recognized", "resonance_awarded", "resonance_value"}


def test_stored_row_carries_no_image_bytes(client, spirit, player, recognizer, db_session):
    """收藏那一列的每個欄位都不含影像位元組。"""
    pid, sess = player

    _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    db_session.expire_all()
    row = (
        db_session.query(models.EncounterCollection)
        .filter_by(player_id=pid, place_id=spirit.spirit_id)
        .one()
    )

    for column in row.__table__.columns.keys():
        value = getattr(row, column)
        as_bytes = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
        assert _IMAGE not in as_bytes


# ── 重複收藏 ───────────────────────────────────────────────────────────

def test_collecting_twice_does_not_award_twice(client, spirit, player, recognizer, db_session):
    """
    🔒 AC：第二次仍回 200、共鳴值維持 +10、表中只有 1 列。

    去重靠 `UNIQUE(player_id, place_id)`——先寫、撞到約束才知道重複，不是先查
    再寫（同 #16 的教訓）。

    AC 指定要做 mutation 驗證的其中一條。
    """
    pid, sess = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    first = _post_photo(client, quest_id, session_token=sess, encounter=enc)
    second = _post_photo(client, quest_id, session_token=sess, encounter=enc)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["resonance_awarded"] is True
    assert second.json()["resonance_awarded"] is False
    assert second.json()["resonance_value"] == 10

    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10
    db_session.expire_all()
    assert (
        db_session.query(models.EncounterCollection)
        .filter_by(player_id=pid, place_id=spirit.spirit_id)
        .count()
        == 1
    )


def test_first_collection_awards_ten(client, spirit, player, recognizer, db_session):
    pid, sess = player

    body = _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_awarded"] is True
    assert body["resonance_value"] == 10


def test_recognition_result_is_recorded_on_the_row(client, spirit, player, recognizer, db_session):
    pid, sess = player

    _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    db_session.expire_all()
    row = (
        db_session.query(models.EncounterCollection)
        .filter_by(player_id=pid, place_id=spirit.spirit_id)
        .one()
    )
    assert row.landmark_recognized is True
    assert row.resonance_awarded is True


# ── 配額 ──────────────────────────────────────────────────────────────

def test_quota_exhausted_returns_429_without_calling_b13(
    client, spirit, player, recognizer, db_session
):
    """
    🔒 AC：超過辨識配額回 429，且 **B13 未被呼叫**。

    配額擋在辨識之前，避免產生成本。
    """
    pid, sess = player
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)
    original = _set_recognition_limit(db_session, pid, 1)

    try:
        assert _post_photo(client, quest_id, session_token=sess, encounter=enc).status_code == 200
        before = recognizer.call_count

        response = _post_photo(client, quest_id, session_token=sess, encounter=enc)

        assert response.status_code == 429
        assert recognizer.call_count == before, "配額擋下之後仍然呼叫了 B13"
    finally:
        _set_recognition_limit(db_session, pid, original)


def test_photo_quota_is_separate_from_dialogue_quota(client, spirit, player, recognizer, db_session):
    """
    辨識配額用滿不影響對話額度。兩種資源各自計數（#32）。
    """
    from app.modules.body.quota import RESOURCE_DIALOGUE, current_usage

    pid, sess = player

    _post_photo(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert current_usage(pid, RESOURCE_LANDMARK_RECOGNITION) == 1
    assert current_usage(pid, RESOURCE_DIALOGUE) == 0


# ── quest_id 格式 ─────────────────────────────────────────────────────

def test_malformed_quest_id_returns_404(client, player):
    pid, sess = player

    response = _post_photo(
        client,
        "nonsense",
        session_token=sess,
        encounter=issue_encounter_token(pid, "whatever"),
    )

    assert response.status_code == 404
