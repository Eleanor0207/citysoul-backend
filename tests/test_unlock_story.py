"""
Ticket #25．共鳴值解鎖敘事生成（B11）。

驗收標準對照見 GitHub issue #25。純函式測試，不需要資料庫或 GCP 憑證。
"""
from app.modules.brain.gemini import FALLBACK_REPLY as GEMINI_FALLBACK_REPLY
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.unlock_story import (
    UnlockStory,
    generate_unlock_stories,
    generate_unlock_story,
)


# ── 介面（issue #25 AC1）─────────────────────────────────────────────────

def test_returns_unlock_story_with_stage_and_text():
    fake = FakeGeminiClient(response="第一次見面，你在廟埕前駐足良久。")

    result = generate_unlock_story("P", "taipei_longshan", 1, fake)

    assert isinstance(result, UnlockStory)
    assert result.stage == 1
    assert result.story_text == "第一次見面，你在廟埕前駐足良久。"


# ── 內容依 stage 不同（issue #25 AC2）────────────────────────────────────

def test_different_stages_produce_different_prompts():
    fake = FakeGeminiClient()

    for stage in (1, 2, 3):
        generate_unlock_story("P", "taipei_longshan", stage, fake)

    prompts = fake.prompts
    assert len(prompts) == 3
    assert len(set(prompts)) == 3, "三個 stage 的 prompt 應該各不相同"


def test_stage_three_prompt_reflects_the_deepest_relationship():
    """stage 3 是三個階段裡最深的關係，prompt 內容要能反映這件事。"""
    fake = FakeGeminiClient()
    generate_unlock_story("P", "taipei_longshan", 3, fake)

    assert "知交" in fake.prompts[0] or "最深" in fake.prompts[0]


# ── 生成失敗／逾時回退（issue #25 AC3）───────────────────────────────────

def test_gemini_fallback_response_is_replaced_with_story_specific_fallback():
    fake = FakeGeminiClient(response=GEMINI_FALLBACK_REPLY)

    result = generate_unlock_story("P", "taipei_longshan", 1, fake)

    assert result.story_text != GEMINI_FALLBACK_REPLY
    assert result.story_text


# ── 一次跨多個門檻，每個 stage 各生成一段（issue #25 AC4）────────────────

def test_multiple_newly_unlocked_stages_each_get_their_own_story():
    fake = FakeGeminiClient()

    results = generate_unlock_stories("P", "taipei_longshan", [1, 2], fake)

    assert len(results) == 2
    assert [r.stage for r in results] == [1, 2]
    assert fake.call_count == 2


def test_no_newly_unlocked_stages_returns_empty_list():
    fake = FakeGeminiClient()

    results = generate_unlock_stories("P", "taipei_longshan", [], fake)

    assert results == []
    assert fake.call_count == 0
