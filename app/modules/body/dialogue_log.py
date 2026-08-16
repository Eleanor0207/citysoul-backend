"""
對話日誌（`dialogue_turns`）的寫入與查詢。

## 為什麼讀跟寫放在同一支

這張表在 2026-08-16 之前**從來沒有被寫過**。`models.py` 定義了它、migration
0013 建了它，但全專案沒有任何一行程式碼寫進去——`grep -rn "DialogueTurn"` 只
會找到定義它的那一行。

對話實際上只存在 Redis 的 `session:{player_id}:{spirit_id}`，而那個 key 有
`SESSION_TTL_SECONDS = 30 分鐘`。那是給 B7 短期記憶用的上下文，不是紀錄。

所以「聊天紀錄」不是「開一支讀取端點」，是**先讓資料存在**。讀與寫是同一件事
的兩半，拆到兩個模組只會讓下一個人以為其中一半本來就有。

## 為什麼不寫進 router.py／queries.py／schemas.py

`feature/brain` 那條分支把 `router.py` 重寫了 929 行、`schemas.py` 327 行，
之後合進 dev 線會是一場大衝突。這支是新檔，衝突面為零；`router.py` 那邊只留
兩行（import 與一次呼叫）。
"""
from __future__ import annotations

import logging
import uuid as uuid_module

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.body.models import DialogueTurn, Resonance, Spirit

logger = logging.getLogger(__name__)

# ⚠️ **跟 Redis 那邊的字串不一樣。**
#
# `append_session_turn()` 寫的是 `user` / `assistant`（那是餵給模型的格式），
# 但 `dialogue_turns` 有 CHECK 約束 `role IN ('player', 'spirit')`。照抄 Redis
# 那兩個字會直接違反約束——而且因為寫入是靜默失敗的（見 `record_turns`），
# 症狀會是「紀錄永遠是空的」而不是任何錯誤訊息。
#
# 定成常數就是為了讓這兩套字串不會在某次複製貼上時被混在一起。
ROLE_PLAYER = "player"
ROLE_SPIRIT = "spirit"

#: 一次最多回幾輪。客戶端傳更大的值會被夾到這裡。
MAX_PAGE_SIZE = 200

#: 沒指定 `limit` 時的頁大小。
DEFAULT_PAGE_SIZE = 50


def _player_uuid(player_id) -> uuid_module.UUID:
    return uuid_module.UUID(str(player_id))


# ── 寫入 ──────────────────────────────────────────────────────────────


def record_turns(
    db: Session,
    *,
    player_id,
    spirit_id: str,
    user_input: str,
    reply_text: str,
) -> bool:
    """
    把一次來回寫成兩列（玩家一列、靈魂一列）。**永遠不拋例外。**

    回傳有沒有寫成功，只給呼叫端做觀測用；對話流程不該因為這個值改變行為。

    ## 為什麼吞掉例外

    呼叫這支的時候，玩家的回覆**已經生成完畢、配額也已經扣掉**了。日誌寫不進去
    是可惜，但把它變成 500 等於「因為記帳失敗，所以假裝這次對話沒發生過」——
    玩家付了配額卻什麼都沒拿到，這是比少一列日誌嚴重得多的失敗。

    ⚠️ 失敗時一定要 `rollback()`。SQLAlchemy 的 session 在錯誤之後會進入
    「需要 rollback」的狀態，不清掉的話**這個請求後續任何一次 DB 操作都會炸**，
    而那些操作跟對話日誌一點關係都沒有——排查時會完全找不到方向。

    ## 為什麼兩列而不是一列

    表的粒度是「一句話」（`role` + `content`），不是「一次來回」。B6 長期記憶的
    夜間批次要逐句讀，聊天視窗也要逐句畫泡泡。存成一列再切，等於把切分規則
    複製到每個讀取端。
    """
    try:
        # 共鳴值是**當下**的快照，不是外鍵——之後共鳴值漲了，這一列仍然記得
        # 「講這句話的時候我們才剛認識」。所以現在讀、現在存，不能之後再回推。
        resonance_value = _resonance_value_at(db, player_id=player_id, spirit_id=spirit_id)

        db.add_all(
            [
                DialogueTurn(
                    player_id=_player_uuid(player_id),
                    spirit_id=spirit_id,
                    role=ROLE_PLAYER,
                    content=user_input,
                    resonance_value_at_time=resonance_value,
                ),
                DialogueTurn(
                    player_id=_player_uuid(player_id),
                    spirit_id=spirit_id,
                    role=ROLE_SPIRIT,
                    content=reply_text,
                    resonance_value_at_time=resonance_value,
                ),
            ]
        )
        db.commit()
        return True
    except Exception:  # noqa: BLE001 — 見上面的說明，這裡的重點就是「什麼都不放過」
        db.rollback()
        logger.exception(
            "對話日誌寫入失敗（player=%s spirit=%s）。對話本身已正常回覆。",
            player_id,
            spirit_id,
        )
        return False


def _resonance_value_at(db: Session, *, player_id, spirit_id: str) -> int:
    row = (
        db.query(Resonance.resonance_value)
        .filter(
            Resonance.player_id == _player_uuid(player_id),
            Resonance.spirit_id == spirit_id,
        )
        .first()
    )
    return row[0] if row else 0


# ── 查詢（唯讀，不 commit、不改任何列）──────────────────────────────────


def recent_turns(
    db: Session,
    *,
    player_id,
    spirit_id: str,
    limit: int = DEFAULT_PAGE_SIZE,
    before_turn_id: int | None = None,
) -> tuple[list[DialogueTurn], bool]:
    """
    某個靈魂的對話，**由舊到新**回傳，外加「還有沒有更舊的」。

    🔒 過濾條件包含 `player_id`，而它來自 session token、不是路徑參數。
    只憑 `spirit_id` 查會回傳**所有玩家**跟這個靈魂講過的話。

    ## 為什麼游標是 `turn_id` 不是 `created_at`

    `created_at` 的精度不保證能分開同一次來回的兩列（玩家與靈魂那兩句常常落在
    同一微秒內，取決於資料庫的時鐘精度）。拿它當游標的下場是翻頁時**漏掉或重複**
    邊界上的那幾句——而且只在對話很密集時才出現，測試資料稀疏就看不到。

    `turn_id` 是 `BigInteger Identity`，嚴格單調遞增，天生就是穩定游標。

    ## 為什麼查的時候由新到舊、回傳前反轉

    要的是「最新的 N 筆」，所以 SQL 必須 `ORDER BY turn_id DESC LIMIT N`——由舊
    到新查的話會拿到**最早的** N 筆，玩家永遠看不到今天講的話。

    但聊天視窗是由上往下讀的，所以回傳前反轉回時間正序。這一步在 Python 做，
    N 最多 200 筆，不值得為它寫一層子查詢。

    ## 多取一筆判斷 `has_more`

    取 `limit + 1` 筆，拿得到第 N+1 筆就代表還有更舊的。這比另外下一次
    `COUNT(*)` 便宜得多，而且不會有兩次查詢之間資料變動的不一致。
    """
    limit = max(1, min(limit, MAX_PAGE_SIZE))

    query = db.query(DialogueTurn).filter(
        DialogueTurn.player_id == _player_uuid(player_id),
        DialogueTurn.spirit_id == spirit_id,
    )
    if before_turn_id is not None:
        query = query.filter(DialogueTurn.turn_id < before_turn_id)

    rows = query.order_by(DialogueTurn.turn_id.desc()).limit(limit + 1).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()

    return rows, has_more


def threads(db: Session, *, player_id) -> list[dict]:
    """
    這個玩家跟哪些靈魂聊過，各自的最後一句、時間與總句數。依最後一句由新到舊。

    **只列聊過的。** 一句都沒講過的靈魂不會出現——那正是清單相對於下拉選單的
    價值：地標有十個，聊過的通常兩三個。

    ## 為什麼「最後一句」是 `MAX(turn_id)` 而不是 `MAX(created_at)`

    跟 `recent_turns` 的游標同一個理由：同一次來回的兩列時間可能相同，用時間取
    最大值會**隨機**拿到玩家那句或靈魂那句，清單上的預覽文字會時好時壞地跳動。
    `turn_id` 沒有這個問題。

    ## 為什麼不過濾 `is_active`

    靈魂下架不代表對話沒發生過。把紀錄一起藏起來，玩家看到的是自己的聊天紀錄
    莫名其妙少了一段——那比看到一個已下架地標的舊對話更難解釋。
    """
    pid = _player_uuid(player_id)

    # 先算出每個靈魂的「最後一列是哪一列」與「總共幾列」。分成子查詢是因為
    # 這兩個聚合值算完之後，還要回頭把那一列的內容撈出來——同一次 GROUP BY
    # 拿不到非聚合欄位。
    latest = (
        db.query(
            DialogueTurn.spirit_id.label("spirit_id"),
            func.max(DialogueTurn.turn_id).label("last_turn_id"),
            func.count(DialogueTurn.turn_id).label("turn_count"),
        )
        .filter(DialogueTurn.player_id == pid)
        .group_by(DialogueTurn.spirit_id)
        .subquery()
    )

    rows = (
        db.query(
            latest.c.spirit_id,
            latest.c.turn_count,
            DialogueTurn.role,
            DialogueTurn.content,
            DialogueTurn.created_at,
            Spirit.display_name,
        )
        .join(DialogueTurn, DialogueTurn.turn_id == latest.c.last_turn_id)
        .join(Spirit, Spirit.spirit_id == latest.c.spirit_id)
        .order_by(latest.c.last_turn_id.desc())
        .all()
    )

    return [
        {
            "spirit_id": spirit_id,
            "name": display_name,
            "last_message": content,
            "last_role": role,
            "last_at": created_at,
            "turn_count": turn_count,
        }
        for spirit_id, turn_count, role, content, created_at, display_name in rows
    ]
