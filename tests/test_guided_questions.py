from datetime import date
from types import SimpleNamespace

from app.modules.brain.guided_questions import (
    CONTENT_REVIEW_STATUS,
    PROXIMITY_FAR,
    PROXIMITY_NEAR,
    GuidedQuestionInputs,
    build_guided_questions_prompt,
    fallback_questions,
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
        guided_question_fallback=None,
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


def test_generation_failure_returns_player_ready_fallback():
    result = generate_guided_questions(
        FakeGeminiClient(response="not-json"), inputs=_inputs(), persona=_persona()
    )

    assert 2 <= len(result.questions) <= 3
    assert result.is_fallback is True
    assert all(question.strip() for question in result.questions)
    assert not any(CONTENT_REVIEW_STATUS in question for question in result.questions)


def test_fallback_uses_the_persona_quest_themes():
    persona = _persona()
    persona.quest_themes = ["THEME_A", "THEME_B", "THEME_C", "THEME_D"]
    result = fallback_questions(persona)

    assert len(result.questions) == 3
    assert [theme in question for theme, question in zip(
        ["THEME_A", "THEME_B", "THEME_C"], result.questions
    )] == [True, True, True]
    assert "THEME_D" not in " ".join(result.questions)


def test_fallback_pads_a_single_theme_to_two_questions():
    persona = _persona()
    persona.quest_themes = ["THEME_A"]
    result = fallback_questions(persona)

    assert len(result.questions) == 2
    assert "THEME_A" in result.questions[0]


def test_fallback_without_persona_is_still_two_player_ready_questions():
    result = fallback_questions(None)

    assert len(result.questions) == 2
    assert result.is_fallback is True
    assert all(question.strip() for question in result.questions)
    assert not any(CONTENT_REVIEW_STATUS in question for question in result.questions)


def test_unqualified_input_returns_pending_review_fallback():
    inputs = GuidedQuestionInputs(daily_context=None)
    result = generate_guided_questions(
        FakeGeminiClient(response='["Q1", "Q2"]'), inputs=inputs, persona=_persona()
    )

    assert result.is_fallback is True


def test_card_authored_fallback_wins_over_quest_themes():
    persona = _persona()
    persona.guided_question_fallback = ["CARD_Q1", "CARD_Q2", "CARD_Q3", "CARD_Q4"]
    result = fallback_questions(persona)

    assert result.questions == ("CARD_Q1", "CARD_Q2", "CARD_Q3")
    assert "TEST_THEME" not in " ".join(result.questions)


def test_card_fallback_with_a_single_question_falls_through_to_themes():
    persona = _persona()
    persona.guided_question_fallback = ["CARD_Q1"]
    result = fallback_questions(persona)

    assert "TEST_THEME" in " ".join(result.questions)
    assert "CARD_Q1" not in " ".join(result.questions)


def test_far_proximity_fallback_ignores_the_card_entirely():
    persona = _persona()
    persona.guided_question_fallback = ["CARD_Q1", "CARD_Q2", "CARD_Q3"]
    far = fallback_questions(persona, proximity=PROXIMITY_FAR)
    near = fallback_questions(persona, proximity=PROXIMITY_NEAR)

    assert far.questions == fallback_questions(None, proximity=PROXIMITY_FAR).questions
    assert "CARD_Q1" not in " ".join(far.questions)
    assert near.questions[0] == "CARD_Q1"


def test_prompt_tells_the_model_which_proximity_tier_the_player_is_in():
    far = build_guided_questions_prompt(
        inputs=GuidedQuestionInputs(daily_context="D", proximity=PROXIMITY_FAR),
        persona=_persona(),
    )
    near = build_guided_questions_prompt(
        inputs=GuidedQuestionInputs(daily_context="D", proximity=PROXIMITY_NEAR),
        persona=_persona(),
    )

    assert "150 公尺外" in far
    assert "不可以要求玩家觀察眼前的細節" in far
    assert "50 公尺內" in near
    assert "15 個字" in far and "15 個字" in near


def test_generation_failure_at_far_proximity_uses_the_far_fallback():
    persona = _persona()
    persona.guided_question_fallback = ["CARD_Q1", "CARD_Q2", "CARD_Q3"]
    inputs = GuidedQuestionInputs(
        daily_context="TEST_DAILY_CONTEXT", proximity=PROXIMITY_FAR
    )
    result = generate_guided_questions(
        FakeGeminiClient(response="not-json"), inputs=inputs, persona=persona
    )

    assert result.is_fallback is True
    assert "CARD_Q1" not in " ".join(result.questions)


def test_every_shipped_card_has_short_player_ready_fallback_questions():
    """十張人格卡各自的保底提問：2–3 則、每則 15 字以內、不含審核佔位字串。"""

    import glob
    import os
    import re

    import yaml

    latest: dict[str, tuple[int, str]] = {}
    for path in glob.glob("content/personas/*.yaml"):
        match = re.match(r"(.+)_v(\d+)\.yaml$", os.path.basename(path))
        base, version = match.group(1), int(match.group(2))
        if version > latest.get(base, (0, ""))[0]:
            latest[base] = (version, path)

    assert len(latest) == 10
    for base, (_, path) in sorted(latest.items()):
        with open(path, encoding="utf-8") as handle:
            card = yaml.safe_load(handle)
        questions = card.get("guided_question_fallback")
        assert questions, f"{base} 沒有 guided_question_fallback"
        assert 2 <= len(questions) <= 3, base
        for question in questions:
            assert question.strip(), base
            assert len(question) <= 15, f"{base}: {question}"
            assert CONTENT_REVIEW_STATUS not in question, base
