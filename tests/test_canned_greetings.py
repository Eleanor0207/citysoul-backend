"""
Ticket #13．快速問候比對層（B12）。

驗收標準對照見 GitHub issue #13。

預寫台詞的實際內容待敘事負責人審核撰寫，所以這裡全部用假資料——測的是比對
邏輯本身，不是任何一句台詞寫得好不好。

## 0005 拿掉了一整批「格式異常」測試

台詞原本是 `persona_cards.content` 這個 JSONB 裡的一段，所以有九個
parametrize case 在驗「這一筆不是 dict」「response_text 不是字串」之類的
狀況會不會炸。現在台詞是 `brain.canned_greetings` 的資料列：
`response_text TEXT NOT NULL CHECK (<> '')`、
`trigger_phrases TEXT[] NOT NULL CHECK (cardinality > 0)`——那些狀態在資料庫
層級就寫不進來，測試沒有東西可以測。

底下 `test_malformed_rows_cannot_be_written` 是它們的替代品：驗的不再是
「讀到壞資料會不會優雅地跳過」，而是「壞資料根本進不去」。**用 schema 讓壞
狀態無法表示，比在讀取端反覆檢查更可靠**，而且測試也變得更短。
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import DataError, IntegrityError

from app.modules.body.models import Spirit
from app.modules.brain.greetings import find_canned_response, match_canned_greeting
from app.modules.brain.models import (
    CannedGreeting,
    Character,
    CharacterPersona,
    CitySoul,
    LandmarkSoul,
)

_GREETING = "你好啊，旅人。今晚的星空很清楚。"
_FAREWELL = "路上小心。"


def _greeting(triggers: list[str], response: str) -> CannedGreeting:
    """未附著到 session 的台詞列，給純比對邏輯用。"""
    return CannedGreeting(trigger_phrases=triggers, response_text=response)


_CANNED = [
    _greeting(["你好", "哈囉", "hello"], _GREETING),
    _greeting(["再見", "bye"], _FAREWELL),
]


# ── 純比對邏輯（不碰資料庫）──────────────────────────────────────────

@pytest.mark.parametrize("user_input", ["你好", "哈囉", "hello"])
def test_exact_trigger_returns_response(user_input):
    assert find_canned_response(_CANNED, user_input) == _GREETING


def test_second_entry_is_also_matched():
    """比對要掃過所有 entry，不是只看第一筆。"""
    assert find_canned_response(_CANNED, "再見") == _FAREWELL


@pytest.mark.parametrize("user_input", ["  你好  ", "\n你好\t"])
def test_surrounding_whitespace_is_ignored(user_input):
    assert find_canned_response(_CANNED, user_input) == _GREETING


@pytest.mark.parametrize("user_input", ["HELLO", "Hello", "hELLo"])
def test_ascii_case_is_ignored(user_input):
    assert find_canned_response(_CANNED, user_input) == _GREETING


@pytest.mark.parametrize(
    "user_input",
    [
        "你好！",
        "你好!",
        "你好。",
        "你好，",
        "你好～",
        "你好？",
        "你好!!",          # 重複標點
        "你好～～～",
        "你好！ ",          # 標點後面還有空白：strip 要在 rstrip 標點之前
        "  你好。 ",
    ],
)
def test_trailing_punctuation_is_ignored(user_input):
    """
    2026-08-20 起去掉尾端標點。理由見模組 docstring：靠人工窮舉
    `trigger_phrases` 會組合爆炸，清單會膨脹到沒有人審得動。
    """
    assert find_canned_response(_CANNED, user_input) == _GREETING


def test_trailing_punctuation_does_not_weaken_the_containment_rule():
    """
    去標點**不能**讓「包含觸發語」變成命中——那是這個模組最重要的一條。

    比對仍然是完全相等，所以句尾的問號被去掉之後，剩下的字串跟「你好」
    還是不相等。
    """
    assert find_canned_response(_CANNED, "你好，龍山寺是什麼時候蓋的？") is None


def test_punctuation_only_input_is_a_miss():
    """全部被去掉之後是空字串，走既有的空輸入分支。"""
    assert find_canned_response(_CANNED, "？？？") is None


def test_full_width_ascii_is_still_not_converted():
    """
    全形半形轉換維持不做：那個會讓「ＨＥＬＬＯ」命中「hello」，屬於真的在猜。
    這條是把「刻意不做」釘住，不是描述缺陷。
    """
    assert find_canned_response(_CANNED, "ＨＥＬＬＯ") is None


def test_unmatched_input_returns_none():
    assert find_canned_response(_CANNED, "龍山寺是什麼時候蓋的？") is None


def test_input_containing_a_trigger_does_not_match():
    """
    這是整個模組最重要的一條：**包含**觸發語不等於命中。

    「你好，龍山寺是什麼時候蓋的？」如果因為含有「你好」就回招呼語，玩家真正
    的問題就被吃掉了。子字串比對會讓這個測試紅。
    """
    assert find_canned_response(_CANNED, "你好，龍山寺是什麼時候蓋的？") is None


def test_trigger_containing_input_does_not_match():
    """反方向也一樣：輸入是觸發語的一部分，同樣不算命中。"""
    assert find_canned_response([_greeting(["你好嗎"], _GREETING)], "你好") is None


def test_no_greetings_is_a_miss():
    assert find_canned_response([], "你好") is None


@pytest.mark.parametrize("user_input", ["", "   "])
def test_blank_input_is_a_miss(user_input):
    assert find_canned_response(_CANNED, user_input) is None


@pytest.mark.parametrize("user_input", ["", "   "])
def test_blank_input_does_not_match_a_blank_trigger(user_input):
    """
    觸發語被誤填成空白字串時，空白輸入不該因為「空 == 空」而命中。

    上面那個測試其實測不到這件事：一般的觸發語沒有一個會正規化成空字串，
    所以拿掉空白輸入的防護它照樣會過（這是跑 mutation 才發現的）。要踩到
    那段防護，資料本身必須也是空的。

    這一條**沒有**被 0005 的 CHECK 取代：`cardinality > 0` 擋的是空陣列，
    擋不住陣列裡有一個空白字串。
    """
    assert find_canned_response([_greeting(["   "], _GREETING)], user_input) is None


# ── 接上人格（走資料庫）───────────────────────────────────────────────

@pytest.fixture
def persona(db_session, unique_spirit_id):
    """一條完整的 city → landmark → character → persona → spirit 鏈。"""
    city_id = f"city-{unique_spirit_id}"
    landmark_id = f"lm-{unique_spirit_id}"
    character_id = f"ch-{unique_spirit_id}"

    db_session.add(CitySoul(city_id=city_id, name="測試城市", macro_history_summary="x"))
    db_session.add(
        LandmarkSoul(
            landmark_id=landmark_id, city_id=city_id, name="測試地標", founding_facts=[]
        )
    )
    db_session.flush()
    db_session.add(Character(character_id=character_id, landmark_id=landmark_id))
    db_session.add(
        Spirit(
            spirit_id=unique_spirit_id,
            display_name="測試地標",
            character_id=character_id,
            landmark_id=landmark_id,
            latitude=25.0,
            longitude=121.5,
            summon_radius_meters=50,
            is_active=True,
        )
    )
    db_session.commit()

    yield character_id

    db_session.query(CannedGreeting).filter_by(character_id=character_id).delete()
    db_session.query(CharacterPersona).filter_by(character_id=character_id).delete()
    db_session.query(Spirit).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(Character).filter_by(character_id=character_id).delete()
    db_session.query(LandmarkSoul).filter_by(landmark_id=landmark_id).delete()
    db_session.query(CitySoul).filter_by(city_id=city_id).delete()
    db_session.commit()


def _make_persona(db_session, character_id: str, *, version: int = 1, active: bool = True):
    db_session.add(
        CharacterPersona(
            character_id=character_id,
            version=version,
            archetype="守望者",
            speech_style="溫和",
            reviewed_by="test-reviewer",
            reviewed_at=datetime.now(timezone.utc),
            active=active,
        )
    )
    db_session.flush()
    db_session.add(
        CannedGreeting(
            character_id=character_id,
            version=version,
            trigger_phrases=["你好", "哈囉", "hello"],
            response_text=_GREETING,
        )
    )
    db_session.commit()


def test_match_from_active_persona(db_session, unique_spirit_id, persona):
    _make_persona(db_session, persona)

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") == _GREETING


def test_miss_from_active_persona(db_session, unique_spirit_id, persona):
    _make_persona(db_session, persona)

    assert match_canned_greeting(db_session, unique_spirit_id, "龍山寺幾點關門？") is None


def test_no_persona_is_a_miss_not_an_error(db_session, unique_spirit_id, persona):
    """AC：沒有生效人格一律視為未命中，不拋例外。"""
    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None


def test_inactive_persona_is_ignored(db_session, unique_spirit_id, persona):
    """未審核通過（active=false）的人格不該生效——B3 的規則在這裡也成立。"""
    _make_persona(db_session, persona, active=False)

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None


def test_greetings_belong_to_a_version_not_a_character(db_session, unique_spirit_id, persona):
    """
    台詞掛在 `(character_id, version)` 上，不是掛在角色上。

    這條讓「改台詞」必須走「新版本 → 重新審核」的同一條路。如果台詞只綁角色，
    就能繞過審核直接換掉玩家聽到的話——而預寫台詞正是唯一**不經過 Gemini、
    原樣送到玩家眼前**的內容，繞過審核的後果最直接。
    """
    _make_persona(db_session, persona, version=1, active=False)
    _make_persona(db_session, persona, version=2, active=True)
    # version 2 的台詞換成別的
    db_session.query(CannedGreeting).filter_by(character_id=persona, version=2).delete()
    db_session.add(
        CannedGreeting(
            character_id=persona, version=2, trigger_phrases=["你好"], response_text=_FAREWELL
        )
    )
    db_session.commit()

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") == _FAREWELL


@pytest.mark.parametrize(
    ("triggers", "response"),
    [
        (["你好"], ""),  # 空台詞
        ([], "有回覆但沒有觸發語"),  # 空觸發語陣列
    ],
)
def test_malformed_rows_cannot_be_written(db_session, persona, triggers, response):
    """
    0005 之前這些是「讀到了要優雅跳過」；現在是「寫不進去」。

    NOT NULL 擋不住空字串與空陣列，兩條 CHECK 才擋得住。差別在於：跳過的
    寫法讓一筆壞資料靜靜地不生效，沒有人會發現；擋在寫入端則是編輯的當下
    就知道。
    """
    _make_persona(db_session, persona)
    db_session.add(
        CannedGreeting(
            character_id=persona, version=1, trigger_phrases=triggers, response_text=response
        )
    )

    with pytest.raises((IntegrityError, DataError)):
        db_session.commit()
    db_session.rollback()
