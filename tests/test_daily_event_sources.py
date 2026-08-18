from datetime import date, datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.modules.body.daily_event_batch import run_daily_event_batch
from app.modules.body.models import Spirit
from app.modules.brain.daily_event_sources import build_daily_event_inputs
from app.modules.brain.daily_event import generate_daily_event_content
from app.modules.brain.gemini import FakeGeminiClient
from app.modules.brain.models import DailyEventCalendar, DailyEventCuratedNote


@pytest.fixture
def source_place(db_session):
    place_id = f"source-test-{uuid4()}"
    yield place_id
    db_session.query(DailyEventCalendar).filter_by(place_id=place_id).delete()
    db_session.query(DailyEventCuratedNote).filter_by(place_id=place_id).delete()
    db_session.commit()


def _reviewed(**kwargs):
    return dict(active=True, reviewed_by="test", reviewed_at=datetime.now(timezone.utc), **kwargs)


def test_fallback_uses_persona_text_before_generic_text():
    from types import SimpleNamespace

    persona = SimpleNamespace(daily_event_fallback="TEST_PERSONA_FALLBACK")
    result = generate_daily_event_content(
        FakeGeminiClient(response="SHOULD_NOT_BE_USED"),
        place_id="test-place",
        event_date=date(2026, 8, 19),
        persona=persona,
    )

    assert result.is_fallback is True
    assert result.narrative_text == "TEST_PERSONA_FALLBACK"


def test_calendar_matches_gregorian_lunar_and_official_ranges(db_session, source_place):
    db_session.add_all(
        [
            DailyEventCalendar(
                place_id=source_place,
                event_type="festival",
                title="TEST_GREGORIAN_FESTIVAL",
                date_rule="gregorian_fixed",
                start_month=8,
                start_day=19,
                end_month=8,
                end_day=19,
                **_reviewed(),
            ),
            DailyEventCalendar(
                place_id=source_place,
                event_type="festival",
                title="TEST_LUNAR_FESTIVAL",
                date_rule="lunar_fixed",
                start_month=7,
                start_day=7,
                end_month=7,
                end_day=7,
                **_reviewed(),
            ),
            DailyEventCalendar(
                place_id=source_place,
                event_type="official_event",
                title="TEST_OFFICIAL_EVENT",
                date_rule="gregorian_range",
                start_date=date(2026, 8, 18),
                end_date=date(2026, 8, 20),
                **_reviewed(),
            ),
            DailyEventCalendar(
                place_id=source_place,
                event_type="festival",
                title="TEST_LUNAR_MONTH_END",
                date_rule="lunar_month_end",
                start_month=12,
                end_month=12,
                **_reviewed(),
            ),
        ]
    )
    db_session.commit()

    inputs = build_daily_event_inputs(
        db_session, place_id=source_place, event_date=date(2026, 8, 19)
    )

    assert set(inputs.festival.split("、")) == {
        "TEST_GREGORIAN_FESTIVAL",
        "TEST_LUNAR_FESTIVAL",
    }
    assert inputs.official_events == ["TEST_OFFICIAL_EVENT"]

    new_year_eve = build_daily_event_inputs(
        db_session, place_id=source_place, event_date=date(2026, 2, 16)
    )
    assert "TEST_LUNAR_MONTH_END" in new_year_eve.festival


def test_curated_rotation_is_date_deterministic_and_ignores_inactive_rows(
    db_session, source_place
):
    db_session.add_all(
        [
            DailyEventCuratedNote(
                place_id=source_place,
                rotation_order=0,
                note_text="TEST_NOTE_ZERO",
                **_reviewed(),
            ),
            DailyEventCuratedNote(
                place_id=source_place,
                rotation_order=1,
                note_text="TEST_NOTE_ONE",
                **_reviewed(),
            ),
            DailyEventCuratedNote(
                place_id=source_place,
                rotation_order=2,
                note_text="TEST_INACTIVE_NOTE",
                active=False,
            ),
        ]
    )
    db_session.commit()

    event_date = date(2026, 8, 19)
    first = build_daily_event_inputs(
        db_session, place_id=source_place, event_date=event_date
    )
    second = build_daily_event_inputs(
        db_session, place_id=source_place, event_date=event_date
    )

    assert first.curated_notes == second.curated_notes
    assert first.curated_notes[0] in {"TEST_NOTE_ZERO", "TEST_NOTE_ONE"}


def test_batch_refreshes_every_active_spirit(db_session):
    spirit_ids = [f"batch-test-{uuid4()}", f"batch-test-{uuid4()}"]
    inactive_id = f"batch-inactive-{uuid4()}"
    db_session.add_all(
        [
            Spirit(
                spirit_id=spirit_id,
                display_name=spirit_id,
                latitude=25.0,
                longitude=121.0,
                is_active=True,
            )
            for spirit_id in spirit_ids
        ]
        + [
            Spirit(
                spirit_id=inactive_id,
                display_name=inactive_id,
                latitude=25.0,
                longitude=121.0,
                is_active=False,
            )
        ]
    )
    db_session.commit()

    with patch("app.modules.body.daily_event_batch.refresh_daily_event") as refresh:
        result = run_daily_event_batch(
            db_session,
            FakeGeminiClient(),
            now=datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc),
        )

    assert result.processed >= 2
    assert result.failed == []
    refreshed_ids = {call.kwargs["place_id"] for call in refresh.call_args_list}
    assert set(spirit_ids).issubset(refreshed_ids)
    assert inactive_id not in refreshed_ids

    db_session.query(Spirit).filter(Spirit.spirit_id.in_(spirit_ids + [inactive_id])).delete(
        synchronize_session=False
    )
    db_session.commit()
