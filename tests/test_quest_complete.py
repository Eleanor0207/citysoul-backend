"""
#34．`POST /quests/{questId}/complete` 任務完成（SDD §7.5 前半）。

🔴 **2026-08-19：這支端點不再入帳共鳴值。** 共鳴值的來源改為每日召喚 +10 與
劇本節點 +30，每日任務這個玩法不做了。端點與狀態機留著，因為劇本任務會用
同一套（`quests.quest_type` 本來就有 `'story'`）。

⚠️ 因此**基準共鳴值是 10 而不是 0**：`summoned` 夾具會走一次 `/summon`，
而召喚現在就給 +10。看到 10 不要以為是任務給的。

對真實 Postgres 跑。任務進度由 `/summon` 建立（那是它唯一的建立路徑），
所以這裡的夾具會先走一次召喚——直接 INSERT 一列的話，測試就繞過了真正的
前置條件，而那條路徑壞掉時這裡不會知道。
"""
import uuid

import pytest

from app.modules.body import models
from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_HEADER, issue_encounter_token
from app.modules.body.quests import STATUS_COMPLETED, STATUS_IN_PROGRESS, quest_id_for_spirit
from app.modules.body.resonance import SOURCE_DAILY_ENCOUNTER, apply_resonance
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

    ⚠️ 2026-08-19 起這一次召喚**順帶入帳 +10**（每日共鳴）。所以用到這個夾具的
    測試，共鳴值的起點是 10，不是 0。
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
    # ⚠️ 10 而不是 0：`summoned` 夾具那次召喚已經給了每日共鳴（2026-08-19 起）。
    # 這條守的是「被擋下的請求沒有**額外**加值」，不是「共鳴值為零」。
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


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
    # ⚠️ 10 而不是 0：`summoned` 夾具那次召喚已經給了每日共鳴（2026-08-19 起）。
    # 這條守的是「被擋下的請求沒有**額外**加值」，不是「共鳴值為零」。
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


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


def test_completion_awards_no_resonance(client, spirit, summoned, db_session):
    """
    🔴 2026-08-19：任務完成**不再加共鳴值**。

    共鳴值仍然是 10——那是 `summoned` 夾具那次召喚給的，不是任務給的。
    這條紅了（變成 30 或 40）代表有人把入帳接了回去。
    """
    pid, sess = summoned
    before = _resonance_value(db_session, pid, spirit.spirit_id)
    assert before == 10, "夾具的召喚應該已經給了 +10"

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 10
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10


def test_completion_writes_no_ledger_row(client, spirit, summoned, db_session):
    """
    🔒 帳本裡不該出現任何 source_type='quest' 的列。

    只看 `resonance_value` 沒變是不夠的——一筆 amount=0 的入帳同樣不會改變
    數值，但它會留下一列，而且代表入帳路徑其實還接著。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)

    _complete(
        client,
        quest_id,
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    )

    db_session.expire_all()
    assert (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=pid, source_type="quest")
        .count()
        == 0
    )


def test_completion_never_crosses_a_threshold(client, spirit, summoned):
    """
    🔴 這條路徑不再跨門檻，所以 `newly_unlocked_stages` 恆為空。

    ⚠️ 這**不是**「還沒接」。跨門檻現在只發生在 `/summon`（那裡才有入帳），
    解鎖敘事也跟著搬過去了。看到空陣列不要以為壞了。

    共鳴值 10 是夾具那次召喚給的，stage 1 也是那時候跨的——不是這一次呼叫。
    """
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 10
    assert body["stage"] == 1
    assert body["newly_unlocked_stages"] == []
    assert body["unlock_story"] is None
    assert body["unlock_stories"] == []


def test_completion_reports_current_resonance_not_zero(client, spirit, summoned, db_session):
    """
    回應裡的 `resonance_value` 是**當下的真實值**（唯讀查詢），不是入帳結果。

    先塞到 55 再完成任務，回應該照實回 55——回 0 代表有人把欄位寫死了，
    回 75 代表入帳被接了回去。
    """
    pid, sess = summoned
    apply_resonance(
        db_session,
        player_id=pid,
        spirit_id=spirit.spirit_id,
        source_type="test_seed",
        source_id="seed-45",
        amount=45,
    )

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["resonance_value"] == 55
    assert body["stage"] == 2


# ── 重複提交 ───────────────────────────────────────────────────────────

def test_submitting_twice_does_not_award_twice(client, spirit, summoned, db_session):
    """
    🔒 第二次仍回 200（不是 500），狀態與共鳴值都沒有被動到。

    重複提交是正常的使用者行為（網路重試、連點兩下），不是錯誤。

    ⚠️ 2026-08-19 後這條測的是**狀態機的冪等**，不再是共鳴去重——這條路徑
    根本不入帳了。共鳴值那邊的去重仍然由 `UNIQUE(player_id, source_type,
    source_id)` 保證，證據在 `test_resonance.py` 與 `test_summon.py`。
    """
    pid, sess = summoned
    quest_id = quest_id_for_spirit(spirit.spirit_id)
    enc = issue_encounter_token(pid, spirit.spirit_id)

    first = _complete(client, quest_id, session_token=sess, encounter_token=enc)
    second = _complete(client, quest_id, session_token=sess, encounter_token=enc)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["resonance_value"] == 10  # 夾具召喚給的，兩次都沒動它
    assert _resonance_value(db_session, pid, spirit.spirit_id) == 10
    assert _progress(db_session, pid, quest_id).status == STATUS_COMPLETED

    # 帳本裡只有召喚那一列，任務沒有留下任何東西。
    db_session.expire_all()
    rows = (
        db_session.query(models.ResonanceEvent)
        .filter_by(player_id=pid, spirit_id=spirit.spirit_id)
        .all()
    )
    assert [r.source_type for r in rows] == [SOURCE_DAILY_ENCOUNTER]


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
    完成。如果判定用到了 LLM，這裡會拿不到 200。
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


# ── #43：敘事欄位 ─────────────────────────────────────────────────────

def test_wrapper_text_is_populated(client, spirit, summoned):
    """
    #43 的任務包裝台詞仍然會生成——那一段跟共鳴值無關，是「你完成了一件事」
    的語氣包裝。

    ⚠️ `unlock_story` 則恆為 null（見 `test_completion_never_crosses_a_threshold`）。
    """
    pid, sess = summoned

    body = _complete(
        client,
        quest_id_for_spirit(spirit.spirit_id),
        session_token=sess,
        encounter_token=issue_encounter_token(pid, spirit.spirit_id),
    ).json()

    assert body["quest_wrapper_text"]
    assert body["unlock_story"] is None


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
                source_type=SOURCE_DAILY_ENCOUNTER,
                source_id=source_id,
                amount=10,
            )
        )
        session.commit()

        session.add(
            models.ResonanceEvent(
                player_id=player_id,
                spirit_id=spirit.spirit_id,
                source_type=SOURCE_DAILY_ENCOUNTER,
                source_id=source_id,
                amount=10,
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
