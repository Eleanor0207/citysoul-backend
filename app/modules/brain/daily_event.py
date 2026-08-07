"""
B9．當日情境內容生成（issue #20；CONTEXT.md「當日情境」）。

**只做生成，不管排程、快取或對外 serve**——那些是 S10（issue #26）的範圍，
v2.1 §6.4 刻意把「腦袋生成內容」跟「身體排程／快取／serve」拆開兩張票。
這支模組因此完全不碰資料庫：`generate_daily_event_content()` 沒有 `db`
參數，也沒有任何寫入操作。

## 輸入來源限定（issue #20 AC2）

CONTEXT.md「當日情境」明訂：**只**由日期／節日、地標官方公開活動、人工審核
資料構成；LLM 只能把這些受控輸入改寫成短敘事。這支模組因此只認得三種輸入：

1. `taiwan_holiday_name(event_date)`——純函式，資料是模組常數，不打任何
   外部行事曆 API。
2. `official_event`——地標官方公開活動，呼叫端傳入（未來由 #26 從審核過的
   資料表讀出）。
3. `curated_material`——人工審核素材，同上。

**這裡沒有、也絕對不該有天氣、空氣品質或社群內容的呼叫**——`docs/aistudio-ar`
的舊原型曾把天氣／氣溫餵進 prompt，那超出 SDD 允許的輸入清單。要納入天氣
得先改 SDD，不是在這支模組裡偷渡一行 API 呼叫。
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from app.modules.brain.gemini import FALLBACK_REPLY as _GEMINI_CLIENT_FALLBACK
from app.modules.brain.gemini import GeminiClient

# 只涵蓋固定西曆日期的國定假日。農曆假日（春節、端午、中秋等）因年份而異，
# 需要農曆對照表——這裡故意不做：寫死幾個示例日期換不到正確性，不如清楚
# 承認目前做不到，等真的要接農曆資料時再處理，不要用錯誤的近似值掩蓋這個缺口。
_FIXED_DATE_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "元旦",
    (2, 28): "228和平紀念日",
    (4, 4): "兒童節",
    (5, 1): "勞動節",
    (10, 10): "國慶日",
}

# 無合格輸入、或 Gemini 呼叫失敗時的回退台詞（issue #20 AC3／AC4）。
#
# 刻意**不**重用 `gemini.FALLBACK_REPLY`——那句是對話用的（「城市靈魂安靜地
# 看著你……要不要先跟我說說你眼前看到的？」），套進「今天的城市發生了什麼」
# 這個敘述語境會文不對題。跟 `router.py` 原本持有自己的 FALLBACK_REPLY 是
# 同一個理由：每個模組的回退文字要適合自己的語境，不是共用一句然後在不同
# 地方讀起來都有點怪。
_DAILY_EVENT_FALLBACK = "今天的城市，一如往常，安靜地繼續著它的故事。"


class DailyEventContent(BaseModel):
    narrative_text: str


def taiwan_holiday_name(event_date: date) -> str | None:
    """固定西曆日期的國定假日名稱；不是假日或是農曆假日（未涵蓋）則回 None。"""
    return _FIXED_DATE_HOLIDAYS.get((event_date.month, event_date.day))


def _build_prompt(
    place_id: str, event_date: date, *, holiday: str | None, official_event: str | None,
    curated_material: str | None,
) -> str:
    lines = [
        f"你是「{place_id}」這個地標的城市靈魂，請用兩三句話描述今天（{event_date.isoformat()}）"
        "的當日情境，語氣要符合角色設定，不要條列，不要加開場白或結語。",
    ]
    if holiday:
        lines.append(f"今天是{holiday}。")
    if official_event:
        lines.append(f"地標官方公開活動：{official_event}")
    if curated_material:
        lines.append(f"參考素材：{curated_material}")
    return "\n".join(lines)


def generate_daily_event_content(
    place_id: str,
    event_date: date,
    client: GeminiClient,
    *,
    official_event: str | None = None,
    curated_material: str | None = None,
) -> DailyEventContent:
    """
    產生某地標某天的當日情境敘事（issue #20 AC1）。

    三種合格輸入（節日／官方活動／人工素材）**全部缺席**時，直接回退，
    連 Gemini 都不呼叫——沒有東西可以改寫，硬送一個空 prompt 只會讓模型
    自己編造內容，那正是 CONTEXT.md 明訂要避免的「未審核即時內容」（issue
    #20 AC3）。

    `client.generate()` 本身永遠不拋例外（見 `gemini.py`），但它失敗時回傳
    的是**對話**語境的回退句，套進這裡的敘述語境不合適，所以額外判斷一次
    ——回傳值剛好等於 `gemini.FALLBACK_REPLY` 就換成這支模組自己的回退文字
    （issue #20 AC4）。
    """
    holiday = taiwan_holiday_name(event_date)

    if holiday is None and official_event is None and curated_material is None:
        return DailyEventContent(narrative_text=_DAILY_EVENT_FALLBACK)

    prompt = _build_prompt(
        place_id, event_date, holiday=holiday, official_event=official_event,
        curated_material=curated_material,
    )
    text = client.generate(prompt)

    if text == _GEMINI_CLIENT_FALLBACK:
        text = _DAILY_EVENT_FALLBACK

    return DailyEventContent(narrative_text=text)
