"""
取一隻靈魂的嗓音（0031）。

正本是 `brain.character_voices`，由 `content/spirits.yaml` 的 `voice:` 區塊經
`scripts/import_spirits.py` 匯入。性別的判定依據是美術做出來的 3D 模型——
人格卡一律用「祂」，寫的是地方不是人，那裡沒有性別資訊。

## 查不到不是錯誤

這張表允許缺列：新靈魂還沒配音是正常狀態。查不到就回 `None`，呼叫端退回
`settings.tts_voice_name`（目前是 None，等於讓 Google 依語言自己挑），
也就是這個模組存在之前的行為。

## 為什麼不快取

這是同一個 request 已經開著的 session 上的一次主鍵查詢，跟同一支端點裡的
Gemini 呼叫（數百毫秒）比可以忽略。過早加快取會讓「改了 YAML、重新匯入之後
聲音沒變」變成一個要查的問題——而調音本來就是要反覆重跑匯入的。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.brain.tts import VoiceProfile

logger = logging.getLogger(__name__)

_QUERY = text(
    """
    SELECT v.voice_name, v.speaking_rate, v.pitch
    FROM spirits s
    JOIN brain.character_voices v ON v.character_id = s.character_id
    WHERE s.spirit_id = :spirit_id
    """
)


def for_spirit(db: Session, spirit_id: str) -> VoiceProfile | None:
    """這隻靈魂的嗓音；沒有配音就回 None。"""
    if not spirit_id:
        return None

    row = db.execute(_QUERY, {"spirit_id": spirit_id}).first()
    if row is None:
        # info 而不是 warning：還沒配音是預期中的狀態，不是要有人去處理的事。
        logger.info("%s 沒有配音，這次用預設嗓音", spirit_id)
        return None

    return VoiceProfile(
        name=row.voice_name,
        speaking_rate=float(row.speaking_rate),
        pitch=float(row.pitch),
    )
