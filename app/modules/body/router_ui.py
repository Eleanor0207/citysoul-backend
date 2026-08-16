"""
介面視窗要的查詢端點。

選單那五個視窗（聊天紀錄／任務／共鳴值／收藏／設定）各自需要一組資料，但那些
資料不屬於核心迴圈（感應→召喚→對話→任務）。放在這裡而不是 `router.py`，有兩
個理由：

1. **衝突面。** `feature/brain` 那條分支把 `router.py` 重寫了 929 行、
   `schemas.py` 327 行，之後合回 dev 線會是一場大衝突。新檔的衝突面是零。
2. **這一批會長大。** 收藏列表、地區資訊、綁定帳號都要往這裡放。它們共同的
   性質是「唯讀、只需要 session token、不推進任何狀態機」。

目前只有聊天紀錄兩支。

## 唯讀，而且是真的唯讀

⚠️ 這裡的端點**不會 commit、不會修改任何資料列**——跟 `queries.py` 同一條規矩。
查詢端點順手做狀態轉移的話，玩家只要打開一個視窗就等於推進了一次遊戲狀態，
那是很難追查的副作用。
"""
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.body import dialogue_log, models, schemas
from app.modules.body.auth import require_session_token

router = APIRouter(prefix="/api/v1", tags=["body"])

_UNAUTHORIZED = {
    401: {"model": schemas.ErrorResponse, "description": "Session token 無效或未提供"}
}
_SPIRIT_NOT_FOUND = {
    404: {"model": schemas.ErrorResponse, "description": "靈魂不存在，或已下架（is_active=false）"}
}


# ── 回應模型 ──────────────────────────────────────────────────────────

# 寫成 Literal 而不是裸 str，理由跟 `QuestStatus` 一樣：客戶端 codegen 才生得出
# 真正的 enum，SDD v2.1 §11.2.1 硬規則 5「Enum 採寬鬆解析」也才有東西可解析。
#
# ⚠️ 值是 player／spirit，**不是** Redis 短期記憶那邊的 user／assistant。
# 兩套字串的來源與用途都不同，見 `dialogue_log.ROLE_PLAYER` 的說明。
DialogueRole = Literal["player", "spirit"]


class DialogueThreadItem(BaseModel):
    """聊天紀錄第一層：跟這個靈魂的對話摘要。"""

    spirit_id: str
    name: str

    #: 最後一句的內容。清單上只顯示一行，過長由客戶端截斷——截在哪裡是版面問題，
    #: 不同畫面寬度的答案不一樣，後端先截等於替所有客戶端決定了字數。
    last_message: str
    last_role: DialogueRole
    last_at: datetime

    #: 總共幾句（玩家與靈魂分開算）。給客戶端顯示「共 24 句」，也讓「聊過但只有
    #: 一句」跟「聊了很久」在清單上分得出來。
    turn_count: int


class DialogueThreadsResponse(BaseModel):
    """一句都沒聊過時 `threads` 是空陣列，不是 404——冷啟動是正常狀態。"""

    threads: list[DialogueThreadItem]


class DialogueTurnItem(BaseModel):
    turn_id: int
    role: DialogueRole
    content: str

    #: 帶時區的 UTC。⚠️ 客戶端要自己換算成台北時間（+8）再顯示，
    #: 而且「今天／昨天」的分界要用**台北的午夜**算，不是 UTC 的。
    created_at: datetime


class DialogueHistoryResponse(BaseModel):
    """
    某個靈魂的對話，`turns` **由舊到新**（畫面由上往下讀）。

    分頁往「更舊」的方向走：把 `next_before` 原封不動放進下一次請求的 `before`。
    """

    turns: list[DialogueTurnItem]

    #: 還有更舊的嗎。`false` 時客戶端可以停止上拉載入。
    has_more: bool

    #: 下一頁要帶的游標；`has_more` 為 false 時是 null。
    #:
    #: 客戶端**不該**自己拿 `turns[0].turn_id` 去減一——游標的語意屬於後端，
    #: 之後若改成不透明字串，自己算的客戶端會整批壞掉。
    next_before: int | None = None


# ── 端點 ──────────────────────────────────────────────────────────────


@router.get(
    "/dialogue-history/spirits",
    response_model=DialogueThreadsResponse,
    responses={**_UNAUTHORIZED},
)
def list_dialogue_threads(
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    聊天紀錄第一層：我跟哪些靈魂聊過。

    🔒 **只回自己的。** 過濾條件是 session token 解出來的 `player_id`，沒有任何
    路徑或查詢參數能指定別人——這是設計，不是驗證不足。
    """
    return DialogueThreadsResponse(
        threads=[DialogueThreadItem(**t) for t in dialogue_log.threads(db, player_id=player_id)]
    )


@router.get(
    "/spirits/{place_id}/dialogue-history",
    response_model=DialogueHistoryResponse,
    responses={**_UNAUTHORIZED, **_SPIRIT_NOT_FOUND},
)
def get_dialogue_history(
    place_id: str,
    limit: int = Query(
        dialogue_log.DEFAULT_PAGE_SIZE,
        ge=1,
        le=dialogue_log.MAX_PAGE_SIZE,
        description="一次回幾句。",
    ),
    before: int | None = Query(
        None,
        ge=1,
        description="只回 turn_id 比這個小的（更舊的）。第一頁不要帶。",
    ),
    player_id: uuid.UUID = Depends(require_session_token),
    db: Session = Depends(get_db),
):
    """
    聊天紀錄第二層：跟某個靈魂的對話內文，由舊到新。

    ## 為什麼靈魂不存在要回 404，而不是空陣列

    「這個靈魂我沒聊過」與「根本沒有這個靈魂」是兩種不同的情況，客戶端要能分開：
    前者顯示「還沒有聊過」，後者代表客戶端拿著一個過期的 id 在問——那是 bug，
    不該被偽裝成一個平靜的空畫面。對齊 `GET /resonance/{spirit_id}` 的做法。

    下架的靈魂也回 404（跟其他所有靈魂查詢端點一致）。**但已經存在的對話紀錄
    本身不會被藏起來**，它們仍然出現在第一層的清單裡——見
    `dialogue_log.threads()` 的說明。
    """
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise HTTPException(status_code=404, detail="spirit not found")

    turns, has_more = dialogue_log.recent_turns(
        db, player_id=player_id, spirit_id=place_id, limit=limit, before_turn_id=before
    )

    return DialogueHistoryResponse(
        turns=[
            DialogueTurnItem(
                turn_id=t.turn_id, role=t.role, content=t.content, created_at=t.created_at
            )
            for t in turns
        ],
        has_more=has_more,
        # 游標指向這一頁**最舊**的那一句。turns 已經反轉成由舊到新，所以是第 0 筆。
        # 沒有下一頁時明確回 null，而不是回一個不會被用到的數字——那會讓客戶端
        # 誤以為還能再翻。
        next_before=turns[0].turn_id if (has_more and turns) else None,
    )
