"""
任務完成的敘事包裝（issue #43 引用的 `generate_quest_wrapper`）。

任務完成本身是**確定性規則**判定的（#34，CONTEXT.md 硬規則）。這裡生成的是
包在結果外面的那句話——「你做到了，而這對我意味著什麼」。

## 包裝失敗不能讓進度消失

任務已經完成、共鳴值已經入帳了。這一層只是包裝，所以 `generate_quest_wrapper()`
**永遠不拋例外**，失敗時回退人工預寫台詞。玩家的進度是真的，不該因為一次生成
失敗而看起來像出錯。

## 為什麼不放進 prompt_builder.py

`prompt_builder` 組的是**對話**的 System Instruction 與 User Turn，那條路徑上
有人格、記憶、當日情境。任務包裝只需要地標與任務 id，兩者的輸入完全不同，
硬塞進同一個模組只會讓那邊的簽章長出一堆對話用不到的可選參數。
"""
from __future__ import annotations

import logging

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient
from app.modules.brain.historical_boundary import get_historical_boundary_rules

logger = logging.getLogger(__name__)

# 🔒 文案審核狀態，與 B5／B4／B11／B9 同一個慣例。
WRAPPER_REVIEW_STATUS = "PENDING_NARRATIVE_REVIEW"

# 人工預寫的回退台詞。
#
# 刻意不提「任務」兩個字——玩家剛完成的是一件在現場做到的事，用系統詞彙包裝它
# 會把人從情境裡拉出來。
_FALLBACK_WRAPPER = "你做到了。這種事我看多了，但每一次還是不太一樣。"


def build_quest_wrapper_prompt(spirit_id: str, quest_id: str) -> str:
    return (
        f"你是地標「{spirit_id}」的擬人化集體意識。\n"
        f"一位玩家剛完成了與你有關的一件小任務（{quest_id}）。\n\n"
        f"{get_historical_boundary_rules()}\n\n"
        "用第一人稱寫一句到兩句話，回應他完成了這件事。\n"
        "語氣是看著一個常來的人做到了什麼，不是頒獎。不要標題、不要條列。"
    )


def generate_quest_wrapper(
    client: GeminiClient, *, spirit_id: str, quest_id: str
) -> str:
    """
    生成任務完成的包裝台詞。**永遠回傳非空字串，永遠不拋例外。**

    B1 的契約是「永遠回非空字串，失敗時回 `FALLBACK_REPLY`」，所以這裡靠內容
    判斷是否回退，而不是 try/except——B1 不會拋例外給我們。
    """
    text = client.generate(build_quest_wrapper_prompt(spirit_id, quest_id))

    if not text or text == FALLBACK_REPLY:
        logger.info("任務包裝台詞生成失敗，回退人工預寫（%s）", quest_id)
        return _FALLBACK_WRAPPER

    return text
