"""
B11．共鳴值解鎖敘事生成（issue #25）。

當玩家與某個城市靈魂的共鳴值跨過 10／40／100 任一門檻時，由身體（S5
`resonance.py`）呼叫這支模組，生成該階段的解鎖短故事。

## 呼叫順序硬規則（v2.1 §6.4）：先寫資料庫，再呼叫腦袋

這支模組**不寫入任何資料表**，`stage` 完全由呼叫端（身體，已經呼叫過
`apply_resonance` 並拿到 `newly_unlocked_stages`）決定。呼叫順序顛倒的話
——先問腦袋「現在是第幾階」再寫資料庫——腦袋看到的 `stage` 可能跟資料庫
最終落地的值不一致（例如同時有兩筆事件入帳）。這支函式因此故意不去查
`resonance` 表自己算 stage，只信任呼叫端傳進來的值。

## 一次跨多個門檻時，每個 stage 都要生成

`apply_resonance`（issue #16）的 `newly_unlocked_stages` 刻意回傳 list，
就是為了「一次入帳跨過兩個門檻」這種情況（例如 5 → 55，跨過 10 與 40）。
只取最後一個會讓中間那段故事**靜默消失**，玩家永遠不會知道自己其實跨過了
兩個階段。`generate_unlock_stories()` 對 list 裡每個 stage 各呼叫一次，
呼叫端不需要自己寫迴圈。
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from app.modules.brain.gemini import FALLBACK_REPLY as _GEMINI_CLIENT_FALLBACK
from app.modules.brain.gemini import GeminiClient

# 三個階段的關係遞進描述（issue #25 AC：內容依 stage 不同而不同，且要能
# 反映階段遞進）。文案本身待敘事負責人審核，這裡先用簡單但方向正確的描述，
# 跟 B4／B5 遇到同樣情況時的做法一致。
_STAGE_DESCRIPTIONS: dict[int, str] = {
    1: "玩家剛建立起初步的信任，還只是個剛認識不久的訪客",
    2: "玩家已經來過好幾次，開始願意分享比較深的心事，關係比初識更近一層",
    3: "玩家與這個靈魂已經是走過漫長時間累積下來的知交，是三個階段裡最深的關係",
}

# 生成失敗、或 Gemini 回傳它自己的對話語境回退句時，換成的解鎖敘事專用回退
# 文字（issue #25 AC3）。理由同 daily_event.py：對話用的回退句套進「解鎖了
# 一段新故事」這個語境會文不對題。
_FALLBACK_STORY_TEXT = "這段緣分還在繼續累積……這次沒能捕捉到完整的故事，但下次見面，它還在那裡。"


class UnlockStory(BaseModel):
    stage: int
    story_text: str


def _build_prompt(spirit_id: str, stage: int) -> str:
    description = _STAGE_DESCRIPTIONS.get(stage, f"玩家剛跨過第 {stage} 個共鳴階段")
    return (
        f"你是「{spirit_id}」這個地標的城市靈魂。玩家剛跨過共鳴值的第 {stage} 個門檻，"
        f"關係階段是：{description}。\n"
        "請用兩三句話寫一段符合這個階段、屬於這次解鎖的短故事，語氣要符合角色設定，"
        "不要條列、不要開場白或結語，要能讓玩家感覺到跟前面階段不一樣。"
    )


def generate_unlock_story(
    player_id: str, spirit_id: str, stage: int, client: GeminiClient
) -> UnlockStory:
    """
    產生單一階段的解鎖故事（issue #25 AC1）。

    `stage` 完全由呼叫端決定，這支函式不查資料庫、不自己重算——見模組開頭
    「呼叫順序硬規則」的說明。
    """
    prompt = _build_prompt(spirit_id, stage)
    text = client.generate(prompt)

    if text == _GEMINI_CLIENT_FALLBACK:
        text = _FALLBACK_STORY_TEXT

    return UnlockStory(stage=stage, story_text=text)


def generate_unlock_stories(
    player_id: str, spirit_id: str, stages: Sequence[int], client: GeminiClient
) -> list[UnlockStory]:
    """
    對 `newly_unlocked_stages`（issue #16 `ResonanceResult`）裡每個 stage
    各生成一段故事（issue #25 AC4）。空 list 回傳空 list，不是每次入帳都
    一定有新故事——沒跨過門檻時本來就不該有東西可以生成。
    """
    return [generate_unlock_story(player_id, spirit_id, stage, client) for stage in stages]
