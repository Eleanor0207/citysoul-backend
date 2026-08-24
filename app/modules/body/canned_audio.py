"""
B12 預寫台詞的語音快取。

## 為什麼只快取預寫台詞

`/dialogue` 的 TTS 合成是**串行**的：等 Gemini 回完才開始，實測佔一輪對話
的 34%（2026-08-24 量測）。生成出來的台詞每次都不一樣，快取永遠不會命中；
**預寫招呼與保底句是固定字串**，同一隻靈魂唸出來的音檔逐字相同，沒有理由
每次都重新合成。

## 為什麼是 Redis 而不是一張表

存的是**簽章 URL**，本來就會過期（`tts_audio_url_ttl_seconds`）。有到期時間
的東西放在有 TTL 的地方，過期就自己不見——存進資料庫的話，「這個 URL 還能不能
用」要另外寫一段判斷，而那段判斷會跟簽章的有效期各自漂移。

TTL 比 URL 有效期短一截：剛好在到期邊緣才命中的話，玩家會拿到一個播到一半
就失效的連結。

## 快取的是 URL，不是音檔

音檔在 GCS，這裡只記「那段字對上哪個 URL」。Redis 掉了最壞的結果是重新合成
一次，不是玩家沒有聲音。
"""
import hashlib
import logging

from app.core.config import settings
from app.core.redis_client import redis_client
from app.modules.brain.tts import TTSClient, TTSResult, VoiceProfile

logger = logging.getLogger(__name__)

# 簽章 URL 的有效期減去這一段，當作快取的存活時間。
_EXPIRY_MARGIN_SECONDS = 300


def _key(spirit_id: str, text: str, voice: VoiceProfile | None) -> str:
    # 嗓音整組進 key：`cmn-TW` 只有三個講者，靈魂之間的差異有一半靠語速與
    # 音高做出來（見 VoiceProfile 的說明）。只用名稱當 key 的話，兩隻共用同一
    # 把嗓音、只有語速不同的靈魂會互相拿到對方的音檔。
    if voice is None:
        voice_key = "default"
    else:
        voice_key = f"{voice.name}:{voice.speaking_rate}:{voice.pitch}"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"tts:canned:{spirit_id}:{voice_key}:{digest}"


def synthesize_canned(
    tts: TTSClient,
    text: str,
    *,
    spirit_id: str,
    voice: VoiceProfile | None = None,
) -> TTSResult | None:
    """
    合成一句**固定字串**的語音，命中快取就不呼叫 TTS。

    介面與 `TTSClient.synthesize` 一致：失敗回 None，不拋例外。Redis 出問題
    時安靜地退回直接合成——快取壞掉不該讓玩家沒有聲音。
    """
    ttl = settings.tts_audio_url_ttl_seconds - _EXPIRY_MARGIN_SECONDS
    if ttl <= 0:
        # URL 的有效期短到扣掉緩衝就沒了，快取沒有意義。
        return tts.synthesize(text, voice=voice)

    key = _key(spirit_id, text, voice)

    try:
        cached = redis_client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("預寫台詞語音快取讀取失敗，改為直接合成：%s", exc)
        return tts.synthesize(text, voice=voice)

    if cached:
        return TTSResult(audio_url=cached)

    result = tts.synthesize(text, voice=voice)
    if result is None:
        # 合成失敗不寫快取——否則一次失敗會被記住一小時。
        return None

    try:
        redis_client.set(key, result.audio_url, ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("預寫台詞語音快取寫入失敗（音檔仍然可用）：%s", exc)

    return result
