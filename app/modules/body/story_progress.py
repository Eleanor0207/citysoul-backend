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
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body.models import (
    PlayerInventory,
    PlayersStoryProgress,
    PlayersStoryVariable,
    QuestProgress,
    Spirit,
)
from app.modules.body.quests import STATUS_COMPLETED
from app.modules.body.resonance import (
    SOURCE_STORY_COMPLETION,
    apply_resonance,
    load_resonance_rules,
    story_completion_source_id,
)
from app.modules.brain.models import StoryArc, StoryBeat

logger = logging.getLogger(__name__)

# `player_inventory.item_type` 是 CHECK 約束擋著的列舉，但 arc 文件的 `items:`
# 區塊沒有型別欄位——內容作者描述的是「這是什麼東西」，不是資料庫的分類。
# 對應寫在這裡，而不是要求內容檔多填一欄。
#
# 未知的 item 落到 `story_key_item`：主線發出來的東西預設是推進用的關鍵物件，
# 而分類錯誤的後果只是 `/inventory` 的分組不準，不會擋住任何進度。
_ITEM_TYPES = {
    "item_wanhua_letter": "story_document",
    "item_homeward_painting": "story_key_item",
}
_DEFAULT_ITEM_TYPE = "story_key_item"


class ArcNotFoundError(LookupError):
    """arc_id 對不到任何存在且生效的 story_arcs 列。"""


class BeatNotFoundError(LookupError):
    """beat_id 對不到任何存在且生效的 story_beats 列。"""


class BeatNotUnlockedError(RuntimeError):
    """前置 beat 還沒全部完成，不能推進到這個 beat。"""


class RequiredQuestIncompleteError(RuntimeError):
    """`required_quest_ids` 裡有玩家還沒完成的任務，不能推進到這個 beat。

    跟另外兩種擋下來的原因分開：任務沒做完是**玩家還有事情要做**，前置 beat
    沒完成是走錯順序，道具沒拿到多半是上一節的發放壞了。三種的補救方式完全
    不同，合成同一個錯誤在現場就分不出來。
    """


class RequiredItemMissingError(RuntimeError):
    """`required_item_ids` 裡有玩家還沒拿到的道具，不能推進到這個 beat。

    跟 `BeatNotUnlockedError` 分開，因為兩者的補救方式完全不同：前置沒完成
    要玩家回去把某一節走完，道具沒拿到則多半是**上一節的發放沒有生效**。
    合成同一種錯誤的話，這兩種情況在 log 上長得一樣。
    """


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
    # 這次推進實際發出去的道具。重複提交時是空的——道具只發一次，靠
    # `uq_inventory_player_item` 保證，不靠呼叫端記得自己有沒有領過。
    granted_item_ids: list[str] = field(default_factory=list)
    # 這次推進寫進去的劇情變數，例如 `{"story_focus": "person"}`。
    #
    # **set_once**：同一條 arc 上一個變數只寫得進去一次。已經有值時這裡是空的，
    # 那不是錯誤——玩家回看序章重選一次，第一次的選擇仍然算數（文件 §2.3）。
    set_variables: dict = field(default_factory=dict)


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


def unlockable(
    beat: StoryBeat,
    completed_beat_ids: set[str],
    held_item_ids: set[str] | None = None,
    completed_quest_ids: set[str] | None = None,
) -> bool:
    """這個 beat 的前置是否都已滿足。沒有前置的 beat（例如序章）永遠可解鎖。

    三道門，語意一致，都是「這個集合是不是子集」：

    1. `prerequisite_beat_ids` —— 前置節點都走過了
    2. `required_item_ids` —— 必要道具都在手上
    3. `required_quest_ids` —— 必要任務都完成了

    ⚠️ `held_item_ids` 與 `completed_quest_ids` 省略時**那道門不檢查**。這是為了
    讓純判斷前置鏈的呼叫端（例如匯入器的圖驗證）不必先有一個玩家，但也意味著
    服務層一定要把它們傳進來——漏傳的話那道門會安靜地失效，而症狀是「玩家沒
    做任務也能把主線推完」。
    """
    if not set(beat.prerequisite_beat_ids or []).issubset(completed_beat_ids):
        return False
    if held_item_ids is not None and not set(
        beat.required_item_ids or []
    ).issubset(held_item_ids):
        return False
    if completed_quest_ids is not None and not set(
        beat.required_quest_ids or []
    ).issubset(completed_quest_ids):
        return False
    return True


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
    held_items = _held_item_ids(db, player_uuid)
    done_quests = _completed_quest_ids(db, player_uuid)
    # 玩家的 players_story_progress 之後可能有別條 arc 的紀錄，這裡限定在
    # 這條 arc 內，不混進來。
    completed_in_arc = all_completed & arc_beat_ids

    eligible = sorted(
        b.beat_id
        for b in arc_beats
        if b.beat_id not in completed_in_arc
        and unlockable(b, all_completed, held_items, done_quests)
    )

    times = completed_beat_times(db, player_uuid)

    return {
        "arc_id": arc_id,
        "completed_beat_ids": sorted(completed_in_arc),
        # 依完成時間排序，不是依 id——玩家走過的順序才是這串東西的意義。
        "completed_beats": [
            {"beat_id": beat_id, "completed_at": times[beat_id]}
            for beat_id in sorted(completed_in_arc, key=lambda b: times[b])
            if beat_id in times
        ],
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
    chosen_option_ids: list[str] | None = None,
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

    held_items = _held_item_ids(db, player_uuid)
    missing = set(beat.required_item_ids or []) - held_items
    if missing:
        raise RequiredItemMissingError(", ".join(sorted(missing)))

    unfinished = set(beat.required_quest_ids or []) - _completed_quest_ids(
        db, player_uuid
    )
    if unfinished:
        raise RequiredQuestIncompleteError(", ".join(sorted(unfinished)))

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

    # 🔒 跟結局共鳴值同一個順序：beat 先落地，再做「下一步」。發放失敗時玩家
    # 的進度不會回捲，而道具可以補發（唯一索引讓補發是安全的）。
    granted = _grant_beat_items(db, player_id=player_uuid, beat=beat)
    variables = _record_choices(
        db, player_id=player_uuid, arc_id=arc_id, beat=beat,
        chosen_option_ids=chosen_option_ids or [],
    )

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
        granted_item_ids=granted,
        set_variables=variables,
    )


def _completed_beat_ids(db: Session, player_id: uuid.UUID) -> set[str]:
    rows = db.query(PlayersStoryProgress.beat_id).filter_by(player_id=player_id).all()
    return {row[0] for row in rows}


def completed_beat_times(db: Session, player_id: uuid.UUID) -> dict[str, datetime]:
    """每個已完成 beat 的完成時間。

    `players_story_progress` 的主鍵是 `(player_id, beat_id)`，同一個 beat 只記
    得了一次，所以 `triggered_at` 就是完成時間——不需要另外一個 `completed_at`
    欄位（那只會變成一個永遠等於前者的複製品）。

    終點 beat 的時間＝玩家走完這條主線的時間。
    """
    rows = (
        db.query(PlayersStoryProgress.beat_id, PlayersStoryProgress.triggered_at)
        .filter_by(player_id=player_id)
        .all()
    )
    return {beat_id: triggered_at for beat_id, triggered_at in rows}


def _held_item_ids(db: Session, player_id: uuid.UUID) -> set[str]:
    rows = db.query(PlayerInventory.item_id).filter_by(player_id=player_id).all()
    return {row[0] for row in rows}


def _completed_quest_ids(db: Session, player_id: uuid.UUID) -> set[str]:
    """這個玩家已完成的任務。

    ⚠️ 讀的是 `quest_progress`（玩家做了什麼），不是 `quests`（目錄裡有什麼）。
    目錄只說任務存在，不代表任何人做過。
    """
    rows = (
        db.query(QuestProgress.quest_id)
        .filter_by(player_id=player_id, status=STATUS_COMPLETED)
        .all()
    )
    return {row[0] for row in rows}


def beat_commands(beat: StoryBeat) -> list[dict]:
    """`narrative_directive` 裡的 `commands` 清單。

    ⚠️ **`narrative_directive` 是 TEXT 欄位裡的 JSON 字串**，不是 JSONB——
    匯入器用 `json.dumps()` 把 `nodes`／`commands`／`completion` 整包塞進去。
    解析失敗不該讓玩家卡在半路：beat 這時已經記成完成了，回空清單等於「這一節
    沒有東西可發」，而壞掉的內容會在 log 上留下痕跡等人去修。
    """
    raw = beat.narrative_directive
    if not raw:
        return []
    try:
        directive = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("beat %s has unparsable narrative_directive", beat.beat_id)
        return []
    commands = directive.get("commands") if isinstance(directive, dict) else None
    return [c for c in (commands or []) if isinstance(c, dict)]


def _record_choices(
    db: Session,
    *,
    player_id: uuid.UUID,
    arc_id: str,
    beat: StoryBeat,
    chosen_option_ids: list[str],
) -> dict:
    """把玩家選到的選項寫成劇情變數，回傳這次真的寫進去的。

    ## set_once 由主鍵保證，不由呼叫端記得

    `players_story_variables` 的主鍵是 `(player_id, arc_id, variable)`，寫入用
    `ON CONFLICT DO NOTHING`。所以「第一個看的位置寫入，之後回看不覆蓋」
    （文件 §2.3）是**資料庫層的事實**，不是某段程式碼要小心維持的約定。

    ## 值要在 arc 宣告的合法範圍內

    `story_arcs.variables` 存著每個變數可以是哪些值。客戶端送一個不在節點裡的
    `option_id`，或節點寫了一個沒宣告過的值，都安靜跳過並留在 log 上——那是
    內容或客戶端的錯，不該讓已經完成的 beat 回捲。
    """
    if not chosen_option_ids:
        return {}

    # option_id → {變數: 值}，從這個 beat 自己的節點取，不信任客戶端送的內容。
    directive = {}
    if beat.narrative_directive:
        try:
            parsed = json.loads(beat.narrative_directive)
            directive = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            directive = {}

    by_option: dict[str, dict] = {}
    for node in directive.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for option in node.get("options") or []:
            if isinstance(option, dict) and option.get("id"):
                by_option[option["id"]] = dict(option.get("set_once") or {})

    allowed = {}
    arc = db.query(StoryArc).filter_by(arc_id=arc_id).first()
    if arc is not None and isinstance(arc.variables, dict):
        allowed = arc.variables

    written: dict = {}
    for option_id in chosen_option_ids:
        assignments = by_option.get(option_id)
        if assignments is None:
            logger.warning(
                "beat %s: 收到不屬於這個節點的 option_id %r", beat.beat_id, option_id
            )
            continue

        for variable, value in assignments.items():
            valid = allowed.get(variable)
            if valid is not None and value not in valid:
                logger.warning(
                    "beat %s: %s=%r 不在 arc 宣告的合法值裡 %r",
                    beat.beat_id, variable, value, valid,
                )
                continue

            statement = (
                pg_insert(PlayersStoryVariable)
                .values(
                    player_id=player_id,
                    arc_id=arc_id,
                    variable=variable,
                    value=value,
                    set_by_beat_id=beat.beat_id,
                )
                .on_conflict_do_nothing(
                    index_elements=["player_id", "arc_id", "variable"]
                )
                .returning(PlayersStoryVariable.variable)
            )
            if db.execute(statement).scalar_one_or_none() is not None:
                written[variable] = value

    if written:
        db.commit()

    return written


def story_variables(db: Session, *, player_id: uuid.UUID | str, arc_id: str) -> dict:
    """玩家在這條 arc 上已經定下來的變數。"""
    rows = (
        db.query(PlayersStoryVariable.variable, PlayersStoryVariable.value)
        .filter_by(player_id=uuid.UUID(str(player_id)), arc_id=arc_id)
        .all()
    )
    return {variable: value for variable, value in rows}


def _grant_beat_items(
    db: Session, *, player_id: uuid.UUID, beat: StoryBeat
) -> list[str]:
    """執行這個 beat 的 `grant_item` 指令，回傳這次真的發出去的道具。

    ## 只執行 `grant_item`

    `commands` 裡還有 `show_asset`／`show_info_card`／`show_story_fragment`
    這些**呈現指令**——那些是客戶端該做的事，後端執行它們沒有意義。這裡刻意
    只認 `grant_item`，遇到不認得的指令安靜跳過，不報錯：呈現指令出現在這裡
    是正常的，不是資料壞掉。

    ## 重複發放靠唯一索引，不靠先查再寫

    跟 `router._grant_district_entry_item()` 同一個做法，理由也一樣：玩家重複
    提交、或兩個請求同時進來時，「先查有沒有再寫」會漏掉。
    """
    granted: list[str] = []

    for command in beat_commands(beat):
        item_id = command.get("grant_item")
        if not item_id:
            continue

        statement = (
            pg_insert(PlayerInventory)
            .values(
                player_id=player_id,
                item_type=_ITEM_TYPES.get(item_id, _DEFAULT_ITEM_TYPE),
                item_id=item_id,
            )
            .on_conflict_do_nothing(index_elements=["player_id", "item_id"])
            .returning(PlayerInventory.inventory_id)
        )
        if db.execute(statement).scalar_one_or_none() is not None:
            granted.append(item_id)

    if granted:
        db.commit()

    return granted


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
