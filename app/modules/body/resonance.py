"""
S5．共鳴值入帳與門檻判定（SDD 第3.1／7.5節）。

刻意做成**獨立服務函式**，不綁任何 API 端點。呼叫方目前有三個：
`POST /quests/{questId}/complete`（任務完成）、
`POST /spirits/{placeId}/dialogue`（每日對話，backend#70）與
`story_progress.advance_beat()`（劇情結局，backend#72）。

## 防重複入帳靠資料庫，不靠先查後寫

「先查帳本有沒有這筆、沒有才寫」在並行下是錯的：兩個請求可以同時查到「沒有」，
然後都寫進去。唯一擋得住的是 `resonance_events` 的
`UNIQUE(player_id, source_type, source_id)`。

所以這裡的流程是**先寫帳本、撞到約束就當作重複**，而不是反過來。撞到時優雅
回傳現況（`awarded=False`），不讓例外炸到呼叫端——重複提交同一個任務完成是
正常的使用者行為（網路重試、連點兩下），不是錯誤。
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import Resonance, ResonanceConfig, ResonanceEvent

SOURCE_ENCOUNTER_COLLECTION = "encounter_collection"
SOURCE_QUEST = "quest"
# 每日對話（backend#70／#52 拍板）。source_id 帶靈魂 id 與 Asia/Taipei 日期
# （`dialogue_source_id()`），因為 resonance_events 的 UNIQUE 是全域的
# （不含 spirit_id）——只用日期當 source_id 會變成「今天跟任何一個靈魂聊過，
# 所有靈魂都入帳」。
SOURCE_DIALOGUE = "dialogue"
# 劇情結局（backend#72／#52 拍板）。同一個理由：source_id 帶 arc_id 與
# spirit_id（`story_completion_source_id()`），三個地標各自入帳一筆。
SOURCE_STORY_COMPLETION = "story_completion"

_REQUIRED_CONFIG_KEYS = (
    "threshold_stage_1",
    "threshold_stage_2",
    "threshold_stage_3",
    "amount_encounter_collection",
    "amount_quest",
    "amount_dialogue",
    "amount_story_completion",
)


@dataclass(frozen=True)
class ResonanceRules:
    """
    共鳴值門檻與各來源點數，從 `resonance_config` 表讀出來的快照。

    刻意是不快取的**每次查詢的快照**，不是行程內全域快取：這些值改了要立即
    生效（backend#73 的整個重點就是「改一筆資料不用重新部署」），跟配額
    `quota.py` 的 `limits_for_tier()` 同一個做法——不是熱路徑，不需要提前
    優化，硬要快取只會多一個「什麼時候該失效」的問題。
    """

    thresholds: tuple[int, int, int]
    amount_encounter_collection: int
    amount_quest: int
    amount_dialogue: int
    amount_story_completion: int


def load_resonance_rules(db: Session) -> ResonanceRules:
    """
    讀 `resonance_config`。缺任何一個必要 key 就直接拋錯——共鳴值規則缺一筆
    代表部署有問題（migration 沒跑到底、或有人手動砍了資料），不該悄悄退回
    一個誰都不知道的預設值繼續跑下去。
    """
    rows = db.query(ResonanceConfig).filter(
        ResonanceConfig.config_key.in_(_REQUIRED_CONFIG_KEYS)
    ).all()
    values = {row.config_key: row.value for row in rows}

    missing = [key for key in _REQUIRED_CONFIG_KEYS if key not in values]
    if missing:
        raise RuntimeError(f"resonance_config 缺少必要設定：{missing}")

    return ResonanceRules(
        thresholds=(
            values["threshold_stage_1"],
            values["threshold_stage_2"],
            values["threshold_stage_3"],
        ),
        amount_encounter_collection=values["amount_encounter_collection"],
        amount_quest=values["amount_quest"],
        amount_dialogue=values["amount_dialogue"],
        amount_story_completion=values["amount_story_completion"],
    )


def dialogue_source_id(spirit_id: str, taipei_date) -> str:
    """
    每日對話入帳用的 `source_id`（backend#70）。

    `resonance_events` 的 UNIQUE 不含 `spirit_id`（見 `ResonanceEvent` 的
    docstring），所以這裡自己把 `spirit_id` 編進去，只用日期會變成「今天
    只要跟任何一個靈魂聊過，所有靈魂都算入帳」。`taipei_date` 由呼叫端傳入
    （`quests.taipei_today()`），這支函式不自己決定「今天」是哪一天——
    日期換算的規則只該有一個地方。
    """
    return f"{spirit_id}:{taipei_date.isoformat()}"


def story_completion_source_id(arc_id: str, spirit_id: str) -> str:
    """
    劇情結局入帳用的 `source_id`（backend#72）。

    一條 arc 結局時會對三個地標各入帳一筆，同一個理由：`resonance_events`
    的 UNIQUE 不含 `spirit_id`，只用 `arc_id` 當 source_id 會讓玩家只拿到
    一次入帳（三個地標搶同一把鎖），而不是三個地標各自 +30。
    """
    return f"{arc_id}:{spirit_id}"


def stage_for_value(thresholds: tuple[int, ...], value: int) -> int:
    """
    共鳴值對應的階段：0（未達第一個門檻）／1／2／3……依 `thresholds` 而定。

    每次都從 value 重算。0003 之前 `resonance` 有一個 `stage` 欄位，但這個
    函式從來沒讀過它；那個欄位已經刪掉了，階段一律是算出來的。

    `thresholds` 由呼叫端傳入（來自 `load_resonance_rules()`），這支函式本身
    不碰資料庫——同一個請求裡通常要對好幾筆共鳴值分別算階段（例如
    `GET /profile`），只查一次表、傳同一組門檻進來用，不必每算一筆就打一次
    資料庫。
    """
    return sum(1 for threshold in thresholds if value >= threshold)


def next_threshold(thresholds: tuple[int, ...], value: int) -> int | None:
    """下一個還沒跨過的門檻；已經滿階則回 None（對應 SDD 第8.9節的欄位）。"""
    for threshold in thresholds:
        if value < threshold:
            return threshold
    return None


@dataclass
class ResonanceResult:
    """
    入帳結果。

    `newly_unlocked_stages` 是 list 而不是單一 stage：一次入帳理論上可能跨過
    多個門檻（例如未來若出現 +50 的來源，從 5 直接到 55 會同時跨過 10 與 40）。
    MVP 的 10／20 點跨不過兩個門檻，但回傳 list 讓呼叫端不需要假設這件事——
    每個新解鎖的 stage 都該有自己的一段敘事，漏掉中間那段是靜默的內容缺漏，
    不會有任何錯誤訊息提醒。
    """

    resonance_value: int
    stage: int
    newly_unlocked_stages: list[int] = field(default_factory=list)
    # False 代表這筆來源先前已經入過帳，這次沒有加值（不是錯誤）。
    awarded: bool = True

    @property
    def has_new_unlock(self) -> bool:
        return bool(self.newly_unlocked_stages)


def apply_resonance(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    source_type: str,
    source_id: str,
    amount: int,
) -> ResonanceResult:
    """
    對某玩家的某個靈魂入帳共鳴值，並回報是否解鎖了新階段。

    同一個 `(player_id, source_type, source_id)` 重複呼叫不會重複加值，會回傳
    `awarded=False` 與當下的真實狀態。
    """
    player_uuid = uuid.UUID(str(player_id))
    thresholds = load_resonance_rules(db).thresholds

    if not _record_event(
        db,
        player_id=player_uuid,
        spirit_id=spirit_id,
        source_type=source_type,
        source_id=source_id,
        amount=amount,
    ):
        current = _current_state(db, player_uuid, spirit_id)
        return ResonanceResult(
            resonance_value=current,
            stage=stage_for_value(thresholds, current),
            awarded=False,
        )

    row = (
        db.query(Resonance)
        .filter_by(player_id=player_uuid, spirit_id=spirit_id)
        # 鎖住這一列，避免兩筆不同來源同時入帳時互相覆蓋掉對方的加值。
        .with_for_update()
        .first()
    )
    if row is None:
        row = Resonance(player_id=player_uuid, spirit_id=spirit_id, resonance_value=0)
        db.add(row)

    stage_before = stage_for_value(thresholds, row.resonance_value)
    row.resonance_value += amount
    stage_after = stage_for_value(thresholds, row.resonance_value)

    db.commit()

    return ResonanceResult(
        resonance_value=row.resonance_value,
        stage=stage_after,
        newly_unlocked_stages=list(range(stage_before + 1, stage_after + 1)),
    )


def _record_event(
    db: Session,
    *,
    player_id: uuid.UUID,
    spirit_id: str,
    source_type: str,
    source_id: str,
    amount: int,
) -> bool:
    """
    寫入帳本。回傳 False 代表這筆來源已經入過帳。

    用 savepoint（`begin_nested`）包住這次寫入：撞到 UNIQUE 時只回捲這一小段，
    外層 session 仍然可用，呼叫端不需要為了「重複提交」這種正常情況重建 session。
    """
    try:
        with db.begin_nested():
            db.add(
                ResonanceEvent(
                    player_id=player_id,
                    spirit_id=spirit_id,
                    source_type=source_type,
                    source_id=source_id,
                    amount=amount,
                )
            )
        return True
    except IntegrityError:
        return False


def _current_state(db: Session, player_id: uuid.UUID, spirit_id: str) -> int:
    row = db.query(Resonance).filter_by(player_id=player_id, spirit_id=spirit_id).first()
    return row.resonance_value if row else 0
