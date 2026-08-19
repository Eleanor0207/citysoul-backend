"""
B9．當日情境內容生成（issue #20）。

「今天這個地標有什麼不一樣」的一小段敘事，讓玩家每天打開都看到新東西。

## 只做生成，不碰排程／快取／資料庫

排程、`daily_event_cache` 的讀寫、對外 serve 全部屬 S10（#26），刻意拆開
（v2.1 §6.4）。所以這個模組**沒有 DB session 參數，也沒有任何寫入操作**——
不是「還沒做」，是它不該做。

這條邊界由測試守著：一旦有人在這裡加了 session 參數，模組邊界就開始溶解，
而「內容生成」與「什麼時候生成、存在哪」會變成同一團東西。

## 輸入來源是白名單，不是黑名單

SDD 允許的輸入**只有三類**：

1. 日期／節日
2. 地標官方公開活動
3. 人工審核素材

⚠️ **不得**使用即時天氣 API、社群內容，或任何未經審核的外部來源。

這不是假設性的規則。曾有原型（`docs/aistudio-ar`）把天氣／氣溫／空氣品質餵進
prompt，那超出 SDD 允許的清單。要納入天氣得先修改 SDD，不是在這裡偷渡——
所以有一條測試直接掃這個模組的原始碼，確認沒有任何對外抓資料的呼叫。

## 無合格輸入不是錯誤

大多數日子既不是節日、也沒有官方活動、也沒有人工素材。那是**常態**，不是缺漏。
回退人工預寫台詞，讓玩家仍然看到東西——「今天沒素材」不是可接受的畫面。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient
from app.modules.brain.historical_boundary import get_historical_boundary_rules

logger = logging.getLogger(__name__)

# 🔒 文案審核狀態，與 B5／B4／B11 同一個慣例。
CONTENT_REVIEW_STATUS = "PENDING_NARRATIVE_REVIEW"


@dataclass(frozen=True)
class DailyEventContent:
    """
    當日情境。`narrative_text` 是要注入 B2 User Turn 第一段的那段文字。

    `is_fallback` 供觀察生成成功率用。**不建議**用它改變玩家看到的東西——
    回退台詞本來就寫得像角色會說的話。

    `sources` 記錄這次用了哪些合格輸入。它存在的理由是**可稽核**：哪天有人
    懷疑內容摻了不該有的來源，這裡看得出來。
    """

    place_id: str
    event_date: date
    narrative_text: str
    is_fallback: bool = False
    sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DailyEventInputs:
    """
    合格輸入。**三類，就這三類**（SDD）。

    做成明確的資料結構而不是散落的參數，是為了讓「允許哪些輸入」在型別上就
    看得見。要加第四類就得改這裡，而那會出現在 code review 的 diff 裡——
    不像多塞一個字串進 prompt 那樣容易溜過去。

    由呼叫端（S10 / #26）負責填。這個模組**不自己去抓任何資料**。
    """

    # 1. 日期／節日，例如「中元節」。
    festival: str | None = None
    # 2. 地標官方公開活動，例如「安太歲法會」。
    official_events: list[str] = field(default_factory=list)
    # 3. 人工審核素材：敘事負責人預先寫好、審核過的片段。
    curated_notes: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """三類都沒有東西——這是常態，不是錯誤。"""
        return not (self.festival or self.official_events or self.curated_notes)

    def describe(self) -> list[str]:
        """列出這次實際用到的來源，供稽核。"""
        used = []
        if self.festival:
            used.append("festival")
        if self.official_events:
            used.append("official_events")
        if self.curated_notes:
            used.append("curated_notes")
        return used


# 人工預寫的回退台詞，**每個地標一句**。
#
# 刻意寫得像「今天很平常」而不是「系統沒有資料」——大多數日子本來就很平常，
# 而玩家不需要知道我們的內容管線今天是空的。
#
# ## 為什麼要分地標
#
# 原本只有一句共用台詞，裡面寫的是「香火照舊」。那對龍山寺與城隍廟成立，套在
# 天文館或美術館上就是玩家一眼看得出來的錯。而回退是**大多數日子**都會走到的
# 路徑，不是稀有的例外——玩家看到這句話的次數比看到生成內容還多。
#
# ## 為什麼留在程式碼裡
#
# 不搬進 `content/`，是為了守住這個模組「不自己去抓資料」的邊界（見模組
# docstring）。要讓敘事負責人自己維護的話，正確的做法是由呼叫端（S10 / #26）
# 當成第三類合格輸入 `curated_notes` 餵進來，而不是讓生成端開始讀檔。
_DEFAULT_FALLBACK_NARRATIVE = (
    "今天沒什麼特別的。人來人往，跟昨天差不多——不過每天的「差不多」其實都不太一樣。"
)

_FALLBACK_NARRATIVES: dict[str, str] = {
    "longshan_temple": (
        "今天沒什麼特別的。香照樣點著，前殿有人跪著唸完就走，"
        "廟埕的鴿子被驚起來又落回原地——每天都是這樣，可是每天來許願的人不一樣。"
    ),
    "ximen_red_house": (
        "今天沒什麼特別的。八角樓外照樣有人約在這裡碰面，等著等著就散進西門町了。"
        "我看過太多場散場，倒是每一次等人的臉都不太一樣。"
    ),
    "bopiliao_historic_block": (
        "今天沒什麼特別的。紅磚牆曬著太陽，偶爾有人舉起手機對著長廊拍一張就走。"
        "這條街廓安靜久了，安靜本身也算是一種聲音。"
    ),
    "xiahai_city_god_temple": (
        "今天沒什麼特別的。廟不大，香客進來繞一圈就出去了，"
        "隔壁迪化街的乾貨味照舊飄進來。月老那邊的紅線少了幾條——這種事天天有。"
    ),
    "taiwan_new_cultural_movement_memorial": (
        "今天沒什麼特別的。展間的燈亮著，訪客不多，腳步聲在磨石子地上聽得特別清楚。"
        "這棟樓從前不是給人安靜走路用的，現在是了。"
    ),
    "national_palace_museum": (
        "今天沒什麼特別的。恆溫恆濕的空氣裡，展櫃前的人換了一批又一批。"
        "玻璃後面的東西幾百年沒變過，看它的人一直在變。"
    ),
    "taipei_astronomical_museum": (
        "今天沒什麼特別的。金黃色的圓頂照樣曬著太陽，宇宙劇場一場接一場把星空放給人看。"
        "外面天氣好不好，裡面的星星都準時升起。"
    ),
    "taipei_fine_arts_museum": (
        "今天沒什麼特別的。中山北路的車聲被擋在外面，白色的展間裡有人站著看很久，"
        "也有人走得很快。同一件作品，快慢之間差的是一整個下午。"
    ),
    "moca_taipei": (
        "今天沒什麼特別的。這棟老校舍的長廊還是那個長廊，"
        "只是每隔一陣子，走廊盡頭的東西就換一個樣子。今天沒換，那也很好。"
    ),
    "songshan_cultural_park": (
        "今天沒什麼特別的。老菸廠的巴洛克花園照樣有人坐著吃午餐，倉庫的鐵門開開關關。"
        "做菸的年代早就過去了，做東西的人倒是一直在。"
    ),
}


def fallback_narrative(place_id: str) -> str:
    """
    取這個地標的回退台詞。沒有為它寫過的話，回一句中性的。

    刻意不拋例外：地標清單只會越來越長，而「新地標上線當天還沒有專屬台詞」應該
    退化成一句平淡但講得通的話，不是讓當日情境端點掛掉。少寫台詞由測試守門
    （`test_daily_event_content.py`），那是這種疏漏該被發現的地方——不是玩家的畫面。
    """
    return _FALLBACK_NARRATIVES.get(place_id, _DEFAULT_FALLBACK_NARRATIVE)


def build_daily_event_prompt(place_id: str, event_date: date, inputs: DailyEventInputs) -> str:
    """
    組出生成提示。純函式，方便直接驗證「prompt 裡只有合格輸入」。
    """
    lines = [
        f"你是地標「{place_id}」的擬人化集體意識。",
        f"今天是 {event_date.isoformat()}。",
        "",
        "以下是今天這個地方的情況：",
    ]

    if inputs.festival:
        lines.append(f"- 節日：{inputs.festival}")
    for event in inputs.official_events:
        lines.append(f"- 官方活動：{event}")
    for note in inputs.curated_notes:
        lines.append(f"- 補充：{note}")

    lines += [
        "",
        get_historical_boundary_rules(),
        "",
        "用第一人稱寫一段 40 到 80 字的短敘事，描述你今天感受到的氣氛。",
        "不要標題、不要條列、不要重複上面的條目原文。",
    ]

    return "\n".join(lines)


def fallback_content(place_id: str, event_date: date) -> DailyEventContent:
    """無合格輸入或生成失敗時的內容。永遠非空。"""
    return DailyEventContent(
        place_id=place_id,
        event_date=event_date,
        narrative_text=fallback_narrative(place_id),
        is_fallback=True,
        sources=[],
    )


def generate_daily_event_content(
    client: GeminiClient,
    *,
    place_id: str,
    event_date: date,
    inputs: DailyEventInputs | None = None,
) -> DailyEventContent:
    """
    生成當日情境。**永遠回傳非空內容，永遠不拋例外。**

    `inputs` 由呼叫端（S10 / #26）提供。這個模組不自己去抓任何資料——那正是
    §6.4 的模組邊界，也是「輸入來源限定」這條規則唯一守得住的方式：如果生成端
    自己會去抓資料，白名單就只是一句口號。
    """
    inputs = inputs or DailyEventInputs()

    if inputs.is_empty():
        # 大多數日子都會走到這裡。這是常態，用 info 而不是 warning。
        logger.info("%s 在 %s 沒有合格輸入，使用人工預寫台詞", place_id, event_date)
        return fallback_content(place_id, event_date)

    prompt = build_daily_event_prompt(place_id, event_date, inputs)

    # B1 的契約是「永遠回非空字串，失敗時回 FALLBACK_REPLY」，所以靠內容判斷
    # 是否回退，而不是 try/except——B1 不會拋例外給我們。
    text = client.generate(prompt)

    if not text or text == FALLBACK_REPLY:
        logger.info("B9 當日情境生成失敗，回退人工預寫台詞（%s %s）", place_id, event_date)
        return fallback_content(place_id, event_date)

    return DailyEventContent(
        place_id=place_id,
        event_date=event_date,
        narrative_text=text,
        sources=inputs.describe(),
    )
