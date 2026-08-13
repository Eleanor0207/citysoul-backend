"""中文文字正規化。**只做一件事，而且刻意只做這一件。**

放在 `app/core/` 而不是 `scripts/`，因為使用它的不只是匯入腳本——`gemini.py`
也用同一支處理模型輸出。`app` 反過來 import `scripts` 會讓相依方向倒過來：
腳本可以依賴應用程式，應用程式不該依賴腳本。

## 問題

YAML 的折行語法（`>-`）把換行接成空格，這對英文是對的，對中文不是：

    archetype: >-
      看過這條街三百年來反覆毀壞又重建的守望者。
      1815 年的地震、1867 年的暴風雨都拆過這裡。

載入後會變成「……守望者。 1815 年的地震」——句號後面多一個空格。研究檔那邊的
多行引言（`parse_blockquote` 用 `" ".join()` 接行）也有同樣的狀況。

不影響語意，模型讀得懂，但它是雜訊，而且會一路帶進每一次 prompt。

## 規則：兩邊都是中日韓字元時，才移除中間的空白

`1945 年` 的空格要留著——那是數字與中文之間的排版慣例，寫的人是刻意打的。
只有「中文 空格 中文」這種組合才是折行造成的。

補充一條：**中文標點之後的空格一律移除**，不看右邊是什麼。「守望者。 1815 年」
的空格同樣來自折行，但右邊是數字，雙邊規則抓不到它。而中文的句號、逗號、破折號
後面本來就不該有空格，所以這一條不會誤傷任何刻意的排版。

這條規則窄到可以一句話講完，是刻意的：匯入器動內容是危險的事，動得愈少愈能
確定它不會改變意思。**不做全形轉半形、不修剪標點、不合併重複空白**——那些都
是「順手做一下」很誘人但會改變作者原意的操作。
"""
from __future__ import annotations

import re

# 中日韓統一表意文字 + 常用中文標點（。，、；：？！（）「」『』——…）
_CJK = (
    r"一-鿿"      # 中日韓統一表意文字
    r"㐀-䶿"      # 擴充 A
    r"　-〿"      # 中日韓符號與標點
    r"＀-￯"      # 全形字元
    r"—…"        # 破折號與刪節號：常用於中文，但碼位在 General Punctuation
)

# 中文標點之後永遠不該有空格。這一條跟上面那條的差別是**不看右邊是什麼**——
# 「守望者。 1815 年」的空格同樣是折行造成的，但右邊是數字，靠雙邊規則抓不到。
_AFTER_PUNCT = "。，、；：？！）」』】》—…"

_FOLD_SPACE = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_PUNCT_SPACE = re.compile(rf"(?<=[{_AFTER_PUNCT}])[ \t]+")


def strip_fold_spaces(value):
    """
    遞迴處理字串／list／dict，移除中日韓字元之間的空白。

    非字串原樣回傳——`None`、布林、數字、巢狀結構都不該因為經過這裡而改變型別。
    """
    if isinstance(value, str):
        return _PUNCT_SPACE.sub("", _FOLD_SPACE.sub("", value))
    if isinstance(value, list):
        return [strip_fold_spaces(v) for v in value]
    if isinstance(value, dict):
        return {k: strip_fold_spaces(v) for k, v in value.items()}
    return value
