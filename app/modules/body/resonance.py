"""
S5．共鳴值入帳與門檻判定（SDD 第3.1／7.5節）。

## 🔑 來源只有兩種（A.L. 2026-08-19 定案，取代先前的 quest／photo 兩條）

| 來源 | 點數 | 頻率 | 觸發點 |
|---|---|---|---|
| `daily_encounter` | +10 | **每個地標每天一次** | `/summon` 成功、對話窗打開那一刻 |
| `story_beat` | +30 | 每個 beat 一輩子一次 | 劇本節點完成（**尚未接上**，見下） |

先前的 `quest`（+20，每日任務完成）與 `encounter_collection`（+10，地標拍照）
**都已移除**：每日任務這個玩法不做了，拍照辨識整條路也一併拆掉。舊的
`resonance_events` 列不會被清掉，它們是歷史流水帳；只是不會再有新的那兩種
`source_type` 寫進來。

## 成長曲線

門檻是 `(10, 40, 100)`，共鳴值是 **per-spirit** 的（`resonance` 的主鍵是
`(player_id, spirit_id)`）。所以每個地標各自算：

- **stage 1** ＝ 第一天走到現場召喚（+10）
- **stage 2** ＝ 走完該地標的劇本節點（+30 → 40），或連續回訪四天
- **stage 3** ＝ 100 點，劇本走完後還要再回訪六天

⚠️ 劇本任務目前只有萬華 arc 一條，三個階段橫跨萬華區三個地標，所以**只有那三個
地標拿得到 +30**；其餘七個地標唯一的成長路徑是每天回訪 +10（十天到滿階）。
這是刻意的落差——劇本走過的地方關係本來就該深得比較快。

## 🔴 去重鍵裡沒有 spirit_id

`uq_resonance_events_source` ＝ `UNIQUE(player_id, source_type, source_id)`，
**不含 `spirit_id`**。所以「每個地標每天一次」不能把 `source_id` 只填日期——
那會變成玩家當天在龍山寺拿了 +10 之後，走到艋舺公園就撞約束、拿不到。

正確的鍵是 `{spirit_id}:{台北日期}`，由 `daily_encounter_source_id()` 產生。
**不要在呼叫端自己拼這個字串**，拼錯了不會有任何錯誤訊息，只會靜默地讓玩家
少拿分或多拿分。

## 防重複入帳靠資料庫，不靠先查後寫

「先查帳本有沒有這筆、沒有才寫」在並行下是錯的：兩個請求可以同時查到「沒有」，
然後都寫進去。唯一擋得住的是上面那條 UNIQUE 約束。

所以這裡的流程是**先寫帳本、撞到約束就當作重複**，而不是反過來。撞到時優雅
回傳現況（`awarded=False`），不讓例外炸到呼叫端——同一天重複召喚同一個地標是
正常的使用者行為（玩家在現場逛一圈又點一次），不是錯誤。

🔑 **「每天一次」因此不需要任何跨日判斷邏輯**：日期編進 `source_id`，跨過台北
午夜就自然是一把新鑰匙，UNIQUE 自己會放行。沒有排程、沒有 reset 欄位、沒有
「上次領取時間」要比對——那些都是同一件事比較容易寫錯的寫法。
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import Resonance, ResonanceEvent
from app.modules.body.taipei import taipei_today

# CONTEXT.md：所有靈魂共用的固定門檻，解鎖三個階段。
RESONANCE_THRESHOLDS = (10, 40, 100)

# A.L. 2026-08-19 定案。呼叫端仍需自己傳 amount，這裡只是把「規則說幾點」記在
# 程式碼裡，避免每個呼叫端各自寫死一個數字。
AMOUNT_DAILY_ENCOUNTER = 10
AMOUNT_STORY_BEAT = 30

SOURCE_DAILY_ENCOUNTER = "daily_encounter"
SOURCE_STORY_BEAT = "story_beat"


def daily_encounter_source_id(spirit_id: str, *, now: datetime | None = None) -> str:
    """
    每日召喚入帳的去重鍵：`{spirit_id}:{台北日期}`。

    🔴 **spirit_id 一定要在裡面**，因為 `uq_resonance_events_source` 沒有
    `spirit_id` 欄位（見模組 docstring）。少了它，玩家一天只能在一個地標拿分。

    日期用台北時區而不是 UTC：SDD 第7節決策8 規定所有「每日一次」統一以
    Asia/Taipei 午夜為基準。用 UTC 的話換日會發生在台灣時間早上八點，玩家吃早餐
    的時候會莫名其妙多拿一次分。
    """
    return f"{spirit_id}:{taipei_today(now or datetime.now(timezone.utc))}"


def stage_for_value(value: int) -> int:
    """
    共鳴值對應的階段：0（未達 10）／1（>=10）／2（>=40）／3（>=100）。

    每次都從 value 重算。0003 之前 `resonance` 有一個 `stage` 欄位，但這個
    函式從來沒讀過它；那個欄位已經刪掉了，階段一律是算出來的。
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
        row = Resonance(player_id=player_uuid, spirit_id=spirit_id, resonance_value=0)
        db.add(row)

    stage_before = stage_for_value(row.resonance_value)
    row.resonance_value += amount
    stage_after = stage_for_value(row.resonance_value)

    db.commit()

    return ResonanceResult(
        resonance_value=row.resonance_value,
        stage=stage_after,
        newly_unlocked_stages=list(range(stage_before + 1, stage_after + 1)),
    )


def award_daily_encounter(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    spirit_id: str,
    now: datetime | None = None,
) -> ResonanceResult:
    """
    每天第一次召喚該地標 → +10。**這一天已經領過就回 `awarded=False`。**

    呼叫端是 `/summon`，在場驗證通過之後。放在驗證之後而不是之前，理由跟同一支
    端點裡的觀察期紀錄與相遇收藏一樣：沒通過在場驗證的請求不構成一次相遇，
    不該產生任何入帳。

    ⚠️ **這支不會拋例外，連 `awarded=False` 都是正常結果**，所以呼叫端不需要
    try/except，也不該把它當成錯誤回報給玩家——同一天第二次召喚不加分是設計，
    不是失敗。

    `now` 只給測試用（跨午夜行為）。正式路徑一律不傳。
    """
    return apply_resonance(
        db,
        player_id=player_id,
        spirit_id=spirit_id,
        source_type=SOURCE_DAILY_ENCOUNTER,
        source_id=daily_encounter_source_id(spirit_id, now=now),
        amount=AMOUNT_DAILY_ENCOUNTER,
    )


def award_story_beat(
    db: Session, *, player_id: uuid.UUID | str, spirit_id: str, beat_id: str
) -> ResonanceResult:
    """
    劇本節點完成 → +30。**每個 beat 一輩子一次。**

    ⚠️ **還沒有呼叫端。** `players_story_progress` 那張表存在，但後端目前沒有任何
    地方寫入它，`check_beat_unlockable()` 也只活在 docstring 裡——劇本進度整條線
    還沒實作（萬華 arc 的 `beat_finale` 要等 Lead 改完劇本機制才定案）。

    先寫是為了讓接上去的時候只需要在 beat 完成的**同一個交易裡**叫這一支，而不是
    臨時決定用什麼 `source_type`、什麼 `source_id`。跟
    `collections_service.grant_arc_completion_collectible()` 同一套處理方式，
    理由也一樣：兩條路先長成同一個形狀，日後不會分岔成語意不同的兩套發放邏輯。

    `source_id` 用 `beat_id` 而不是 `{spirit_id}:{beat_id}`：beat_id 本身就是
    `story_beats` 的主鍵、全域唯一，前面再加 spirit_id 只會讓同一個 beat 在
    不同 spirit 下變成兩把鑰匙——而那正是「一輩子一次」要擋的事。

    ⚠️ 這跟 `daily_encounter_source_id()` **刻意相反**，兩者的差別不是疏忽：
    每日入帳要的是「每個地標各自每天一次」，所以鍵裡必須有 spirit_id；
    劇本入帳要的是「這個節點全玩家生涯一次」，所以鍵裡必須沒有。
    """
    return apply_resonance(
        db,
        player_id=player_id,
        spirit_id=spirit_id,
        source_type=SOURCE_STORY_BEAT,
        source_id=beat_id,
        amount=AMOUNT_STORY_BEAT,
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
