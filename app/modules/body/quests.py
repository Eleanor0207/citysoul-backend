"""
S4．可驗證微任務生命週期狀態機（SDD 第7.4節）。

    不存在 → in_progress → completed
                  ↓（憑證過期且任務未完成）
               失敗嘗試（attempts_today += 1）
                  ↓（attempts_today < 3）
               回到 in_progress，可重新召喚再挑戰
                  ↓（attempts_today >= 3）
               當天鎖定，等隔天（Asia/Taipei 午夜）reset

## 「失敗」是被動判定的

沒有「回報失敗」API。玩家拿了相遇憑證卻沒完成任務就直接關掉 App，後端當下
不會知道；要等他**下一次召喚同一個靈魂**時，才回頭看「上一張憑證是不是已經
過期而任務還停在 in_progress」，是的話才記一次失敗。

這代表失敗次數只在玩家自己回來時才會前進，永遠不會有背景 job 去掃。這是
SDD 第7.4節刻意的選擇（「以後端確定性規則驗證完成與否」），不是偷懶。

## 每日重置也是被動的

`attempts_date` 存的是 Asia/Taipei 的日期。查詢當下比對它跟「今天」是否相同，
不同就把 `attempts_today` 歸零。同樣不需要排程 job 在午夜清表——沒有人召喚
的時候，那個玩家的次數是幾就是幾，沒有任何人會觀察到差別。
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.modules.body.encounter_tokens import ENCOUNTER_TOKEN_EXPIRE_SECONDS
from app.modules.body.models import QuestProgress

# SDD 第7節決策8：所有「每日一次」「隔天重置」統一以 Asia/Taipei 午夜為基準。
TAIPEI = ZoneInfo("Asia/Taipei")

# SDD 第7節決策6：當天最多失敗重試 3 次。
MAX_DAILY_ATTEMPTS = 3

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
# 只出現在 API 回應，不會寫進資料庫（見 QuestProgress 的 docstring）。
STATUS_DAILY_LIMIT_REACHED = "daily_limit_reached"


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


# quest_id_for_spirit() 的反函式（issue #33）。quest_progress 沒有 spirit_id
# 欄位，GET /quests/daily 的回應卻需要它——目前唯一的來源就是這個命名慣例。
# 之後導入任務目錄表時，這支函式要換成查表，呼叫端（router.py）不用跟著改。
_QUEST_ID_SUFFIX = ":daily"


def spirit_id_for_quest(quest_id: str) -> str:
    if not quest_id.endswith(_QUEST_ID_SUFFIX):
        raise ValueError(f"無法從 quest_id 反推 spirit_id（格式不符）：{quest_id!r}")
    return quest_id[: -len(_QUEST_ID_SUFFIX)]


def taipei_today(now: datetime) -> date:
    """把一個帶時區的時間點換算成 Asia/Taipei 的日期。"""
    return now.astimezone(TAIPEI).date()


class QuestState:
    """
    回傳給呼叫端的任務狀態快照。

    刻意不是 ORM 物件：`status` 可能是 `daily_limit_reached`，那個值在資料庫裡
    並不存在，硬塞回 ORM 物件會讓人以為它會被存起來。
    """

    def __init__(self, quest_id: str, status: str, attempts_today: int):
        self.quest_id = quest_id
        self.status = status
        self.attempts_today = attempts_today


def effective_state(progress: QuestProgress, *, now: datetime | None = None) -> QuestState:
    """
    唯讀版本的狀態計算（issue #33／#36 的查詢端點用這個，不是 `evaluate_on_summon`）。

    `evaluate_on_summon` 只該在 `/summon` 被呼叫一次——它有副作用（可能把
    `attempts_today` 歸零、可能記一次失敗嘗試，兩者都會 commit）。查詢端點
    只是「玩家現在看到的狀態是什麼」，不該因為被查詢就順便改寫資料庫，
    尤其是「今天已跨到新的一天」這件事：查詢的當下重算就好，不需要也不該
    寫回 DB——沒有人召喚的時候，這一列本來就不會有人在乎它現在該不該重置。
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)

    # 跨日：顯示 0，不寫回 attempts_date／attempts_today。
    attempts_today = 0 if progress.attempts_date != today else progress.attempts_today

    if attempts_today >= MAX_DAILY_ATTEMPTS:
        status = STATUS_DAILY_LIMIT_REACHED
    else:
        status = progress.status

    return QuestState(progress.quest_id, status, attempts_today)


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
    - 上一張憑證過期而任務未完成時，`attempts_today += 1`
    - 還有次數可用時，開始／延續一次嘗試（寫入 `current_token_issued_at`）
    """
    now = now or datetime.now(timezone.utc)
    today = taipei_today(now)
    quest_id = quest_id_for_spirit(spirit_id)

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

    if progress.attempts_today >= MAX_DAILY_ATTEMPTS:
        # 當天鎖定。仍然不阻擋召喚本身——呼叫端還是會核發 encounter_token，
        # 玩家可以繼續對話，只是今天不能再挑戰任務（AC：在場驗證仍可通過）。
        db.commit()
        return QuestState(quest_id, STATUS_DAILY_LIMIT_REACHED, progress.attempts_today)

    if progress.status != STATUS_COMPLETED:
        # 開始（或重新開始）一次嘗試：記下這張憑證的核發時間，下一次召喚就是
        # 靠它判斷這次嘗試有沒有逾時。
        progress.status = STATUS_IN_PROGRESS
        progress.current_token_issued_at = now

    db.commit()
    return QuestState(quest_id, progress.status, progress.attempts_today)


def _reset_attempts_if_new_day(progress: QuestProgress, *, today: date) -> None:
    if progress.attempts_date != today:
        progress.attempts_today = 0
        progress.attempts_date = today


def _count_failed_attempt_if_token_expired(progress: QuestProgress, *, now: datetime) -> None:
    """
    上一張相遇憑證過期、任務卻還停在 in_progress → 記一次失敗。

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
        progress.attempts_today += 1
        progress.current_token_issued_at = None
