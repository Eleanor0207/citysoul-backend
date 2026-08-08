"""
Ticket #25．共鳴值解鎖敘事生成（B11）。

驗收標準對照見 GitHub issue #25。跟其他 B-系列模組同一種風格：真實 SDK
從來不載入，測試注入 `FakeGeminiClient`，完全不需要 GCP 憑證。
"""
import ast
import inspect
from pathlib import Path

import pytest

from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient, GeminiClient
from app.modules.brain.unlock_story import (
    UnlockStory,
    generate_unlock_stories,
    generate_unlock_story,
)

_PLAYER = "P"
_SPIRIT = "taipei_longshan"


class _RaisingGeminiClient(GeminiClient):
    """
    模擬 (a) 連線錯誤／(b) 逾時。

    `GeminiClient` 契約上說 generate 不會拋例外，但呼叫端可以注入任何實作，
    B11 不該賭別人都遵守契約——這個 fake 就是用來驗證那道兜底。
    """

    def __init__(self, error: Exception):
        self._error = error

    def generate(self, prompt: str) -> str:
        raise self._error


# ── 介面（AC1） ──────────────────────────────────────────────────────────


def test_returns_unlock_story_with_stage_and_text():
    fake = FakeGeminiClient(response="你又來了，這次我想多說一點。")

    result = generate_unlock_story(_PLAYER, _SPIRIT, 1, gemini_client=fake)

    assert isinstance(result, UnlockStory)
    assert result.stage == 1
    assert result.story_text == "你又來了，這次我想多說一點。"
    assert result.source == "generated"


# ── 內容依 stage 不同（AC2） ──────────────────────────────────────────────


def test_each_stage_produces_a_different_prompt():
    """
    三個 stage 的 prompt 必須各不相同——若三者相同，玩家會三次拿到同樣的
    故事，解鎖就失去意義。
    """
    fake = FakeGeminiClient()

    for stage in (1, 2, 3):
        generate_unlock_story(_PLAYER, _SPIRIT, stage, gemini_client=fake)

    assert len(fake.prompts) == 3
    assert len(set(fake.prompts)) == 3


def test_prompts_reflect_deepening_relationship():
    """
    stage 3 應該是最深的關係階段。用「老朋友」這個語意標記驗證遞進方向——
    不逐字比對整段 prompt，那會讓每次潤稿都變成破壞性變更。
    """
    fake = FakeGeminiClient()

    for stage in (1, 2, 3):
        generate_unlock_story(_PLAYER, _SPIRIT, stage, gemini_client=fake)

    stage_1_prompt, stage_2_prompt, stage_3_prompt = fake.prompts

    assert "初識" in stage_1_prompt
    assert "熟識" in stage_2_prompt
    assert "老朋友" in stage_3_prompt


def test_stage_number_appears_in_the_prompt():
    fake = FakeGeminiClient()

    generate_unlock_story(_PLAYER, _SPIRIT, 2, gemini_client=fake)

    assert "2" in fake.prompts[0]


# ── 生成失敗／逾時回退（AC3） ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [ConnectionError("connection reset"), TimeoutError("deadline exceeded")],
)
def test_client_exceptions_fall_back_without_raising(error):
    result = generate_unlock_story(
        _PLAYER, _SPIRIT, 1, gemini_client=_RaisingGeminiClient(error)
    )

    assert result.source == "fallback"
    assert result.story_text.strip() != ""
    assert result.stage == 1


def test_b1_fallback_reply_is_replaced_by_a_stage_appropriate_fallback():
    """
    B1 失敗時會回傳它自己的 `FALLBACK_REPLY`（那句話是為了「我不知道怎麼
    回答」寫的）。直接把它當成解鎖故事會很突兀——這裡要換成屬於這個階段的
    回退台詞。
    """
    fake = FakeGeminiClient(response=FALLBACK_REPLY)

    result = generate_unlock_story(_PLAYER, _SPIRIT, 3, gemini_client=fake)

    assert result.source == "fallback"
    assert result.story_text != FALLBACK_REPLY
    assert result.story_text.strip() != ""


def test_empty_model_response_falls_back():
    fake = FakeGeminiClient(response="   ")

    result = generate_unlock_story(_PLAYER, _SPIRIT, 1, gemini_client=fake)

    assert result.source == "fallback"
    assert result.story_text.strip() != ""


def test_fallback_text_differs_per_stage():
    """
    回退時玩家仍然應該感覺到「這是第幾階段的解鎖」。三個階段共用同一句話
    會讓三次解鎖看起來一樣，那正是這張票要避免的事。
    """
    texts = {
        stage: generate_unlock_story(
            _PLAYER,
            _SPIRIT,
            stage,
            gemini_client=_RaisingGeminiClient(ConnectionError("x")),
        ).story_text
        for stage in (1, 2, 3)
    }

    assert len(set(texts.values())) == 3


def test_unknown_stage_falls_back_without_calling_the_model():
    """
    門檻改了但這裡沒跟上時，回退而不是硬湊一個 prompt 送出去。
    """
    fake = FakeGeminiClient()

    result = generate_unlock_story(_PLAYER, _SPIRIT, 99, gemini_client=fake)

    assert result.source == "fallback"
    assert result.stage == 99
    assert fake.call_count == 0


# ── 一次跨多個門檻，每個 stage 各一段（AC4） ──────────────────────────────


def test_multiple_stages_produce_one_story_each():
    """
    #16 讓 `newly_unlocked_stages` 回傳 list 就是為了這件事：5 → 55 會同時
    跨過 10 與 40，兩段故事都要生，中間那段不能靜默消失。
    """
    fake = FakeGeminiClient(response="一段故事")

    stories = generate_unlock_stories(_PLAYER, _SPIRIT, [1, 2], gemini_client=fake)

    assert len(stories) == 2
    assert [s.stage for s in stories] == [1, 2]
    assert fake.call_count == 2


def test_multiple_stages_each_get_their_own_prompt():
    fake = FakeGeminiClient()

    generate_unlock_stories(_PLAYER, _SPIRIT, [1, 2, 3], gemini_client=fake)

    assert len(set(fake.prompts)) == 3


def test_empty_stage_list_produces_no_stories_and_no_calls():
    """沒有跨過任何門檻時不該呼叫模型——那是最常見的情況，不該白花成本。"""
    fake = FakeGeminiClient()

    assert generate_unlock_stories(_PLAYER, _SPIRIT, [], gemini_client=fake) == []
    assert fake.call_count == 0


# ── 呼叫順序硬規則（v2.1 §6.4） ───────────────────────────────────────────


def test_module_takes_no_database_session():
    """
    v2.1 §6.4：身體必須先寫完 `resonance` 表、再呼叫腦袋。這支模組拿不到
    資料庫，也就不可能自己去讀一個可能還沒寫完的值——`stage` 只能由呼叫端
    傳進來，而呼叫端傳進來的時候表已經寫完了。
    """
    for func in (generate_unlock_story, generate_unlock_stories):
        params = inspect.signature(func).parameters
        assert "db" not in params
        assert "session" not in params


def test_no_player_id_in_the_prompt():
    """
    `player_id` 收在簽章裡是 SDD 指定的介面，但不該送進模型——故事內容取決
    於關係階段，不取決於玩家是誰，送 id 只是多一份沒必要外流的資料。
    """
    fake = FakeGeminiClient()
    distinctive_player_id = "player-abc123-very-distinctive"

    generate_unlock_story(distinctive_player_id, _SPIRIT, 1, gemini_client=fake)

    assert distinctive_player_id not in fake.prompts[0]


def test_no_database_identifiers_in_implementation():
    """跟簽章檢查互補：模組內部也不該偷偷 import 一個 session 自己去查。"""
    module_path = Path(
        __import__("app.modules.brain.unlock_story", fromlist=["x"]).__file__
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    forbidden = ["sessionlocal", "get_db", "query", "commit"]

    offenders = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, ast.Attribute):
            name = node.attr

        if name and any(f in name.lower() for f in forbidden):
            offenders.append(name)

    assert offenders == [], f"發現資料庫相關識別碼，違反 v2.1 §6.4 的呼叫順序邊界：{offenders}"
