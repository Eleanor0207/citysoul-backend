"""
B11．共鳴值解鎖敘事生成（issue #25）。

純單元測試，模型以 fake 注入——**不需要 GCP 憑證、不需要資料庫**。
"""
from types import SimpleNamespace

import pytest

from app.modules.brain.gemini import FALLBACK_REPLY, FakeGeminiClient
from app.modules.brain.unlock_story import (
    STORY_REVIEW_STATUS,
    UnlockStory,
    build_unlock_prompt,
    fallback_story_for,
    generate_unlock_stories,
    generate_unlock_story,
)

_SPIRIT = "taipei_longshan"
_GENERATED = "（城市靈魂偏過頭）我記得你上次站在石獅子旁邊很久。"
_NOT_THIS_CHARACTER = "TEST_NOT_THIS_CHARACTER"
_TABOO = "TEST_TABOO"
_PERSONA = SimpleNamespace(
    archetype="TEST_ARCHETYPE",
    personality_traits=[],
    values=[],
    speech_style="TEST_SPEECH_STYLE",
    tone_override=None,
    not_this_character=_NOT_THIS_CHARACTER,
    taboos=[_TABOO],
    imagination_license="TEST_IMAGINATION_LICENSE",
)


# ── 介面 ───────────────────────────────────────────────────────────────

def test_returns_an_unlock_story():
    """AC：回傳 `UnlockStory`（含 `stage` 與 `story_text`）。"""
    result = generate_unlock_story(
        FakeGeminiClient(response=_GENERATED),
        spirit_id=_SPIRIT,
        stage=1,
        persona=_PERSONA,
    )

    assert isinstance(result, UnlockStory)
    assert result.stage == 1
    assert result.story_text == _GENERATED
    assert result.is_fallback is False


def test_the_prompt_reaches_the_model():
    client = FakeGeminiClient(response=_GENERATED)

    generate_unlock_story(client, spirit_id=_SPIRIT, stage=1, persona=_PERSONA)

    assert client.call_count == 1
    assert _SPIRIT in client.prompts[0]


# ── 內容依 stage 遞進 ─────────────────────────────────────────────────

def test_each_stage_produces_a_different_prompt():
    """
    🔒 AC：三個 stage 的 prompt **各不相同**。

    若三段講的其實是同一件事，解鎖的意義就消失了——玩家會發現三次拿到同樣的
    故事，而「共鳴值」這整個機制的說服力也就沒了。

    AC 指定要做 mutation 驗證的那一條。
    """
    prompts = [
        build_unlock_prompt(_SPIRIT, stage, persona=_PERSONA) for stage in (1, 2, 3)
    ]

    assert len(set(prompts)) == 3, "不同階段產生了相同的 prompt——解鎖失去意義"


def test_stages_are_progressive_not_paraphrases():
    """
    三段是**遞進**，不是同義改寫。

    用各階段的關鍵語意當訊號：stage 1 是「剛認得」、stage 2 是「願意講不對外人
    說的事」、stage 3 是「你已經是我的一部分」。潤稿可以改字，但這三種關係深度
    要分得出來。
    """
    stage_1 = build_unlock_prompt(_SPIRIT, 1, persona=_PERSONA)
    stage_2 = build_unlock_prompt(_SPIRIT, 2, persona=_PERSONA)
    stage_3 = build_unlock_prompt(_SPIRIT, 3, persona=_PERSONA)

    assert "第一個轉折" in stage_1
    assert "不對外人說" in stage_2
    assert "記憶的一部分" in stage_3
    # stage 3 是最深的，不該還在講「剛認出」。
    assert "剛認出" not in stage_3


def test_prompt_injects_persona_safety_fields():
    prompt = build_unlock_prompt(_SPIRIT, 1, persona=_PERSONA)

    assert _NOT_THIS_CHARACTER in prompt
    assert _TABOO in prompt


def test_prompt_mentions_the_stage_number():
    for stage in (1, 2, 3):
        assert str(stage) in build_unlock_prompt(_SPIRIT, stage, persona=_PERSONA)


def test_prompt_carries_the_historical_boundary_rules():
    """
    解鎖故事同樣會講到這座地標的過去，沒有理由讓它比一般對話寬鬆。
    """
    from app.modules.brain.historical_boundary import get_historical_boundary_rules

    assert get_historical_boundary_rules() in build_unlock_prompt(
        _SPIRIT, 1, persona=_PERSONA
    )


def test_unknown_stage_falls_back_to_the_first_brief_without_raising():
    """未知階段不該讓整個任務完成流程炸掉。"""
    assert build_unlock_prompt(_SPIRIT, 99, persona=_PERSONA)


# ── 失敗回退 ───────────────────────────────────────────────────────────

def test_model_failure_falls_back_to_prewritten_text():
    """
    AC：生成失敗回退人工預寫台詞、不拋例外。

    B1 的契約是「永遠回非空字串，失敗時回 `FALLBACK_REPLY`」，所以這裡靠內容
    判斷是否回退，而不是 try/except——B1 根本不會拋例外給我們。
    """
    result = generate_unlock_story(
        FakeGeminiClient(response=FALLBACK_REPLY),
        spirit_id=_SPIRIT,
        stage=2,
        persona=_PERSONA,
    )

    assert result.is_fallback is True
    assert result.story_text == fallback_story_for(2)
    assert result.stage == 2


def test_empty_response_falls_back():
    result = generate_unlock_story(
        FakeGeminiClient(response=""),
        spirit_id=_SPIRIT,
        stage=1,
        persona=_PERSONA,
    )

    assert result.is_fallback is True
    assert result.story_text


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_every_stage_has_its_own_fallback(stage):
    """
    每個階段的回退台詞不同。

    共用一句的話，一次模型中斷會讓三個階段的解鎖看起來完全一樣——那正是這個
    機制最不該發生的事。
    """
    others = [fallback_story_for(s) for s in (1, 2, 3) if s != stage]

    assert fallback_story_for(stage) not in others


def test_fallback_for_unknown_stage_does_not_raise():
    assert fallback_story_for(99)


def test_no_exception_escapes_even_if_the_client_is_broken():
    """
    這一層在任務完成流程的後半。共鳴值已經入帳了，玩家的進度是真的——一次生成
    失敗不該讓整個流程看起來像出錯。
    """

    class _BrokenClient:
        def generate(self, prompt):
            return FALLBACK_REPLY

    result = generate_unlock_story(
        _BrokenClient(), spirit_id=_SPIRIT, stage=1, persona=_PERSONA
    )

    assert result.is_fallback is True


# ── 一次跨多個門檻 ─────────────────────────────────────────────────────

def test_multiple_stages_produce_multiple_stories():
    """
    🔒 AC：`newly_unlocked_stages = [1, 2]` 要得到 **2 段**故事。

    ⚠️ #16 刻意讓 `newly_unlocked_stages` 回傳 list 就是為了這件事。只取最後
    一個會讓中間那段**靜默消失**——沒有例外、沒有 log，玩家只是永遠看不到那一段。
    """
    client = FakeGeminiClient(response=_GENERATED)

    stories = generate_unlock_stories(
        client, spirit_id=_SPIRIT, stages=[1, 2], persona=_PERSONA
    )

    assert [s.stage for s in stories] == [1, 2]
    assert client.call_count == 2


def test_each_story_gets_its_own_prompt():
    client = FakeGeminiClient(response=_GENERATED)

    generate_unlock_stories(
        client, spirit_id=_SPIRIT, stages=[1, 2, 3], persona=_PERSONA
    )

    assert len(set(client.prompts)) == 3


def test_empty_stage_list_produces_nothing_and_calls_nothing():
    """
    沒有跨門檻時不該呼叫模型。這是最常發生的情況（大多數任務完成都沒跨門檻），
    每次都白呼叫一次的成本會很可觀。
    """
    client = FakeGeminiClient()

    assert generate_unlock_stories(
        client, spirit_id=_SPIRIT, stages=[], persona=_PERSONA
    ) == []
    assert client.call_count == 0


# ── 文案審核狀態 ───────────────────────────────────────────────────────

def test_stories_are_marked_as_pending_review():
    assert STORY_REVIEW_STATUS == "PENDING_NARRATIVE_REVIEW"


def test_review_marker_never_reaches_the_player():
    for stage in (1, 2, 3):
        assert STORY_REVIEW_STATUS not in fallback_story_for(stage)
        assert STORY_REVIEW_STATUS not in build_unlock_prompt(
            _SPIRIT, stage, persona=_PERSONA
        )
