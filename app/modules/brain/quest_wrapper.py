"""
B2．任務完成的敘事包裝（issue #43；SDD §7.5 後半）。

把「任務完成了」這個由後端確定性規則判定的機械事實（issue #34），包成一段
符合角色語氣的短敘事文字。這支模組**不判定**任務有沒有完成——那件事已經
在 #34 做完、寫進資料庫了，這裡只負責把結果講得像角色會說的話，講失敗了
也不影響任務已經完成這個事實（見 issue #43：「敘事只是包裝，包裝失敗不能
讓玩家的進度消失」）。
"""
from app.modules.brain.gemini import FALLBACK_REPLY as _GEMINI_CLIENT_FALLBACK
from app.modules.brain.gemini import GeminiClient

# 生成失敗、或 Gemini 回傳它自己的對話語境回退句時，換成的任務包裝專用回退
# 文字——理由同 daily_event.py／unlock_story.py：對話用的回退句套進「你完成
# 了一項任務」這個語境會文不對題。
FALLBACK_WRAPPER_TEXT = "這件事，你做到了。"


def generate_quest_wrapper(player_id: str, quest_id: str, client: GeminiClient) -> str:
    """產生任務完成的敘事包裝文字（issue #43）。"""
    prompt = (
        f"你是城市靈魂，玩家剛完成了任務「{quest_id}」。"
        "請用一兩句話，以符合角色設定的語氣肯定玩家完成了這件事，"
        "不要條列、不要加開場白或結語。"
    )
    text = client.generate(prompt)

    if text == _GEMINI_CLIENT_FALLBACK:
        text = FALLBACK_WRAPPER_TEXT

    return text
