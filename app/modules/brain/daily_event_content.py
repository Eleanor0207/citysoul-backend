"""
B9．當日情境內容生成（#20，SDD v2.1 §6.4）。

**只做生成，不管排程／快取／對外 serve**——那些拆給 S10（#26）：排程什麼
時候產生、對外 API 怎麼 serve，都不是這支模組的事。這裡刻意**沒有資料庫
參數**，是純函式，輸入輸出之外不碰任何持久化寫入。

輸入來源限定三類（v2.1 §6.4）：日期／節日、地標官方公開活動、人工審核
素材。**不接受**即時天氣、社群內容或任何其他未經審核的外部來源——早期
原型把天氣、氣溫餵進過 prompt，那已經超出 SDD 允許的輸入清單，這裡不
重蹈覆轍。三類輸入實際上哪裡來（真正的節日判定、官方活動去哪抓）是呼叫端
（#26）的事，這支函式只接已經準備好的內容，不自己伸手去抓。
"""
from datetime import date
from typing import Literal, Sequence

from pydantic import BaseModel

from app.modules.brain.gemini import GeminiClient, VertexAIGeminiClient

# 三類輸入全部缺席時的回退。跟 B1 的 `FALLBACK_REPLY` 是不同的話，刻意
# 各自持有：那句話回答的是「對話生成失敗」，這句話回答的是「今天沒有
# 值得說的當日情境」，語境不同，之後也會因為不同理由被改寫。
_NO_QUALIFYING_INPUT_FALLBACK = "（城市靈魂靜靜守著這裡，今天沒有特別想說的事，但很高興你來了。）"

_PROMPT_TEMPLATE = (
    "你是「{place_id}」的城市靈魂，請用兩三句話描述今天（{event_date}）"
    "在這裡的當日情境。\n{context}\n"
    "語氣自然、貼近角色，不要條列，不要提到「AI」或「生成」。"
)


class DailyEventContent(BaseModel):
    """
    B9 的輸出。`source` 區分內容從何而來——跟 `schemas.DialogueResponse.source`
    同一個精神，方便觀察命中率，呼叫端不需要據此改變行為。
    """

    place_id: str
    event_date: date
    narrative_text: str
    source: Literal["generated", "fallback"]


def generate_daily_event_content(
    place_id: str,
    event_date: date,
    *,
    festival_name: str | None = None,
    official_events: Sequence[str] = (),
    reviewed_material: Sequence[str] = (),
    gemini_client: GeminiClient | None = None,
) -> DailyEventContent:
    """
    三類輸入（節日、官方活動、人工審核素材）全部缺席時，直接回退，
    **不呼叫 Gemini**——沒有合格輸入時仍放模型自由發揮，等於讓沒審核過的
    內容進到玩家眼前，違反這張票的輸入限定，也白花一次呼叫成本。

    輸入齊全時才呼叫 Gemini；`GeminiClient.generate()` 自己保證永遠不拋
    例外、永遠回傳非空字串（見 `gemini.py` 模組 docstring），所以「Gemini
    呼叫失敗」的回退完全由 B1 自己吸收，這裡不需要再包一層 try/except。
    """
    context = _render_context(festival_name, official_events, reviewed_material)
    if context is None:
        return DailyEventContent(
            place_id=place_id,
            event_date=event_date,
            narrative_text=_NO_QUALIFYING_INPUT_FALLBACK,
            source="fallback",
        )

    client = gemini_client or VertexAIGeminiClient()
    prompt = _PROMPT_TEMPLATE.format(place_id=place_id, event_date=event_date, context=context)

    return DailyEventContent(
        place_id=place_id,
        event_date=event_date,
        narrative_text=client.generate(prompt),
        source="generated",
    )


def _render_context(
    festival_name: str | None,
    official_events: Sequence[str],
    reviewed_material: Sequence[str],
) -> str | None:
    """把三類輸入拼成給 Gemini 的參考資料。三類都空時回 `None`（無合格輸入）。"""
    lines: list[str] = []
    if festival_name:
        lines.append(f"今天是：{festival_name}")
    if official_events:
        lines.append("官方公開活動：" + "、".join(official_events))
    if reviewed_material:
        lines.append("可參考的審核素材：" + "、".join(reviewed_material))

    return "\n".join(lines) if lines else None
