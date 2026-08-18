"""Run the B9 daily event producer once.

Cloud Scheduler can invoke this management entry point once each morning in
Asia/Taipei. Re-running it for the same day is safe because the cache primary
key is ``(place_id, event_date)`` and refresh uses insert-then-update handling.

Usage::

    .venv/Scripts/python.exe -m scripts.run_daily_event_batch
"""

import logging

from app.core.database import SessionLocal
from app.modules.body.daily_event_batch import run_daily_event_batch
from app.modules.brain.gemini import VertexAIGeminiClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    db = SessionLocal()
    try:
        result = run_daily_event_batch(db, VertexAIGeminiClient())
    finally:
        db.close()

    logger.info(
        "daily event batch date=%s processed=%s failed=%s",
        result.event_date,
        result.processed,
        len(result.failed),
    )
    if result.failed:
        logger.warning("daily event batch failed spirits=%s", result.failed)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
