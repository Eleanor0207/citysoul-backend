"""Scheduled producer for every active spirit's daily event cache."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.body.daily_event_service import refresh_daily_event
from app.modules.body.models import Spirit
from app.modules.body.quests import taipei_today
from app.modules.brain.daily_event_sources import build_daily_event_inputs

logger = logging.getLogger(__name__)


@dataclass
class DailyEventBatchResult:
    event_date: str
    processed: int = 0
    failed: list[str] = field(default_factory=list)


def run_daily_event_batch(db: Session, client, *, now: datetime | None = None) -> DailyEventBatchResult:
    """Refresh one deterministic event row for each active spirit.

    A failed spirit is isolated with a rollback and does not prevent later
    active spirits from being attempted. The cache primary key remains the
    idempotency authority inside ``refresh_daily_event``.
    """
    moment = now or datetime.now(timezone.utc)
    event_date = taipei_today(moment)
    result = DailyEventBatchResult(event_date=event_date.isoformat())

    spirits = (
        db.query(Spirit)
        .filter(Spirit.is_active.is_(True))
        .order_by(Spirit.spirit_id)
        .all()
    )
    for spirit in spirits:
        try:
            inputs = build_daily_event_inputs(
                db, place_id=spirit.spirit_id, event_date=event_date
            )
            refresh_daily_event(
                db,
                client,
                place_id=spirit.spirit_id,
                inputs=inputs,
                now=moment,
            )
            result.processed += 1
        except Exception:
            db.rollback()
            result.failed.append(spirit.spirit_id)
            logger.exception("daily event refresh failed for active spirit %s", spirit.spirit_id)

    return result
