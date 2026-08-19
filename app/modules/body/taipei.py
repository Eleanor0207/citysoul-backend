"""
台北時區的「今天」。

⚠️ **這個模組只有六行，但它的存在是刻意的。**

`TAIPEI` 與 `taipei_today()` 原本住在 `quests.py`，被 `quota.py` 直接 import。
那個位置在「每日任務」還是遊戲主軸時說得過去，但它們其實是三個以上模組共用的
時間基準（SDD 第7節決策8：所有「每日一次」「隔天重置」統一以 Asia/Taipei 午夜
為基準），跟任務沒有關係。

2026-08-19 共鳴值改制時，`resonance.py` 也需要「今天是哪一天」——如果繼續從
`quests.py` 拿，共鳴值就會反過來依賴任務模組，而任務模組正是這一波要縮編的
東西。所以趁這次把它搬到中性的位置。

`quests.py` 與 `quota.py` 仍然 re-export 這兩個名字，既有的 import 不會斷。
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

# SDD 第7節決策8：所有「每日一次」「隔天重置」統一以 Asia/Taipei 午夜為基準。
TAIPEI = ZoneInfo("Asia/Taipei")


def taipei_today(now: datetime) -> date:
    """把一個帶時區的時間點換算成 Asia/Taipei 的日期。"""
    return now.astimezone(TAIPEI).date()
