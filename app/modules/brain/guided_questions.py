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

# 兩層距離，對應 Body 層的兩種憑證：sense_token（150m 感應圈）與 encounter_token
# （50m 在場成立）。150m 外玩家未必看得到建築物，所以那一圈的提問不可以依賴目視
# 細節；50m 才問得出「柱子上的痕跡」這種題目。
PROXIMITY_FAR = "far"
PROXIMITY_NEAR = "near"

# Player-facing copy (BE 13, reviewed 2026-08-21). These are shown whenever B14
# cannot generate: no qualified daily context, an unusable model response, or an
# error. They must read as something the spirit itself would ask, and must hold up
# for every persona, since the fallback path may run with no persona at all.
_GENERIC_FALLBACK_QUESTIONS = (
    "這裡最近有什麼不一樣？",
    "你在這裡最久是什麼樣子？",
)

# Used when a persona is available: its own quest_themes make a far better fallback
# than the generic pair, and they are already human-reviewed content on the card.
_THEME_QUESTION_TEMPLATES = (
    "可以跟我說說{theme}嗎？",
    "{theme}這件事，你會想從哪裡講起？",
    "關於{theme}，有什麼是站在這裡才看得出來的？",
)


# 這兩段是遠近兩圈唯一的 prompt 差異。遠圈拿掉一切「你眼前看得到什麼」的假設，
# 因為 150m 可能還隔著一個街廓。
_PROXIMITY_INSTRUCTIONS = {
    PROXIMITY_FAR: (
        "玩家在 150 公尺外的感應範圍，可能還看不到這個地標本身。"
        "提問不可以要求玩家觀察眼前的細節或指出具體構造，改問這個地方的來歷、"
        "氣氛、周邊街區，或它跟今天的關係。"
    ),
    PROXIMITY_NEAR: (
        "玩家就站在這個地標前面（50 公尺內）。"
        "提問可以直接指向眼前看得到的東西——構造、材料、空間、今天的樣子。"
    ),
}


@dataclass(frozen=True)
class GuidedQuestionInputs:
    """Whitelisted context supplied by the Body layer."""

    daily_context: str | None = None
    resonance_stage: int = 0
    story_beat: str | None = None
    event_date: date | None = None
    proximity: str = PROXIMITY_NEAR

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


def _quote_theme(theme: str) -> str:
    """Wrap a theme in corner brackets unless it already contains some."""

    return theme if "「" in theme or "」" in theme else f"「{theme}」"


def _card_fallback_questions(persona) -> tuple[str, ...]:
    """Return the persona card's own authored fallback questions, if any."""

    try:
        authored = getattr(persona, "guided_question_fallback", None) or []
        cleaned = [str(item).strip() for item in authored if str(item).strip()]
    except Exception:  # noqa: BLE001 - persona is caller-supplied, never trusted here
        return ()
    return tuple(cleaned[:3]) if len(cleaned) >= 2 else ()


def _theme_fallback_questions(persona) -> tuple[str, ...]:
    """Build up to three questions from the persona card's own quest_themes."""

    try:
        themes = getattr(persona, "quest_themes", None) or []
        cleaned = [str(theme).strip() for theme in themes if str(theme).strip()]
    except Exception:  # noqa: BLE001 - persona is caller-supplied, never trusted here
        return ()
    if not cleaned:
        return ()
    questions = [
        _THEME_QUESTION_TEMPLATES[index % len(_THEME_QUESTION_TEMPLATES)].format(
            theme=_quote_theme(theme)
        )
        for index, theme in enumerate(cleaned[:3])
    ]
    if len(questions) == 1:
        questions.append(_GENERIC_FALLBACK_QUESTIONS[0])
    return tuple(questions)


def fallback_questions(persona=None, *, proximity: str = PROXIMITY_NEAR) -> GuidedQuestions:
    """Return a non-empty, player-ready fallback of two or three questions.

    近圈（50m）依序試：卡上寫好的 `guided_question_fallback` → 用 `quest_themes`
    組出來的句子 → 通用兩句。遠圈（150m）直接用通用兩句：卡上那兩層的內容都預設
    玩家站在建築物前面，150m 外不成立。
    """

    if proximity == PROXIMITY_FAR:
        return GuidedQuestions(questions=_GENERIC_FALLBACK_QUESTIONS, is_fallback=True)
    return GuidedQuestions(
        questions=(
            _card_fallback_questions(persona)
            or _theme_fallback_questions(persona)
            or _GENERIC_FALLBACK_QUESTIONS
        ),
        is_fallback=True,
    )


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
        "每則提問不超過 15 個字。",
        "提問要有當日變化、符合角色口吻，不得觸碰人格卡中的 taboos，也不得把角色寫成 not_this_character 所排除的身分。",
        _PROXIMITY_INSTRUCTIONS.get(inputs.proximity, _PROXIMITY_INSTRUCTIONS[PROXIMITY_NEAR]),
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
            return fallback_questions(persona, proximity=inputs.proximity)
        prompt = build_guided_questions_prompt(inputs=inputs, persona=persona)
        questions = _parse_questions(client.generate(prompt))
        return (
            GuidedQuestions(questions=questions)
            if questions is not None
            else fallback_questions(persona, proximity=inputs.proximity)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("B14 guided question generation failed: %s", exc)
        return fallback_questions(persona, proximity=inputs.proximity)
