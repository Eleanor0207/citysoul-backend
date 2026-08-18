from datetime import date
from types import SimpleNamespace

from app.modules.brain.guided_questions import (
    GuidedQuestionInputs,
    build_guided_questions_prompt,
    generate_guided_questions,
)
from app.modules.brain.gemini import FakeGeminiClient


def _persona():
    return SimpleNamespace(
        archetype="TEST_ARCHETYPE",
        personality_traits=["TEST_TRAIT"],
        values=["TEST_VALUE"],
        speech_style="TEST_STYLE",
        tone_override=None,
        not_this_character="TEST_NOT_THIS_CHARACTER",
        taboos=["TEST_TABOO"],
        quest_themes=["TEST_THEME"],
    )


def _inputs():
    return GuidedQuestionInputs(
        daily_context="TEST_DAILY_CONTEXT",
        resonance_stage=2,
        story_beat="TEST_STORY_BEAT",
        event_date=date(2026, 8, 19),
    )


def test_generation_returns_two_or_three_questions_and_injects_persona():
    client = FakeGeminiClient(response='["Q1", "Q2", "Q3"]')
    result = generate_guided_questions(client, inputs=_inputs(), persona=_persona())

    assert len(result.questions) == 3
    assert result.is_fallback is False
    prompt = build_guided_questions_prompt(inputs=_inputs(), persona=_persona())
    assert "TEST_TABOO" in prompt
    assert "TEST_THEME" in prompt


def test_generation_failure_returns_pending_review_fallback():
    result = generate_guided_questions(
        FakeGeminiClient(response="not-json"), inputs=_inputs(), persona=_persona()
    )

    assert 2 <= len(result.questions) <= 3
    assert result.is_fallback is True
    assert all("PENDING_NARRATIVE_REVIEW" in question for question in result.questions)


def test_unqualified_input_returns_pending_review_fallback():
    inputs = GuidedQuestionInputs(daily_context=None)
    result = generate_guided_questions(
        FakeGeminiClient(response='["Q1", "Q2"]'), inputs=inputs, persona=_persona()
    )

    assert result.is_fallback is True
