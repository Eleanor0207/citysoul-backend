"""
S4．可驗證微任務生命週期狀態機（SDD 第7.4節）。

    不存在 → in_progress → completed
                  ↓（憑證過期且任務未完成）
               清除當次憑證，可重新召喚再挑戰

## 「失敗」是被動判定的

沒有「回報失敗」API。玩家拿了相遇憑證卻沒完成任務就直接關掉 App，後端當下
不會知道；要等他**下一次召喚同一個靈魂**時，才回頭看「上一張憑證是不是已經
過期而任務還停在 in_progress」，是的話只清除舊憑證，不增加失敗次數。

`attempts_today` / `attempts_date` 保留作為觀測資料，永遠不會有背景 job 去掃。
這是 SDD 第7.4節刻意的選擇（「以後端確定性規則驗證完成與否」）。

## 每日重置也是被動的

`attempts_date` 存的是 Asia/Taipei 的日期。查詢當下比對它跟「今天」是否相同，
不同就把 `attempts_today` 歸零。同樣不需要排程 job 在午夜清表。
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_EXPIRE_SECONDS
from app.modules.body.models import PlayerQuestStep, Quest, QuestProgress

# SDD 第7節決策8：所有「每日一次」「隔天重置」統一以 Asia/Taipei 午夜為基準。
TAIPEI = ZoneInfo("Asia/Taipei")

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"


def quest_id_for_spirit(spirit_id: str) -> str:
    """
    地標 → 任務 id 的對應。

    ⚠️ 這是垂直切片階段的簡化。SDD 沒有定義任務目錄表，而 `quest_progress`
    的主鍵是 `(player_id, quest_id)`、沒有 `spirit_id` 欄位，所以必須有個方式
    從 spirit_id 推出 quest_id。這裡採「每個靈魂一個固定任務」，對應封閉測試
    只有天文館一隻靈魂的現況。

    之所以不把日期編進 quest_id，是因為表裡已經有 `attempts_date` 了——如果
    quest_id 自己帶日期，每天都是新的一列，那個欄位就完全沒有存在的必要。
    欄位的存在本身就說明了 quest_id 應該是跨日穩定的。

    等真的有多個任務、或任務內容需要編輯時，這裡要換成查任務目錄表。
    """
    return f"{spirit_id}:daily"


def taipei_today(now: datetime) -> date:
    """把一個帶時區的時間點換算成 Asia/Taipei 的日期。"""
    return now.astimezone(TAIPEI).date()


class QuestState:
    """
    回傳給呼叫端的任務狀態快照。

    刻意不是 ORM 物件：它是給 API 回應用的狀態快照，不直接暴露 ORM 物件。
    """

    def __init__(self, quest_id: str, status: str, attempts_today: int):
        self.quest_id = quest_id
        self.status = status
        self.attempts_today = attempts_today


def evaluate_on_summon(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    now: datetime | None = None,
) -> QuestState:
    """
    在 `/summon` 在場驗證通過後呼叫，推進任務狀態機並回傳這次要給前端的狀態。

    副作用（都會 commit）：
    - 跨日時把 `attempts_today` 歸零
    - 上一張憑證過期而任務未完成時，清除 `current_token_issued_at`
    - 任務未完成時，開始／延續一次嘗試（寫入 `current_token_issued_at`）
    - **開啟這個地標的 story 型任務**（見 `open_story_quests`）

    ## 回傳的仍然是當日任務

    story 型任務不進回傳值：`/summon` 的回應形狀是「這次召喚的當日任務怎麼了」，
    多塞一個清單進去會讓兩種任務在同一個欄位裡混著。玩家要看 story 任務走
    `GET /quests/daily`，那支本來就回傳玩家的全部任務。
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)
    quest_id = quest_id_for_spirit(spirit_id)

    open_story_quests(db, player_id=player_id, spirit_id=spirit_id, today=today)

    progress = (
        db.query(QuestProgress).filter_by(player_id=uuid.UUID(str(player_id)), quest_id=quest_id).first()
    )

    if progress is None:
        progress = QuestProgress(
            player_id=uuid.UUID(str(player_id)),
            quest_id=quest_id,
            status=STATUS_IN_PROGRESS,
            attempts_today=0,
            attempts_date=today,
        )
        db.add(progress)

    _reset_attempts_if_new_day(progress, today=today)
    _count_failed_attempt_if_token_expired(progress, now=now)

    if progress.status != STATUS_COMPLETED:
        # 開始（或重新開始）一次嘗試：記下這張憑證的核發時間，下一次召喚就是
        # 靠它判斷這次嘗試有沒有逾時。
        progress.status = STATUS_IN_PROGRESS
        progress.current_token_issued_at = now

    db.commit()
    return QuestState(quest_id, progress.status, progress.attempts_today)


def open_story_quests(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    today: date,
) -> list[str]:
    """替這個地標的 story 型任務開進度列，回傳這次新開的任務 id。

    ## 為什麼在召喚時開，而不是在玩家建立時全部開

    任務要玩家**到過現場**才成立（CONTEXT.md 的「在場」）。一次把九個地標的
    story 任務都塞進任務列表，玩家打開就看到一整頁還沒去過的地方——那是待辦
    清單，不是遊戲。

    ## `issued_date` 是 NULL

    story 任務不是每日重置的：它一輩子只完成一次。`quest_progress` 的兩個
    partial unique index 就是照這個分的——`issued_date IS NULL` 的一次性任務
    唯一於 `(player_id, quest_id)`（0003）。重複召喚因此不會長出第二列。

    ## 已完成的任務不會被重開

    `on_conflict_do_nothing` 讓這支函式對「玩家已經做完了」是無感的——它只負責
    「還沒有進度列的話開一列」，不碰既有的狀態。
    """
    rows = (
        db.query(Quest.quest_id)
        .filter(
            Quest.spirit_id == spirit_id,
            Quest.quest_type == "story",
            Quest.is_active.is_(True),
        )
        .order_by(Quest.quest_id)
        .all()
    )
    if not rows:
        return []

    opened: list[str] = []
    for (quest_id,) in rows:
        statement = (
            pg_insert(QuestProgress)
            .values(
                player_id=uuid.UUID(str(player_id)),
                quest_id=quest_id,
                issued_date=None,
                status=STATUS_IN_PROGRESS,
                progress_value=0,
                attempts_today=0,
                attempts_date=today,
            )
            # ⚠️ `uq_quest_progress_onetime` 是**部分索引**（`WHERE issued_date
            # IS NULL`），所以 ON CONFLICT 必須帶同一個條件才推論得到它——少了
            # `index_where` 會是 InvalidColumnReference，不是安靜地不去重。
            .on_conflict_do_nothing(
                index_elements=["player_id", "quest_id"],
                index_where=text("issued_date IS NULL"),
            )
            .returning(QuestProgress.progress_id)
        )
        if db.execute(statement).scalar_one_or_none() is not None:
            opened.append(quest_id)

    if opened:
        db.commit()

    return opened


def _reset_attempts_if_new_day(progress: QuestProgress, *, today: date) -> None:
    if progress.attempts_date != today:
        progress.attempts_today = 0
        progress.attempts_date = today


def _count_failed_attempt_if_token_expired(progress: QuestProgress, *, now: datetime) -> None:
    """
    上一張相遇憑證過期、任務卻還停在 in_progress → 清除當次憑證。

    判定用的是憑證的效期本身（900 秒），不是另外再加 15 分鐘寬限。SDD 第7.4節
    寫的「encounter_token 已過期超過15分鐘」，指的就是「這張 15 分鐘的憑證已經
    過了它的 15 分鐘」。
    """
    if progress.status != STATUS_IN_PROGRESS:
        return
    if progress.current_token_issued_at is None:
        return

    issued_at = progress.current_token_issued_at
    if issued_at.tzinfo is None:
        issued_at = issued_at.replace(tzinfo=timezone.utc)

    if now - issued_at > timedelta(seconds=ENCOUNTER_TOKEN_EXPIRE_SECONDS):
        progress.current_token_issued_at = None


class QuestNotFoundError(LookupError):
    """quest_id 對不到任何任務（格式不符，或該玩家沒有這筆進度）。"""


class QuestNotCompletableError(RuntimeError):
    """任務目前的狀態不允許完成。"""


def spirit_id_for_quest(quest_id: str) -> str:
    """
    任務 id → 地標 id，`quest_id_for_spirit()` 的反向。

    格式不符時拋 `QuestNotFoundError` 而不是讓 `ValueError`／`IndexError` 往外
    竄——呼叫端拿到的該是「查無此任務」（404），不是 500。玩家送一個亂打的
    quest_id 是可預期的輸入，不是伺服器故障。
    """
    if not quest_id or ":" not in quest_id:
        raise QuestNotFoundError(f"無法解析的 quest_id：{quest_id!r}")

    spirit_id, _, suffix = quest_id.rpartition(":")
    if not spirit_id or suffix != "daily":
        raise QuestNotFoundError(f"無法解析的 quest_id：{quest_id!r}")

    return spirit_id


def resolve_spirit_id(db: Session, quest_id: str) -> str:
    """任務屬於哪個地標。先查目錄表，查不到才回頭用命名慣例。

    ⚠️ 兩種 quest_id 的來源不同，**只認其中一種會 404 掉另一種**：

    - `{spirit_id}:daily` —— `quest_id_for_spirit()` 推導的，不進目錄表
    - `q_longshan_repair_trace` —— 目錄表裡人工撰寫的 story 任務

    `spirit_id_for_quest()` 只認得前者。story 任務的 id 不含 `:daily`，所以
    在目錄表有資料之後，任何只用命名慣例的呼叫端都會把它當成「查無此任務」。
    """
    row = (
        db.query(Quest.spirit_id)
        .filter(Quest.quest_id == quest_id, Quest.is_active.is_(True))
        .first()
    )
    if row is not None and row[0]:
        return row[0]
    return spirit_id_for_quest(quest_id)


def complete_on_dialogue(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    now: datetime | None = None,
) -> bool:
    """
    「當天與這隻靈魂有任一輪對話」＝當日任務完成（SDD §20.3.4／backend#71）。

    回傳這次是否真的把它從進行中改成完成；已完成、或玩家還沒召喚過（沒有進度列）
    時回 False。

    ## 判定不看對話內容

    要判斷「玩家有沒有真的問到那件事」需要 LLM，而 CONTEXT.md 明訂「可驗證微任務
    由後端確定性規則判定，**不由 LLM 判定**」。判定一旦進了模型，玩家就無法預期
    怎樣算完成——那比判定得不夠精確糟得多。

    ## 沒有進度列不是錯誤

    任務是在 `/summon` 時建立的（`evaluate_on_summon`）。玩家在 150m 外隔空聊天、
    還沒走進 50m 召喚時，就是這個狀況：他確實在對話，但還沒有任務可以完成。
    **兩段式維持不變**（SDD §20.3.4），所以這裡安靜地什麼都不做。

    ## 跟共鳴值同一個觸發點

    §18.5 的「每日對話 +10」也接在同一個地方。兩者分開寫入的話，同一輪對話會走
    兩條「完成／入帳」邏輯，去重規則要維護兩份——呼叫端因此把兩件事排在一起。
    """
    moment = now or datetime.now(timezone.utc)

    progress = (
        db.query(QuestProgress)
        .filter_by(
            player_id=uuid.UUID(str(player_id)),
            quest_id=quest_id_for_spirit(spirit_id),
        )
        .first()
    )

    if progress is None or progress.status == STATUS_COMPLETED:
        return False

    progress.status = STATUS_COMPLETED
    progress.completed_at = moment
    db.commit()
    return True


def complete_quest(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    quest_id: str,
    now: datetime | None = None,
) -> QuestProgress:
    """
    把任務標記為完成（SDD §7.5 前半）。

    ## 判定是確定性規則，不是 LLM

    CONTEXT.md 明訂「可驗證微任務由後端確定性規則判定，不由 LLM 判定」。
    這支函式因此**完全不碰腦袋模組**——判定只看資料庫裡的狀態。

    MVP 的規則很小：任務進度必須存在且不是已完成。`completion_evidence` 目前
    不參與判定（SDD 尚未定義它的結構），但仍然收下來，因為之後加規則時
    API 形狀不該跟著變。

    ## 已完成的任務再次呼叫不是錯誤

    直接回傳現況。重複提交是正常的使用者行為（網路重試、連點兩下），共鳴值的
    去重由 `resonance_events` 的 UNIQUE 約束保證，不需要在這裡擋。
    """
    moment = now or datetime.now(timezone.utc)

    progress = (
        db.query(QuestProgress)
        .filter_by(player_id=uuid.UUID(str(player_id)), quest_id=quest_id)
        .first()
    )

    if progress is None:
        # 沒有進度列代表玩家還沒召喚過這個靈魂——任務是在 /summon 時建立的。
        raise QuestNotFoundError(f"玩家沒有 {quest_id} 的任務進度")

    if progress.status == STATUS_COMPLETED:
        return progress

    progress.status = STATUS_COMPLETED
    progress.completed_at = moment
    db.commit()
    db.refresh(progress)

    return progress


class StepNotFoundError(LookupError):
    """這個任務的 `steps` 裡沒有這個 step_id。"""


def quest_steps(db: Session, quest_id: str) -> list[dict]:
    """任務目錄裡定義的步驟。daily 型任務沒有目錄資料，回空陣列。"""
    quest = db.query(Quest).filter_by(quest_id=quest_id).first()
    if quest is None:
        return []
    return [s for s in (quest.steps or []) if isinstance(s, dict)]


def completed_step_ids(db: Session, *, player_id: uuid.UUID | str, quest_id: str) -> set[str]:
    """這個玩家在這個任務上做完的步驟。"""
    rows = (
        db.query(PlayerQuestStep.step_id)
        .filter_by(player_id=uuid.UUID(str(player_id)), quest_id=quest_id)
        .all()
    )
    return {row[0] for row in rows}


def pending_steps(db: Session, *, player_id: uuid.UUID | str, quest_id: str) -> list[dict]:
    """還沒做到的步驟，照目錄順序。全部做完時回空陣列。"""
    done = completed_step_ids(db, player_id=player_id, quest_id=quest_id)
    return [s for s in quest_steps(db, quest_id) if s.get("step_id") not in done]


@dataclass(frozen=True)
class StepCompletion:
    """`complete_step` 的結果。步驟集合一併回傳，呼叫端不必再查一次。"""

    newly_added: bool
    quest_completed: bool
    completed_step_ids: set[str]
    pending_steps: list[dict]


def complete_step(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    quest_id: str,
    step_id: str,
    now: datetime | None = None,
) -> StepCompletion:
    """
    記下玩家做到了某一步。回傳 `StepCompletion`。

    ## 判定是玩家自陳，不是 LLM

    CONTEXT.md 明訂「可驗證微任務由後端確定性規則判定，**不由 LLM 判定**」。
    這裡的規則就是「玩家說他看到了」——沒有自動驗證。

    那確實很弱，但另外兩條路現在都不成立：GPS 只能判斷「在附近」，分不出同一個
    地標上的三個步驟；照片辨識要為每一步準備辨識目標，而 `optional_photo_evidence`
    整個任務只有一個。**驗證是可以之後換掉的那一層，資料結構不用跟著變。**

    ## 全部做完就自動完成任務

    不讓玩家再按一次「完成任務」：有步驟又有完成鈕等於「做完了沒」有兩個定義。
    最後一步落地之後，同一次呼叫把任務標成完成。

    ## 重複提交不是錯誤

    步驟是集合語意，做過就做過。主鍵去重，`newly_added` 告訴呼叫端「這次有沒有
    真的新增」——共鳴值與包裝台詞只該在真的新增時發生。
    """
    moment = now or datetime.now(timezone.utc)
    player_uuid = uuid.UUID(str(player_id))

    catalogue = quest_steps(db, quest_id)
    defined = {s.get("step_id") for s in catalogue}
    if step_id not in defined:
        raise StepNotFoundError(f"{quest_id} 沒有步驟 {step_id}")

    progress = (
        db.query(QuestProgress)
        .filter_by(player_id=player_uuid, quest_id=quest_id)
        .first()
    )
    if progress is None:
        # 跟 complete_quest 同一個理由：任務是在 /summon 時建立的。
        raise QuestNotFoundError(f"玩家沒有 {quest_id} 的任務進度")

    statement = (
        pg_insert(PlayerQuestStep)
        .values(
            player_id=player_uuid,
            quest_id=quest_id,
            step_id=step_id,
            completed_at=moment,
        )
        .on_conflict_do_nothing(index_elements=["player_id", "quest_id", "step_id"])
        .returning(PlayerQuestStep.step_id)
    )
    newly_added = db.execute(statement).scalar_one_or_none() is not None

    # 🔒 步驟先落地，才判斷任務有沒有做完——順序顛倒的話，最後一步會在自己
    # 還沒寫進去的情況下被算成「還沒做完」。這一列已經在本交易裡，後面讀得到。
    done = completed_step_ids(db, player_id=player_uuid, quest_id=quest_id)
    quest_completed = (
        progress.status != STATUS_COMPLETED and defined.issubset(done)
    )
    if quest_completed:
        progress.status = STATUS_COMPLETED
        progress.completed_at = moment

    # 步驟與任務狀態同一次 commit：中間斷線不會留下「步驟全做完但任務仍在進行」。
    db.commit()

    return StepCompletion(
        newly_added=newly_added,
        quest_completed=quest_completed,
        completed_step_ids=done,
        pending_steps=[s for s in catalogue if s.get("step_id") not in done],
    )
