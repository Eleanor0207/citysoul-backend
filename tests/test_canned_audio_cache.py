"""
B12 預寫台詞的語音快取（`app/modules/body/canned_audio.py`）。

固定字串的音檔逐字相同，沒有理由每一輪重新合成——合成是串在生成之後的，
2026-08-24 實測佔一輪對話的 34%。
"""
import uuid

import pytest

from app.core.redis_client import redis_client
from app.modules.body import canned_audio
from app.modules.brain.tts import FakeTTSClient, TTSResult, VoiceProfile

_TEXT = "你來了。今晚雲不多。"


@pytest.fixture
def spirit_id():
    value = f"perf-canned-{uuid.uuid4().hex[:8]}"
    yield value
    for key in redis_client.scan_iter(f"tts:canned:{value}:*"):
        redis_client.delete(key)


def test_second_call_does_not_synthesize_again(spirit_id):
    tts = FakeTTSClient(TTSResult(audio_url="https://example.test/a.mp3"))

    first = canned_audio.synthesize_canned(tts, _TEXT, spirit_id=spirit_id)
    second = canned_audio.synthesize_canned(tts, _TEXT, spirit_id=spirit_id)

    assert first.audio_url == second.audio_url
    assert tts.call_count == 1


def test_a_different_voice_is_a_different_cache_entry(spirit_id):
    """
    🔒 嗓音整組進 key。

    `cmn-TW` 只有三個講者，靈魂之間的差異有一半靠語速與音高做出來。只用名稱
    當 key 的話，兩隻共用同一把嗓音、只有語速不同的靈魂會互相拿到對方的音檔。
    """
    tts = FakeTTSClient(TTSResult(audio_url="https://example.test/a.mp3"))
    slow = VoiceProfile(name="cmn-TW-Wavenet-B", speaking_rate=0.9, pitch=-2.0)
    fast = VoiceProfile(name="cmn-TW-Wavenet-B", speaking_rate=1.1, pitch=-2.0)

    canned_audio.synthesize_canned(tts, _TEXT, spirit_id=spirit_id, voice=slow)
    canned_audio.synthesize_canned(tts, _TEXT, spirit_id=spirit_id, voice=fast)

    assert tts.call_count == 2


def test_failed_synthesis_is_not_cached(spirit_id):
    """
    🔒 一次合成失敗不該被記住一小時。

    失敗回 None 是 B10 的既有契約（語音是加分項）；把 None 寫進快取的話，
    一次網路抖動會讓這隻靈魂的招呼整整一小時沒有聲音。
    """
    failing = FakeTTSClient(None)

    assert canned_audio.synthesize_canned(failing, _TEXT, spirit_id=spirit_id) is None

    working = FakeTTSClient(TTSResult(audio_url="https://example.test/a.mp3"))
    result = canned_audio.synthesize_canned(working, _TEXT, spirit_id=spirit_id)

    assert result is not None
    assert working.call_count == 1
