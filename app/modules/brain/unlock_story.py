"""
B11．共鳴值解鎖敘事生成（#25）。

共鳴值跨過 10/40/100 任一門檻時，由身體呼叫這裡生成該階段的解鎖短故事。

**呼叫順序硬規則（v2.1 §6.4）**：身體必須「先寫完自己的 `resonance` 表、
再呼叫腦袋」，不可顛倒——否則這裡拿到的 `stage` 會跟資料庫不一致，玩家
看到的故事會對不上他實際的關係階段。這支模組本身沒有 `db` 參數，正是這條
規則的體現：它拿不到資料庫，也就不可能自己去讀一個可能還沒寫完的值。

生成失敗時回退人工預寫台詞，**不阻擋任務完成流程**——解鎖敘事是獎勵，
不是任務完成的必要條件。
"""
from pydantic import BaseModel

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient, VertexAIGeminiClient

# 每個階段的關係定調。三段刻意寫成遞進的關係深度——如果三個 stage 生出
# 一樣的 prompt，解鎖就失去意義了：玩家會發現自己三次拿到同一個故事。
_STAGE_BRIEFS = {
    1: "你剛開始注意到這位旅人，願意多說一點這座地標的故事。語氣是初識的友善距離感。",
    2: "這位旅人已經來過幾次，你開始把他當作熟識的人，願意分享一些比較個人的觀察與記憶。",
    3: "這位旅人是你真正的老朋友了，你願意說出最深的、平常不對外人說的心事與眷戀。",
}

# 每個階段各自的人工預寫回退台詞。刻意不共用一句——回退的時候玩家仍然
# 應該感覺到「這是第幾階段的解鎖」，用同一句話會讓三次解鎖看起來一樣，
# 那正是這張票要避免的事。
_STAGE_FALLBACKS = {
    1: "（城市靈魂看了你一眼，像是記住了你的臉。）你又來了。這裡的故事，我慢慢說給你聽。",
    2: "（城市靈魂的語氣柔和了些。）你來過幾次了吧。有些事，我只對常來的人說。",
    3: "（城市靈魂沉默了一會兒。）你已經是這裡的一部分了。有些話我放在心裡很久，今天想說給你聽。",
}

_UNKNOWN_STAGE_FALLBACK = "（城市靈魂靜靜地看著你，像是還在想該從哪裡說起。）"

_PROMPT_TEMPLATE = (
    "你是「{spirit_id}」的城市靈魂。這位旅人與你的共鳴值剛跨過第 {stage} 階段的門檻。\n"
    "{stage_brief}\n"
    "請用三到四句話，寫一段屬於這個階段的解鎖短故事，讓旅人感覺到關係的推進。"
    "語氣自然、貼近角色，不要條列，不要提到「階段」「共鳴值」這些系統用詞。"
)


class UnlockStory(BaseModel):
    """
    解鎖敘事。`source` 區分內容是生成的還是回退的，方便觀察生成成功率；
    呼叫端不需要據此改變行為——兩種情況對玩家來說都是一段解鎖故事。
    """

    stage: int
    story_text: str
    source: str  # "generated" / "fallback"


def generate_unlock_story(
    player_id: str,
    spirit_id: str,
    stage: int,
    *,
    gemini_client: GeminiClient | None = None,
) -> UnlockStory:
    """
    生成單一階段的解鎖短故事。

    `player_id` 收在簽章裡（SDD 指定的介面）但**刻意不進 prompt**：故事的
    內容取決於「關係到了第幾階段」，不取決於「這個玩家是誰」，而把玩家識別
    碼送進模型只會多一份沒有必要外流的資料。之後若要做個人化（例如帶入
    長期記憶），那是把記憶內容送進去，仍然不需要送 id 本身。

    任何失敗都回退到該階段的人工預寫台詞，不拋例外——解鎖敘事生不出來
    不該讓玩家的任務完成請求跟著失敗。

    `GeminiClient.generate()` 契約上不拋例外、失敗時回傳 `FALLBACK_REPLY`
    （見 `gemini.py`），所以這裡有兩道回退：
    1. 回傳值等於 B1 的 `FALLBACK_REPLY` → 生成其實失敗了，換成**這個階段**
       的回退台詞。直接把 B1 那句對話用的話當成解鎖故事會很突兀，它是為了
       「我不知道怎麼回答」寫的，不是為了「你跟我更熟了」。
    2. `try/except` 兜底：契約說不會拋，但呼叫端可以注入任何 `GeminiClient`
       實作，這裡不賭別人都遵守契約。
    """
    stage_brief = _STAGE_BRIEFS.get(stage)
    if stage_brief is None:
        # 未知階段（門檻改了但這裡沒跟上）——回退而不是硬湊一個 prompt。
        return UnlockStory(
            stage=stage, story_text=_UNKNOWN_STAGE_FALLBACK, source="fallback"
        )

    client = gemini_client or VertexAIGeminiClient()
    prompt = _PROMPT_TEMPLATE.format(
        spirit_id=spirit_id, stage=stage, stage_brief=stage_brief
    )

    try:
        text = client.generate(prompt)
    except Exception:  # noqa: BLE001
        return UnlockStory(stage=stage, story_text=_fallback_for(stage), source="fallback")

    if not text or not text.strip() or text == FALLBACK_REPLY:
        return UnlockStory(stage=stage, story_text=_fallback_for(stage), source="fallback")

    return UnlockStory(stage=stage, story_text=text, source="generated")


def generate_unlock_stories(
    player_id: str,
    spirit_id: str,
    stages: list[int],
    *,
    gemini_client: GeminiClient | None = None,
) -> list[UnlockStory]:
    """
    一次跨多個門檻時，每個 stage 各生成一段。

    ⚠️ 這支存在的理由就是 #16 刻意讓 `newly_unlocked_stages` 回傳 **list**
    的那個理由：只取最後一個 stage 會讓中間那段故事**靜默消失**，沒有任何
    錯誤訊息會提醒。呼叫端（#43）直接用這支，就不會有人不小心寫成
    `stages[-1]`。
    """
    return [
        generate_unlock_story(player_id, spirit_id, stage, gemini_client=gemini_client)
        for stage in stages
    ]


def _fallback_for(stage: int) -> str:
    return _STAGE_FALLBACKS.get(stage, _UNKNOWN_STAGE_FALLBACK)
