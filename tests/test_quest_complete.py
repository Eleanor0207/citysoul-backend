"""
#34．`POST /quests/{questId}/complete` 任務完成與共鳴入帳（SDD §7.5 前半）。

對真實 Postgres 跑。任務進度由 `/summon` 建立（那是它唯一的建立路徑），
所以這裡的夾具會先走一次召喚——直接 INSERT 一列的話，測試就繞過了真正的
前置條件，而那條路徑壞掉時這裡不會知道。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import STATUS_COMPLETED, STATUS_IN_PROGRESS, quest_id_for_spirit
from app.modules.body.resonance import SOURCE_QUEST, apply_resonance
from app.modules.body.sense_tokens import issue_sense_token

_LAT, _LON = 25.0955, 121.5186


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


@pytest.fixture
def summoned(client, spirit, player):
    """
    先召喚一次，讓 quest_progress 有一列。

    直接 INSERT 會繞過真正的建立路徑——那條路徑壞掉時這裡就看不出來。
    """
    pid, sess = player
    client.post(
        "/api/v1/summon",
        json={"spirit_id": spirit.spirit_id, "latitude": _LAT, "longitude": _LON},
        headers={"Authorization": f"Bearer {sess}"},
    )
    return pid, sess


def _complete(client, quest_id, session_token=None, encounter_token=None, sense_token=None):
    headers = {}
    if session_token:
        headers["Authorization"] = f"Bearer {session_token}"
    if encounter_token:
        headers[ENCOUNTER_TOKEN_HEADER] = encounter_token
    if sense_token:
        headers[ENCOUNTER_TOKEN_HEADER] = sense_token
    return client.post(
        f"/api/v1/quests/{quest_id}/complete", json={"completion_evidence": {}}, headers=headers
    )


def _progress(db_session, player_id, quest_id):
    db_session.expire_all()
    return (
        db_session.query(models.QuestProgress)
        .filter_by(player_id=player_id, quest_id=quest_id)
        .one()
    )


def _resonance_value(db_session, player_id, spirit_id) -> int:
    db_session.expire_all()
    row = (
        db_session.query(models.Resonance)
        .filter_by(player_id=player_id, spirit_id=spirit_id)
        .first()
    )
    return row.resonance_value if row else 0


# ── 憑證把關 ───────────────────────────────────────────────────────────

def test_missing_session_token_returns_401(client, spirit, summoned):
    pid, _ = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client, quest_id, encounter_token=issue_encounter_token(pid, spirit.spirit_id)
    )

    assert response.status_code == 401


def test_missing_encounter_token_returns_401(client, spirit, summoned):
    _, sess = summoned

    response = _complete(client, quest_id_for_spirit(spirit.spirit_id), session_token=sess)

    assert response.status_code == 401


def test_no_tokens_returns_401(client, spirit, summoned):
    assert _complete(client, quest_id_for_spirit(spirit.spirit_id)).status_code == 401


def test_encounter_token_for_another_spirit_returns_403(client, spirit, summoned, db_session):
    """
    AC：403，且任務狀態**未改變**、共鳴值**未增加**。

    只驗狀態碼是不夠的——一個先寫入再檢查憑證的實作也會回 403，但傷害已經造成。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client,
        quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(pid, "some-other-spirit"),
    )

    assert response.status_code == 403
    assert _progress(db_session, pid, quest_id).status == STATUS_IN_PROGRESS
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 0


def test_tokens_from_different_players_are_rejected(client, spirit, summoned, db_session):
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client,
        quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(uuid.uuid4(), spirit.spirit_id),
    )

    assert response.status_code == 403
    assert _progress(db_session, pid, quest_id).status == STATUS_IN_PROGRESS


def test_sense_token_cannot_complete_a_quest(client, spirit, summoned, db_session):
    """
    🔒 AC：持 Sense Token 者不可呼叫此端點（SDD §6）。

    感應憑證代表「你在 150m 內」，任務完成需要「你真的到了現場」。兩者用不同
    金鑰簽章，所以塞進 `X-Encounter-Token` 會在**驗章**就失敗——這不是靠我們
    記得檢查 purpose，是兩套憑證本來就換不過來。

    AC 指定要做 mutation 驗證的那一條。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client,
        quest_id,
        session_token=sess,
        sense_token=issue_sense_token(pid, spirit.spirit_id),
    )

    assert response.status_code in (401, 403)
    assert _progress(db_session, pid, quest_id).status == STATUS_IN_PROGRESS
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 0


# ── 完成流程 ───────────────────────────────────────────────────────────

def test_completion_sets_status_and_timestamp(client, spirit, summoned, db_session):
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client,
        quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 200
    progress = _progress(db_session, pid, quest_id)
    assert progress.status == STATUS_COMPLETED
    assert progress.completed_at is not None


def test_completion_awards_twenty_resonance(client, spirit, summoned, db_session):
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 20
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 20


def test_crossing_the_first_threshold_reports_stage_one(client, spirit, summoned):
    """AC (a)：共鳴值 0 → 20，跨過 10，回應標示新達成 stage 1。"""
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 20
    assert body["stage"] == 1
    assert body["newly_unlocked_stages"] == [1]


def test_crossing_the_second_threshold_reports_stage_two(client, spirit, summoned, db_session):
    """AC (b)：共鳴值 30 → 50，跨過 40，回應標示新達成 stage 2。"""
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-30",
        amount=30,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 50
    assert body["stage"] == 2
    assert body["newly_unlocked_stages"] == [2]


def test_not_crossing_a_threshold_reports_no_new_stage(client, spirit, summoned, db_session):
    """AC：共鳴值 50 → 70，兩者都在 stage 2，回應標示**沒有**新 stage。"""
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-50",
        amount=50,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 70
    assert body["stage"] == 2
    assert body["newly_unlocked_stages"] == []


# ── 重複提交 ───────────────────────────────────────────────────────────

def test_submitting_twice_does_not_award_twice(client, spirit, summoned, db_session):
    """
    🔒 AC：第二次仍回 200（不是 500）、共鳴值維持 20、帳本只有 1 列。

    重複提交是正常的使用者行為（網路重試、連點兩下），不是錯誤。去重靠 S5 的
    `UNIQUE(player_id, source_type, source_id)`，**不是先查再寫**——先查再寫在
    並行下會讓兩個請求同時查到「沒有」然後都寫進去。

    AC 指定要做 mutation 驗證的那一條。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    first = _complete(client, quest_id, session_token=sess, encounter_token=enc)
    second = _complete(client, quest_id, session_token=sess, encounter_token=enc)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["resonance_value"] == 20
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 20

    db_session.expire_all()
    ledger_rows = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=pid, source_type=SOURCE_QUEST, source_id=quest_id)
        .count()
    )
    assert ledger_rows == 1


def test_second_submission_reports_no_new_unlock(client, spirit, summoned):
    """第二次沒有加值，自然也沒有新解鎖——不該讓客戶端再播一次解鎖動畫。"""
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    _complete(client, quest_id, session_token=sess, encounter_token=enc)
    body = _complete(client, quest_id, session_token=sess, encounter_token=enc).json()

    assert body["newly_unlocked_stages"] == []


# ── 不呼叫腦袋 ─────────────────────────────────────────────────────────

def test_judgement_never_depends_on_the_llm(client, spirit, summoned, monkeypatch, db_session):
    """
    🔒 CONTEXT.md：「可驗證微任務由**後端確定性規則**判定，不由 LLM 判定。」

    ⚠️ **這條測試在 #43 之後改寫過。** 原本斷言「腦袋一次都沒被呼叫」，那在 #34
    的範圍內是對的；#43 把解鎖敘事接上來之後，腦袋**會**被呼叫——但只在所有
    資料庫寫入完成之後，而且只為了產生包裝文字。

    判定本身仍然完全不依賴 LLM，而證明它的方式是：讓腦袋整個爆炸，任務照樣
    完成、共鳴值照樣入帳。如果判定用到了 LLM，這裡會拿不到 200。
    """
    from app.modules.brain import gemini

    def _explode(*args, **kwargs):
        raise RuntimeError("模型整個掛了")

    monkeypatch.setattr(gemini.FakeGeminiClient, "generate", _explode)

    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    response = _complete(
        client,
        quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 200
    assert _progress(db_session, pid, quest_id).status == STATUS_COMPLETED
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 20


# ── #43：敘事欄位 ─────────────────────────────────────────────────────

def test_narrative_fields_are_populated_when_a_threshold_is_crossed(client, spirit, summoned):
    """
    ⚠️ **這條測試在 #43 之後改寫過。** 原本斷言 `unlock_story` 與
    `quest_wrapper_text` 一律為 null，那是 #34 刻意的範圍切割（§7.5 允許
    `unlock_story` 為 null，所以那是合法的完整回應，不是半成品）。

    #43 把 B11 與任務包裝接上來之後，跨門檻時它們就該有值了。
    """
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["newly_unlocked_stages"] == [1]
    assert body["unlock_story"] is not None
    assert body["unlock_story"]["stage"] == 1
    assert body["unlock_story"]["story_text"]
    assert body["quest_wrapper_text"]


# ── quest_id 格式 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_quest_id", ["nonsense", "no-colon-here", ":daily", "spirit:weekly"])
def test_malformed_quest_id_returns_404_not_500(client, player, bad_quest_id):
    """
    亂打的 quest_id 是可預期的輸入，不是伺服器故障。

    少了這條，`rpartition` 的結果會一路帶著空字串往下走，最後在某個意想不到的
    地方炸成 500。
    """
    pid, sess = player

    response = _complete(
        client,
        bad_quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(pid, "whatever"),
    )

    assert response.status_code == 404


def test_quest_without_progress_returns_404(client, spirit, player):
    """
    沒召喚過就直接完成 → 404。任務進度是在 /summon 時建立的。
    """
    pid, sess = player

    response = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    )

    assert response.status_code == 404


def test_duplicate_is_rejected_by_the_database_not_by_application_code(spirit, player):
    """
    🔒 去重的**執行點是資料庫**，不是程式碼裡的判斷。

    ⚠️ **關於 AC 指定的那個 mutation，要說清楚一件事。**

    AC 要求「把去重改成先查再寫，重複提交測試須紅」。我實際做了那個 mutation，
    上面那條循序的重複提交測試**照樣全綠**——因為循序執行時先查再寫本來就是
    對的。我接著寫了一條雙執行緒的測試（含 `threading.Barrier`），它**仍然
    無法穩定重現競爭**：barrier 只同步起點，兩條執行緒的 DB 往返還是被排程
    序列化了。與其留一條會偶發通過的測試假裝有保護，不如直接驗證真正在做事
    的那個東西。

    所以這條測的是：同一組 `(player_id, source_type, source_id)` 寫第二次，
    **資料庫自己會拒絕**。這正是 `resonance.py` 說的「唯一擋得住的是 UNIQUE
    約束」——它成立的話，就算應用層的判斷寫錯，超賣也寫不進去。約束被拿掉時
    這條會紅。

    真正的並行行為在 #32 的配額那邊有一條可靠的測試（Redis Lua 是單一原子
    操作，兩條執行緒必定其中一個失敗），那裡的機制不一樣，測得起來。
    """
    import uuid as uuid_module

    from sqlalchemy.exc import IntegrityError

    from app.core.database import SessionLocal

    player_id, _ = player
    source_id = f"dup-test-{uuid_module.uuid4().hex[:8]}"

    session = SessionLocal()
    try:
        session.add(
            models.ResonanceEvent(
                player_id=player_id,
                spirit_id=spirit.spirit_id,
                source_type=SOURCE_QUEST,
                source_id=source_id,
                amount=20,
            )
        )
        session.commit()

        session.add(
            models.ResonanceEvent(
                player_id=player_id,
                spirit_id=spirit.spirit_id,
                source_type=SOURCE_QUEST,
                source_id=source_id,
                amount=20,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    finally:
        session.rollback()
        session.query(models.ResonanceEvent).filter_by(player_id=player_id).delete()
        session.commit()
        session.close()
