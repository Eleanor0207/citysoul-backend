"""
任務完成的敘事包裝（#43，SDD §7.5 後半）。

任務完成之後，靈魂對這件事說的一句話——把「你完成了任務」這個系統事件
包裝成角色會說的話。

⚠️ **票上把這支歸類為 B2**，但它刻意不放進 `prompt_builder.py`：那支模組
（#12）的職責是「把人格卡＋記憶＋玩家輸入組成對話用的 System Instruction
與 User Turn」，是對話流程的組裝器。任務包裝是另一種生成任務，塞進去只會
讓那支模組同時有兩個不相干的責任。放在這裡，跟 `unlock_story.py`（B11）、
`daily_event_content.py`（B9）同一個層級與寫法。

生成失敗回退人工預寫台詞，**不阻擋任務完成**——任務已經完成了、共鳴值也
入帳了，包裝失敗不能讓玩家的進度消失。
"""
from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient, VertexAIGeminiClient

# 生成失敗時的人工預寫包裝。刻意不跟 B1 的 `FALLBACK_REPLY` 共用——那句是
# 為了「我不知道怎麼回答」寫的，用來當任務完成的祝賀會很突兀。
_FALLBACK_WRAPPER = "（城市靈魂輕輕點了點頭。）你做到了。這座城市又多記得你一點。"

_PROMPT_TEMPLATE = (
    "你是這座地標的城市靈魂。這位旅人剛完成了任務「{quest_id}」。\n"
    "請用一到兩句話，以角色的口吻回應他完成任務這件事。"
    "語氣自然、貼近角色，不要條列，不要提到「任務」「系統」「共鳴值」這些系統用詞。"
)


def generate_quest_wrapper(
    player_id: str,
    quest_id: str,
    *,
    gemini_client: GeminiClient | None = None,
) -> str:
    """
    產生任務完成的包裝台詞。**永遠回傳非空字串**，永遠不拋例外。

    `player_id` 收在簽章裡（票上指定的介面）但刻意不進 prompt，理由同
    `unlock_story.generate_unlock_story`：包裝內容取決於完成了什麼，不取決
    於玩家是誰，送玩家識別碼進模型只是多一份沒必要外流的資料。
    """
    client = gemini_client or VertexAIGeminiClient()
    prompt = _PROMPT_TEMPLATE.format(quest_id=quest_id)

    try:
        text = client.generate(prompt)
    except Exception:  # noqa: BLE001
        # 契約上 `generate` 不拋例外，但呼叫端可以注入任何實作——不賭別人
        # 都遵守契約（同 unlock_story 的處理）。
        return _FALLBACK_WRAPPER

    if not text or not text.strip() or text == FALLBACK_REPLY:
        return _FALLBACK_WRAPPER

    return text
