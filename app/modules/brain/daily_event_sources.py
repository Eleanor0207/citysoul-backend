"""Deterministic, reviewed input producers for B9 daily events."""

from __future__ import annotations

from datetime import date, timedelta

from lunardate import LunarDate
from sqlalchemy.orm import Session

from app.modules.brain.daily_event import DailyEventInputs
from app.modules.brain.models import DailyEventCalendar, DailyEventCuratedNote


def _month_day_in_range(
    current: tuple[int, int], start: tuple[int, int], end: tuple[int, int]
) -> bool:
    """Match an annual range, including ranges that cross New Year's Day."""
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _calendar_row_matches(row: DailyEventCalendar, event_date: date) -> bool:
    if row.date_rule == "gregorian_range":
        return bool(row.start_date <= event_date <= row.end_date)

    if row.date_rule == "gregorian_fixed":
        current = (event_date.month, event_date.day)
    elif row.date_rule in {"lunar_fixed", "lunar_month_end"}:
        try:
            lunar = LunarDate.fromSolarDate(
                event_date.year, event_date.month, event_date.day
            )
        except ValueError:
            # The dependency intentionally has a bounded supported range. An
            # out-of-range date simply has no lunar producer match.
            return False
        if row.date_rule == "lunar_month_end":
            if lunar.month != row.start_month:
                return False
            next_date = event_date + timedelta(days=1)
            try:
                next_lunar = LunarDate.fromSolarDate(
                    next_date.year, next_date.month, next_date.day
                )
            except ValueError:
                return False
            return (next_lunar.year, next_lunar.month) != (lunar.year, lunar.month)
        current = (lunar.month, lunar.day)
    else:  # Defensive against rows written outside the ORM.
        return False

    return _month_day_in_range(
        current,
        (row.start_month, row.start_day),
        (row.end_month, row.end_day),
    )


def build_daily_event_inputs(
    db: Session, *, place_id: str, event_date: date
) -> DailyEventInputs:
    """Assemble only active, human-approved source rows for one place/day.

    The rotation index is derived solely from the Taipei event date and the
    stable editor-provided order. No per-day cursor or derived state is stored.
    """
    calendar_rows = (
        db.query(DailyEventCalendar)
        .filter_by(place_id=place_id, active=True)
        .order_by(DailyEventCalendar.calendar_id)
        .all()
    )

    festivals: list[str] = []
    official_events: list[str] = []
    for row in calendar_rows:
        if not _calendar_row_matches(row, event_date):
            continue
        if row.event_type == "festival":
            festivals.append(row.title)
        elif row.event_type == "official_event":
            official_events.append(row.title)

    notes = (
        db.query(DailyEventCuratedNote)
        .filter_by(place_id=place_id, active=True)
        .order_by(DailyEventCuratedNote.rotation_order, DailyEventCuratedNote.note_id)
        .all()
    )
    curated_notes = []
    if notes:
        curated_notes = [notes[event_date.toordinal() % len(notes)].note_text]

    return DailyEventInputs(
        festival="、".join(festivals) if festivals else None,
        official_events=official_events,
        curated_notes=curated_notes,
    )
