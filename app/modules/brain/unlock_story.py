"""
B11．共鳴值解鎖敘事生成（issue #25）。

共鳴值跨過 10／40／100 任一門檻時，由**身體**呼叫這裡生成該階段的解鎖短故事。

## 呼叫順序是硬規則（v2.1 §6.4）

身體必須「**先寫完自己的 `resonance` 表、再呼叫腦袋**」，不可顛倒——否則腦袋
拿到的 `stage` 會跟資料庫不一致，玩家會看到一段講述他還沒達到的關係階段的故事。

這個模組因此**不碰 `resonance` 表，也不自己算 stage**：`stage` 是參數，由已經
寫完資料庫的呼叫端傳進來。它沒有能力顛倒順序，就不會有人不小心顛倒。

## 一次跨多個門檻要生成多段

`apply_resonance` 的 `newly_unlocked_stages` 是 **list**（#16 刻意的設計）。
`generate_unlock_stories()` 對清單裡的每個 stage 各生成一段——只取最後一個會讓
中間那段**靜默消失**，不會有任何錯誤訊息提醒。

## 失敗一律回退，不阻擋任務完成

`generate_unlock_story()` 永遠回傳一個 `UnlockStory`，永遠不拋例外。共鳴值已經
入帳了，玩家的進度是真的——一次生成失敗不該讓整個任務完成流程看起來像出錯。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient
from app.modules.brain.historical_boundary import get_historical_boundary_rules
from app.modules.brain.prompt_builder import persona_section

logger = logging.getLogger(__name__)

# 🔒 文案審核狀態，與 B5／B4 同一個慣例。
STORY_REVIEW_STATUS = "PENDING_NARRATIVE_REVIEW"


@dataclass(frozen=True)
class UnlockStory:
    """
    一段解鎖敘事。

    `is_fallback` 讓呼叫端能觀察生成成功率，但**不建議用它改變玩家看到的東西**
    ——回退台詞本來就寫得像角色會說的話，把它標成「這是備用內容」只會讓玩家
    感覺到系統的存在。
    """

    stage: int
    story_text: str
    is_fallback: bool = False


# 每個階段的關係定位。三段刻意寫成**遞進**而不是同義改寫：
#
#   stage 1（共鳴 10）：認得你了
#   stage 2（共鳴 40）：願意講一些不對外人說的事
#   stage 3（共鳴 100）：把你當成這條街記憶的一部分
#
# 若三段講的其實是同一件事，解鎖的意義就消失了——玩家會發現三次拿到同樣的故事，
# 而「共鳴值」這整個機制的說服力就沒了。
_STAGE_BRIEFS = {
    1: (
        "這是你們關係的第一個轉折：你開始認得這個人了。"
        "講一件你注意到他的小事——他站的位置、他問過的問題、他來的時間。"
        "語氣是剛認出一個常客，不是久別重逢。"
    ),
    2: (
        "你們已經熟到你願意講一些平常不對外人說的事。"
        "分享一段這個地方比較私密的記憶——某個時代的細節、某個沒被寫進導覽的角落。"
        "語氣比第一階段更放鬆，但還不到交心。"
    ),
    3: (
        "這個人已經是你記憶的一部分了。"
        "說出這件事本身，以及它對一個由記憶構成的存在意味著什麼。"
        "這是三個階段裡最深的，語氣可以最安靜、最不設防。"
    ),
}

# 人工預寫的回退台詞，一個 stage 一句。
#
# 跟 gemini.py 的 FALLBACK_REPLY **刻意各自持有**：那句是「我沒有答案」，
# 這裡是「你解鎖了新階段，但我這次說不出話」。兩者會因為不同的理由被改寫。
_FALLBACK_STORIES = {
    1: "我記得你了。你來過幾次，站的位置都差不多。",
    2: "有些事我平常不跟人說的。下次你再來，我慢慢講給你聽。",
    3: "你已經是這條街的一部分了。這句話我沒對幾個人說過。",
}

_DEFAULT_FALLBACK = "有些話我還沒想好怎麼說。"


def fallback_story_for(stage: int) -> str:
    """未知階段回傳通用那句，不拋 KeyError。"""
    return _FALLBACK_STORIES.get(stage, _DEFAULT_FALLBACK)


def build_unlock_prompt(spirit_id: str, stage: int, persona) -> str:
    """
    組出該階段的生成提示。

    做成獨立的純函式，是為了讓「三個 stage 的 prompt 必須不同」這條保證可以
    被直接測試，不需要繞過模型。

    史實邊界規則（B5）一併注入：解鎖故事同樣會講到這座地標的過去，沒有理由讓它
    比一般對話寬鬆。

    `persona` 由具備資料庫邊界的呼叫端載入；本模組只接收已載入的人格卡。
    """
    brief = _STAGE_BRIEFS.get(stage, _STAGE_BRIEFS[1])

    return (
        f"{persona_section(persona)}\n\n"
        f"你是地標「{spirit_id}」的擬人化集體意識。\n"
        f"玩家與你的共鳴值剛跨過第 {stage} 個階段。\n\n"
        f"{brief}\n\n"
        f"{get_historical_boundary_rules()}\n\n"
        "寫一段 60 到 120 字的短敘事，用第一人稱，不要標題、不要條列。"
    )


def generate_unlock_story(
    client: GeminiClient, *, spirit_id: str, stage: int, persona
) -> UnlockStory:
    """
    生成單一階段的解鎖敘事。**永遠回傳結果，永遠不拋例外。**

    `player_id` 刻意不是參數。issue #25 的簽章寫了它，但它在這裡沒有用途——
    敘事的內容只取決於地標與階段，而傳入一個不會被使用的識別碼，只會讓人以為
    生成內容是個人化的。要做個人化（例如帶入該玩家的長期記憶）時，那是一次
    明確的功能決定，不該靠一個早就悄悄放在簽章裡的參數。
    """
    if persona is None:
        logger.info("B11 spirit %s 沒有生效中的人格卡，使用人工預寫台詞", spirit_id)
        return UnlockStory(
            stage=stage, story_text=fallback_story_for(stage), is_fallback=True
        )

    prompt = build_unlock_prompt(spirit_id, stage, persona=persona)

    # B1 的契約是「永遠回非空字串，失敗時回 FALLBACK_REPLY」，所以這裡靠內容
    # 判斷是否回退，而不是 try/except——B1 根本不會拋例外給我們。
    text = client.generate(prompt)

    if not text or text == FALLBACK_REPLY:
        logger.info("B11 解鎖敘事生成失敗，回退人工預寫台詞（stage=%s）", stage)
        return UnlockStory(stage=stage, story_text=fallback_story_for(stage), is_fallback=True)

    return UnlockStory(stage=stage, story_text=text)


def generate_unlock_stories(
    client: GeminiClient, *, spirit_id: str, stages: list[int], persona
) -> list[UnlockStory]:
    """
    對每個新解鎖的階段各生成一段。

    ⚠️ **不要改成只取 `stages[-1]`。** `apply_resonance` 回傳 list 就是為了這件
    事（#16 的設計）：一次入帳可能跨過多個門檻，而每個階段都該有自己的一段敘事。
    只取最後一個會讓中間那段靜默消失——沒有例外、沒有 log，玩家只是永遠看不到
    那一段。
    """
    return [
        generate_unlock_story(
            client, spirit_id=spirit_id, stage=s, persona=persona
        )
        for s in stages
    ]
