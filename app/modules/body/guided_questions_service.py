"""Body-side assembly and daily persistence for B14 guided questions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.body import models
from app.modules.body.quests import taipei_today
from app.modules.body.resonance import stage_for_value
from app.modules.brain.guided_questions import GuidedQuestionInputs, generate_guided_questions
from app.modules.brain.loader import load_active_persona
from app.modules.brain.models import StoryBeat

CACHE_TTL_HOURS = 48


class SpiritNotFoundError(LookupError):
    """The requested spirit is missing or inactive."""


def _current_daily_context(db: Session, *, place_id: str, event_date):
    row = (
        db.query(models.DailyEventCache)
        .filter_by(place_id=place_id, event_date=event_date)
        .first()
    )
    if row is None or not row.content or row.content.get("is_fallback"):
        return None
    text = row.content.get("narrative_text")
    return text if isinstance(text, str) and text.strip() else None


def _resonance_stage(db: Session, *, player_id, place_id: str) -> int:
    row = (
        db.query(models.Resonance)
        .filter_by(player_id=player_id, spirit_id=place_id)
        .first()
    )
    return stage_for_value(row.resonance_value if row else 0)


def _current_story_beat(db: Session, *, player_id, spirit) -> str | None:
    if not spirit.character_id:
        return None
    row = (
        db.query(models.PlayersStoryProgress, StoryBeat)
        .join(StoryBeat, models.PlayersStoryProgress.beat_id == StoryBeat.beat_id)
        .filter(
            models.PlayersStoryProgress.player_id == player_id,
            StoryBeat.character_id == spirit.character_id,
        )
        .order_by(models.PlayersStoryProgress.triggered_at.desc())
        .first()
    )
    return row[1].narrative_directive if row else None


def get_suggested_questions(
    db: Session,
    client,
    *,
    place_id: str,
    player_id,
    now: datetime | None = None,
) -> dict:
    """Return one cached B14 result for the spirit and Taipei calendar day."""

    moment = now or datetime.now(timezone.utc)
    event_date = taipei_today(moment)
    spirit = db.query(models.Spirit).filter_by(spirit_id=place_id).first()
    if spirit is None or not spirit.is_active:
        raise SpiritNotFoundError(place_id)

    cached = (
        db.query(models.GuidedQuestionCache)
        .filter_by(place_id=place_id, event_date=event_date)
        .first()
    )
    if cached is not None:
        return dict(cached.content)

    persona = load_active_persona(db, place_id)
    inputs = GuidedQuestionInputs(
        daily_context=_current_daily_context(
            db, place_id=place_id, event_date=event_date
        ),
        resonance_stage=_resonance_stage(
            db, player_id=player_id, place_id=place_id
        ),
        story_beat=_current_story_beat(db, player_id=player_id, spirit=spirit),
        event_date=event_date,
    )
    content = generate_guided_questions(client, inputs=inputs, persona=persona)
    payload = {
        "questions": list(content.questions),
        "is_fallback": content.is_fallback,
    }
    row = models.GuidedQuestionCache(
        place_id=place_id,
        event_date=event_date,
        content=payload,
        generated_at=moment,
        expires_at=moment + timedelta(hours=CACHE_TTL_HOURS),
    )

    try:
        with db.begin_nested():
            db.add(row)
        db.commit()
        return payload
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(models.GuidedQuestionCache)
            .filter_by(place_id=place_id, event_date=event_date)
            .one()
        )
        return dict(existing.content)
