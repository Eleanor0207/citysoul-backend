"""
每隻靈魂一把嗓音（0031）：`brain.character_voices` 與 `voices.for_spirit`。

這裡**不斷言九隻靈魂各自配到哪一把**——那是內容不是行為，鎖進測試只會讓
調音變成改測試。這個檔案驗的是機制：查得到、查不到、以及超範圍的值進不去。
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.brain import voices
from app.modules.brain.tts import VoiceProfile


@pytest.fixture
def voiced_spirit(db_session):
    """
    建一隻自己的靈魂並配音，測完清掉。

    用獨立的 id 而不是既有的龍山寺：嗓音是會被調的內容，把測試綁在正式資料上
    等於每次調音都要跟著改測試。
    """
    suffix = uuid.uuid4().hex[:8]
    landmark_id = f"test-landmark-{suffix}"
    character_id = f"test-char-{suffix}"
    spirit_id = landmark_id  # spirit_id 就是 landmark_id（見 content/spirits.yaml）

    db_session.execute(
        text(
            "INSERT INTO brain.landmark_souls (landmark_id, city_id, name, founding_facts)"
            " VALUES (:id, 'taipei', :name, '{}'::jsonb)"
        ),
        {"id": landmark_id, "name": "測試地標"},
    )
    db_session.execute(
        text(
            "INSERT INTO brain.characters (character_id, landmark_id) "
            "VALUES (:cid, :lid)"
        ),
        {"cid": character_id, "lid": landmark_id},
    )
    db_session.execute(
        text(
            "INSERT INTO spirits (spirit_id, display_name, character_id, landmark_id,"
            " latitude, longitude, summon_radius_meters, sense_radius_meters,"
            " is_active, safety_gate_enabled)"
            " VALUES (:sid, :name, :cid, :lid, 25.0, 121.5, 50, 150, true, false)"
        ),
        {"sid": spirit_id, "name": "測試靈魂", "cid": character_id, "lid": landmark_id},
    )
    db_session.commit()

    yield spirit_id, character_id

    db_session.rollback()
    db_session.execute(
        text("DELETE FROM brain.character_voices WHERE character_id = :cid"),
        {"cid": character_id},
    )
    db_session.execute(text("DELETE FROM spirits WHERE spirit_id = :sid"), {"sid": spirit_id})
    db_session.execute(
        text("DELETE FROM brain.characters WHERE character_id = :cid"), {"cid": character_id}
    )
    db_session.execute(
        text("DELETE FROM brain.landmark_souls WHERE landmark_id = :lid"), {"lid": landmark_id}
    )
    db_session.commit()


def _set_voice(db_session, character_id, name, rate, pitch):
    db_session.execute(
        text(
            "INSERT INTO brain.character_voices"
            " (character_id, voice_name, speaking_rate, pitch)"
            " VALUES (:cid, :name, :rate, :pitch)"
        ),
        {"cid": character_id, "name": name, "rate": rate, "pitch": pitch},
    )
    db_session.commit()


def test_for_spirit_returns_the_configured_voice(db_session, voiced_spirit):
    spirit_id, character_id = voiced_spirit
    _set_voice(db_session, character_id, "cmn-TW-Wavenet-C", 0.88, -2.0)

    profile = voices.for_spirit(db_session, spirit_id)

    assert profile == VoiceProfile(name="cmn-TW-Wavenet-C", speaking_rate=0.88, pitch=-2.0)


def test_a_spirit_without_a_voice_is_not_an_error(db_session, voiced_spirit):
    """
    🔒 沒配音回 None，不丟例外。

    新靈魂還沒配音是正常狀態，而 TTS 這條路徑的既定原則是缺件安靜降級——
    這裡丟例外會讓整支對話端點 500，只因為某隻靈魂還沒挑好聲音。
    """
    spirit_id, _character_id = voiced_spirit

    assert voices.for_spirit(db_session, spirit_id) is None


def test_an_unknown_spirit_is_not_an_error(db_session):
    assert voices.for_spirit(db_session, "no-such-spirit") is None


def test_empty_spirit_id_is_not_an_error(db_session):
    assert voices.for_spirit(db_session, "") is None


@pytest.mark.parametrize(
    ("rate", "pitch"),
    [
        (0.1, 0.0),    # rate 低於 0.25
        (5.0, 0.0),    # rate 高於 4.0
        (1.0, -25.0),  # pitch 低於 -20
        (1.0, 25.0),   # pitch 高於 +20
    ],
)
def test_out_of_range_values_are_rejected_by_the_database(db_session, voiced_spirit, rate, pitch):
    """
    🔒 超出 Google API 合法範圍的值寫不進去。

    這個約束在資料庫層而不是只在匯入腳本裡，因為超範圍的值會讓 TTS 在
    **執行期**失敗，而 TTS 失敗是安靜降級成純文字的——沒有人會發現聲音不見了。
    """
    _spirit_id, character_id = voiced_spirit

    with pytest.raises(IntegrityError):
        _set_voice(db_session, character_id, "cmn-TW-Wavenet-A", rate, pitch)

    db_session.rollback()
