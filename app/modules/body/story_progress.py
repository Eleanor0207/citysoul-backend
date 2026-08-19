"""
劇情主線：Beat 推進判定與寫入（backend#72，SDD §20.4）。

跟 `quests.py`／`resonance.py` 同一個分工：純函式判斷「該不該推進」＋一支
會寫 DB 的服務函式，不綁任何 API 端點。

## players_story_progress 是唯一真相來源

`PlayersStoryProgress` 的 docstring 已經寫過一次：`dialogue_turns.story_beat_id`
只是日誌，不是判斷依據。這裡不重複那條規則，只重申呼叫端不能反過來用日誌
推進度。

## 判定是確定性規則，不是 LLM

跟 `quests.complete_quest()` 同一條 CONTEXT.md 原則：「可驗證微任務由後端
確定性規則判定，不由 LLM 判定」。這裡的版本是「前置 beat 是否都在
`players_story_progress` 裡」——純粹查資料庫，不碰腦袋模組。

## 終點 beat 是算出來的，不是存的欄位

`story_beats` 沒有「這是結局」的欄位。用因果鏈本身推出終點——沒有任何其他
active beat 把它列進 `prerequisite_beat_ids`，它就是終點——而不是另外加一個
容易跟 `prerequisite_beat_ids` 各自維護、彼此漂移的旗標。

## 目前沒有真正的呼叫端

`quests` 表裡 `quest_type='story'` 的資料是 0 筆——三地標觀察任務卡在實地
勘查（`story/wanhua_field_survey_checklist.md`），還沒有任何內容能觸發
這裡的 `advance_beat()`。這支服務函式與對應的 API endpoint（`router.py`）
是先把機制建好，等內容到位時直接接上，不是說現在有東西在用它。

⚠️ 同一個理由，`advance_beat()` 對應的 API 端點目前只驗證 session token，
**沒有**比照 `POST /quests/{questId}/complete` 那樣要求 encounter token
做在場證明——因為現在完全沒有呼叫端，無從決定「哪一種在場證明」是對的。
等三地標觀察任務落地、真正的呼叫端（quest turn-in）確定形狀時，這道
gating 需要一併補上，不該假設現在這個寬鬆版本就是終版。
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import PlayersStoryProgress, Spirit
from app.modules.body.resonance import (
    SOURCE_STORY_COMPLETION,
    apply_resonance,
    load_resonance_rules,
    story_completion_source_id,
)
from app.modules.brain.models import StoryArc, StoryBeat


class ArcNotFoundError(LookupError):
    """arc_id 對不到任何存在且生效的 story_arcs 列。"""


class BeatNotFoundError(LookupError):
    """beat_id 對不到任何存在且生效的 story_beats 列。"""


class BeatNotUnlockedError(RuntimeError):
    """前置 beat 還沒全部完成，不能推進到這個 beat。"""


@dataclass
class StoryProgressResult:
    """`advance_beat()` 的回傳形狀。"""

    beat_id: str
    # True 代表這個 beat 先前已經完成過，這次呼叫沒有寫入新資料——重複提交
    # 是正常的使用者行為，不是錯誤（跟 quests.complete_quest() 同一個道理）。
    already_completed: bool
    # 這次推進的是不是這條 arc 的終點 beat（因而觸發了結局共鳴值）。
    story_completed: bool
    completed_beat_ids: list[str] = field(default_factory=list)


def is_terminal_beat(beat_id: str, arc_beats: list[StoryBeat]) -> bool:
    """
    這個 beat 是不是所屬 arc 的終點：沒有任何其他 active beat 把它列進
    `prerequisite_beat_ids`。`arc_beats` 應該是同一條 arc 底下的所有 active
    beat（呼叫端自己篩過），這支函式不自己查資料庫。
    """
    return not any(
        beat_id in (other.prerequisite_beat_ids or [])
        for other in arc_beats
        if other.beat_id != beat_id
    )


def unlockable(beat: StoryBeat, completed_beat_ids: set[str]) -> bool:
    """這個 beat 的前置是否都已完成。沒有前置的 beat（例如序章）永遠可解鎖。"""
    return set(beat.prerequisite_beat_ids or []).issubset(completed_beat_ids)


def arc_state(db: Session, *, player_id: uuid.UUID | str, arc_id: str) -> dict:
    """
    玩家在這條 arc 上的目前進度。**不觸發任何寫入**——查詢端點的常見規矩，
    跟 `queries.py` 那組唯讀查詢同一個原則。
    """
    player_uuid = uuid.UUID(str(player_id))

    arc = db.query(StoryArc).filter_by(arc_id=arc_id, active=True).first()
    if arc is None:
        raise ArcNotFoundError(arc_id)

    arc_beats = db.query(StoryBeat).filter_by(arc_id=arc_id, active=True).all()
    arc_beat_ids = {b.beat_id for b in arc_beats}

    all_completed = _completed_beat_ids(db, player_uuid)
    # 玩家的 players_story_progress 之後可能有別條 arc 的紀錄，這裡限定在
    # 這條 arc 內，不混進來。
    completed_in_arc = all_completed & arc_beat_ids

    eligible = sorted(
        b.beat_id
        for b in arc_beats
        if b.beat_id not in completed_in_arc and unlockable(b, all_completed)
    )

    return {
        "arc_id": arc_id,
        "completed_beat_ids": sorted(completed_in_arc),
        "eligible_beat_ids": eligible,
        "story_completed": any(
            is_terminal_beat(beat_id, arc_beats) for beat_id in completed_in_arc
        ),
    }


def advance_beat(
    db: Session,
    *,
    player_id: uuid.UUID | str,
    arc_id: str,
    beat_id: str,
    now: datetime | None = None,
) -> StoryProgressResult:
    """
    把一個 beat 記成這個玩家已完成。

    `arc_id` 要跟 `beat.arc_id` 一致，不一致當成 `BeatNotFoundError`——URL
    上的 arc_id 與 beat_id 對不起來，跟 beat 根本不存在對呼叫端是同一種
    「查無此節點」，不需要另一種錯誤形狀。

    前置沒滿足就拋 `BeatNotUnlockedError`；已經完成過直接回傳現況，不重複
    寫入也不算錯誤。推進到終點 beat 時，在同一次呼叫裡發放結局共鳴值
    （backend#72／#52 拍板：三個地標各 +30，見 `_award_story_completion`）。
    """
    now = now or datetime.now(timezone.utc)
    player_uuid = uuid.UUID(str(player_id))

    beat = db.query(StoryBeat).filter_by(beat_id=beat_id, active=True).first()
    if beat is None or beat.arc_id != arc_id:
        raise BeatNotFoundError(beat_id)

    completed = _completed_beat_ids(db, player_uuid)

    if beat_id in completed:
        return StoryProgressResult(
            beat_id=beat_id,
            already_completed=True,
            story_completed=False,
            completed_beat_ids=sorted(completed),
        )

    if not unlockable(beat, completed):
        raise BeatNotUnlockedError(beat_id)

    if not _record_beat(db, player_id=player_uuid, beat_id=beat_id, now=now):
        # 併發：另一個請求同時把這個 beat 寫進去了。跟重複提交同一個結果。
        completed.add(beat_id)
        return StoryProgressResult(
            beat_id=beat_id,
            already_completed=True,
            story_completed=False,
            completed_beat_ids=sorted(completed),
        )

    # 🔒 beat 本身先落地，才可能觸發結局共鳴值——跟 v2.1 §6.4「先寫完自己的
    # 表、再呼叫下一步」同一個順序，不可顛倒。
    db.commit()
    completed.add(beat_id)

    arc_beats = (
        db.query(StoryBeat).filter_by(arc_id=beat.arc_id, active=True).all()
        if beat.arc_id
        else []
    )
    story_completed = bool(arc_beats) and is_terminal_beat(beat_id, arc_beats)

    if story_completed:
        _award_story_completion(
            db, player_id=player_uuid, arc_id=beat.arc_id, arc_beats=arc_beats
        )

    return StoryProgressResult(
        beat_id=beat_id,
        already_completed=False,
        story_completed=story_completed,
        completed_beat_ids=sorted(completed),
    )


def _completed_beat_ids(db: Session, player_id: uuid.UUID) -> set[str]:
    rows = db.query(PlayersStoryProgress.beat_id).filter_by(player_id=player_id).all()
    return {row[0] for row in rows}


def _record_beat(
    db: Session, *, player_id: uuid.UUID, beat_id: str, now: datetime
) -> bool:
    """
    寫入 `players_story_progress`。回傳 False 代表這個 beat 已經被記過了。

    用 savepoint（`begin_nested`）包住這次寫入——跟 `resonance._record_event()`
    同一個做法：撞到主鍵時只回捲這一小段，外層 session 仍然可用。
    """
    try:
        with db.begin_nested():
            db.add(PlayersStoryProgress(player_id=player_id, beat_id=beat_id, triggered_at=now))
        return True
    except IntegrityError:
        return False


def _award_story_completion(
    db: Session, *, player_id: uuid.UUID, arc_id: str, arc_beats: list[StoryBeat]
) -> None:
    """
    結局共鳴值：一次入帳給這條 arc 裡每個掛了角色的 beat 對應的地標
    （backend#72／#52 拍板，三個地標各 +30）。

    序章、終點這類不綁角色的 beat（`character_id IS NULL`）不對應任何地標，
    自然被排除——`character_ids` 只收集有值的那些。
    """
    character_ids = {b.character_id for b in arc_beats if b.character_id}
    if not character_ids:
        return

    spirits = db.query(Spirit).filter(Spirit.character_id.in_(character_ids)).all()
    amount = load_resonance_rules(db).amount_story_completion

    for spirit in spirits:
        apply_resonance(
            db,
            player_id=player_id,
            spirit_id=spirit.spirit_id,
            source_type=SOURCE_STORY_COMPLETION,
            source_id=story_completion_source_id(arc_id, spirit.spirit_id),
            amount=amount,
        )
