"""B14 guided question generation with no database or cache access."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from app.modules.brain.gemini import FALLBACK_REPLY, GeminiClient
from app.modules.brain.prompt_builder import persona_section

logger = logging.getLogger(__name__)
CONTENT_REVIEW_STATUS = "PENDING_NARRATIVE_REVIEW"

# Deliberately not player-ready copy; content review must replace these.
_FALLBACK_QUESTIONS = (
    f"[{CONTENT_REVIEW_STATUS}] guided question placeholder 1",
    f"[{CONTENT_REVIEW_STATUS}] guided question placeholder 2",
)


@dataclass(frozen=True)
class GuidedQuestionInputs:
    """Whitelisted context supplied by the Body layer."""

    daily_context: str | None = None
    resonance_stage: int = 0
    story_beat: str | None = None
    event_date: date | None = None

    def is_qualified(self, persona) -> bool:
        themes = getattr(persona, "quest_themes", None) if persona is not None else None
        return bool(
            self.daily_context
            and self.daily_context.strip()
            and themes
            and any(str(theme).strip() for theme in themes)
        )


@dataclass(frozen=True)
class GuidedQuestions:
    questions: tuple[str, ...]
    is_fallback: bool = False


def fallback_questions() -> GuidedQuestions:
    """Return a non-empty fallback visibly marked for narrative review."""

    return GuidedQuestions(questions=_FALLBACK_QUESTIONS, is_fallback=True)


def build_guided_questions_prompt(
    *,
    inputs: GuidedQuestionInputs,
    persona,
) -> str:
    """Build the B14 prompt from only the approved inputs."""

    themes = getattr(persona, "quest_themes", None) or []
    lines = [
        persona_section(persona),
        "",
        "【人格卡中的任務主題】",
        *[f"- {theme}" for theme in themes if str(theme).strip()],
        "",
        "【B14 引導提問任務】",
        "根據人格卡、當日情境、玩家共鳴階段與目前劇情節點，產生 2 或 3 則玩家可以直接點擊送出的提問。",
        "只輸出 JSON 陣列；每個元素都是一則完整提問，不要輸出 Markdown、答案、前言或其他文字。",
        "提問要有當日變化、符合角色口吻，不得觸碰人格卡中的 taboos，也不得把角色寫成 not_this_character 所排除的身分。",
        f"玩家共鳴階段：{inputs.resonance_stage}",
        f"當日情境：{inputs.daily_context}",
    ]
    if inputs.event_date is not None:
        lines.append(f"情境日期：{inputs.event_date.isoformat()}")
    lines.append(
        f"目前劇情節點：{inputs.story_beat}"
        if inputs.story_beat and inputs.story_beat.strip()
        else "目前劇情節點：無"
    )
    return "\n".join(lines)


def _parse_questions(raw: str) -> tuple[str, ...] | None:
    """Accept only a clean 2–3 item JSON response from the model."""

    text = (raw or "").strip()
    if not text or text == FALLBACK_REPLY:
        return None
    if text.startswith("```") and text.endswith("```"):
        parts = text.split("\n", 1)
        text = parts[1].rsplit("```", 1)[0].strip() if len(parts) == 2 else ""
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(value, dict):
        value = value.get("questions")
    if not isinstance(value, list) or not 2 <= len(value) <= 3:
        return None
    questions: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        questions.append(item.strip())
    return tuple(questions)


def generate_guided_questions(
    client: GeminiClient,
    *,
    inputs: GuidedQuestionInputs,
    persona,
) -> GuidedQuestions:
    """Generate B14 questions without raising or accessing persistence."""

    try:
        if persona is None or not inputs.is_qualified(persona):
            return fallback_questions()
        prompt = build_guided_questions_prompt(inputs=inputs, persona=persona)
        questions = _parse_questions(client.generate(prompt))
        return (
            GuidedQuestions(questions=questions)
            if questions is not None
            else fallback_questions()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("B14 guided question generation failed: %s", exc)
        return fallback_questions()
