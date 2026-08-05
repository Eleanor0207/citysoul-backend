"""
S5．共鳴值入帳與門檻判定（SDD 第3.1／7.5節）。

刻意做成**獨立服務函式**，不綁任何 API 端點。真正的呼叫方
`POST /quests/{questId}/complete` 要等 S4（#15）與 B11 解鎖敘事生成都到位後
才會在 Sprint6 整合，不屬於這裡。

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

from app.modules.body.models import Resonance, ResonanceEvent

# CONTEXT.md：所有靈魂共用的固定門檻，解鎖三個階段。
RESONANCE_THRESHOLDS = (10, 40, 100)

# MVP 固定值（SDD 第7.5節 / CONTEXT.md）。呼叫端仍需自己傳 amount，
# 這裡只是把「規格說幾點」記在程式碼裡，避免每個呼叫端各自寫死一個數字。
AMOUNT_ENCOUNTER_COLLECTION = 10
AMOUNT_QUEST = 20

SOURCE_ENCOUNTER_COLLECTION = "encounter_collection"
SOURCE_QUEST = "quest"


def stage_for_value(value: int) -> int:
    """
    共鳴值對應的階段：0（未達 10）／1（>=10）／2（>=40）／3（>=100）。

    每次都從 value 重算，不信任 `resonance.stage` 欄位——那個欄位是為了查詢
    方便才存的快取，value 才是事實來源。
    """
    return sum(1 for threshold in RESONANCE_THRESHOLDS if value >= threshold)


def next_threshold(value: int) -> int | None:
    """下一個還沒跨過的門檻；已經滿階則回 None（對應 SDD 第8.9節的欄位）。"""
    for threshold in RESONANCE_THRESHOLDS:
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
            resonance_value=current, stage=stage_for_value(current), awarded=False
        )

    row = (
        db.query(Resonance)
        .filter_by(player_id=player_uuid, spirit_id=spirit_id)
        # 鎖住這一列，避免兩筆不同來源同時入帳時互相覆蓋掉對方的加值。
        .with_for_update()
        .first()
    )
    if row is None:
        row = Resonance(player_id=player_uuid, spirit_id=spirit_id, resonance_value=0, stage=0)
        db.add(row)

    stage_before = stage_for_value(row.resonance_value)
    row.resonance_value += amount
    stage_after = stage_for_value(row.resonance_value)
    row.stage = stage_after

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
