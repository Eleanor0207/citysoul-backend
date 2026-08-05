"""
Ticket #13．快速問候比對層（B12）。

驗收標準對照見 GitHub issue #13。

`canned_greetings` 的實際內容待敘事負責人審核撰寫，所以這裡全部用假資料——
測的是比對邏輯本身，不是任何一句台詞寫得好不好。
"""
from datetime import datetime, timezone

import pytest

from app.modules.brain.greetings import find_canned_response, match_canned_greeting
from app.modules.brain.models import PersonaCard

_GREETING = "你好啊，旅人。今晚的星空很清楚。"
_FAREWELL = "路上小心。"

_CANNED = [
    {"trigger_phrases": ["你好", "哈囉", "hello"], "response_text": _GREETING},
    {"trigger_phrases": ["再見", "bye"], "response_text": _FAREWELL},
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


def test_unmatched_input_returns_none():
    assert find_canned_response(_CANNED, "天文館是什麼時候蓋的？") is None


def test_input_containing_a_trigger_does_not_match():
    """
    這是整個模組最重要的一條：**包含**觸發語不等於命中。

    「你好，天文館是什麼時候蓋的？」如果因為含有「你好」就回招呼語，玩家真正
    的問題就被吃掉了。子字串比對會讓這個測試紅。
    """
    assert find_canned_response(_CANNED, "你好，天文館是什麼時候蓋的？") is None


def test_trigger_containing_input_does_not_match():
    """反方向也一樣：輸入是觸發語的一部分，同樣不算命中。"""
    assert find_canned_response([{"trigger_phrases": ["你好嗎"], "response_text": _GREETING}], "你好") is None


# ── 空值與異常資料 ────────────────────────────────────────────────────

@pytest.mark.parametrize("empty", [[], None])
def test_empty_or_missing_canned_greetings_is_a_miss(empty):
    assert find_canned_response(empty, "你好") is None


@pytest.mark.parametrize("user_input", ["", "   "])
def test_blank_input_is_a_miss(user_input):
    assert find_canned_response(_CANNED, user_input) is None


@pytest.mark.parametrize("user_input", ["", "   "])
def test_blank_input_does_not_match_a_blank_trigger(user_input):
    """
    觸發語被誤填成空字串時，空白輸入不該因為「空 == 空」而命中。

    上面那個測試其實測不到這件事：一般的觸發語沒有一個會正規化成空字串，
    所以拿掉空白輸入的防護它照樣會過（這是跑 mutation 才發現的）。要踩到
    那段防護，資料本身必須也是空的。
    """
    bad_data = [{"trigger_phrases": ["   "], "response_text": _GREETING}]
    assert find_canned_response(bad_data, user_input) is None


@pytest.mark.parametrize(
    "malformed",
    [
        "不是 list 而是字串",
        [None],
        ["不是 dict"],
        [{"trigger_phrases": ["你好"]}],  # 少了 response_text
        [{"response_text": "有回覆但沒有觸發語"}],
        [{"trigger_phrases": "不是 list", "response_text": "x"}],
        [{"trigger_phrases": [None, 123], "response_text": "x"}],
        [{"trigger_phrases": ["你好"], "response_text": ""}],
        [{"trigger_phrases": ["你好"], "response_text": 123}],
    ],
)
def test_malformed_entries_are_skipped_not_raised(malformed):
    """
    人工編輯的 JSONB 打錯字，應該讓那一筆失效，而不是讓對話端點 500——
    否則一個內容問題會變成一次服務中斷。
    """
    assert find_canned_response(malformed, "你好") is None


def test_malformed_entry_does_not_block_a_later_valid_one():
    """壞掉的那筆要被跳過，後面正常的那筆仍要能命中。"""
    mixed = ["壞資料", {"trigger_phrases": ["你好"], "response_text": _GREETING}]
    assert find_canned_response(mixed, "你好") == _GREETING


# ── 接上人格卡（走資料庫）─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cleanup(db_session, unique_spirit_id):
    yield
    db_session.query(PersonaCard).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.commit()


def _make_card(db_session, spirit_id: str, content: dict, is_active: bool = True) -> None:
    db_session.add(
        PersonaCard(
            spirit_id=spirit_id,
            version=1,
            content=content,
            reviewed_by="test-reviewer",
            reviewed_at=datetime.now(timezone.utc),
            is_active=is_active,
        )
    )
    db_session.commit()


def test_match_from_active_persona_card(db_session, unique_spirit_id):
    _make_card(db_session, unique_spirit_id, {"schema_version": 1, "canned_greetings": _CANNED})

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") == _GREETING


def test_miss_from_active_persona_card(db_session, unique_spirit_id):
    _make_card(db_session, unique_spirit_id, {"schema_version": 1, "canned_greetings": _CANNED})

    assert match_canned_greeting(db_session, unique_spirit_id, "天文館幾點關門？") is None


def test_no_persona_card_is_a_miss_not_an_error(db_session, unique_spirit_id):
    """AC：沒有生效人格卡一律視為未命中，不拋例外。"""
    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None


def test_inactive_persona_card_is_ignored(db_session, unique_spirit_id):
    """未審核通過（is_active=false）的人格卡不該生效——B3 的規則在這裡也成立。"""
    _make_card(
        db_session,
        unique_spirit_id,
        {"schema_version": 1, "canned_greetings": _CANNED},
        is_active=False,
    )

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None


def test_card_without_canned_greetings_field_is_a_miss(db_session, unique_spirit_id):
    """人格卡存在但根本沒有 canned_greetings 欄位（例如舊版 schema）。"""
    _make_card(db_session, unique_spirit_id, {"schema_version": 1, "core_personality": "溫和"})

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None


def test_card_with_empty_canned_greetings_is_a_miss(db_session, unique_spirit_id):
    """對應 seed data 目前的狀態：欄位在，但還沒有人寫內容。"""
    _make_card(db_session, unique_spirit_id, {"schema_version": 1, "canned_greetings": []})

    assert match_canned_greeting(db_session, unique_spirit_id, "你好") is None
