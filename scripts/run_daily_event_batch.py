"""Run the B9 daily event producer once.

Cloud Scheduler can invoke this management entry point once each morning in
Asia/Taipei. Re-running it for the same day is safe because the cache primary
key is ``(place_id, event_date)`` and refresh uses insert-then-update handling.

Usage::

    .venv/Scripts/python.exe -m scripts.run_daily_event_batch
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.core.database import SessionLocal
from app.modules.body.official_announcements import (
    OfficialSource,
    refresh_official_announcements,
)
from app.modules.body.quests import taipei_today
from app.modules.body.daily_event_batch import run_daily_event_batch
from app.modules.brain.gemini import VertexAIGeminiClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_official_sources() -> list[OfficialSource]:
    """
    讀官方公告來源清單。檔案不存在或清單為空都是**正常狀態**——2026-08-21 起
    網址待提供（見 content/official_sources.yaml 的說明）。
    """
    path = Path(__file__).resolve().parent.parent / "content" / "official_sources.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        OfficialSource(place_id=entry["place_id"], feed_url=entry["feed_url"])
        for entry in (raw.get("sources") or [])
    ]


def main() -> None:
    db = SessionLocal()
    try:
        # ── 先抓官方公告，再生成 ───────────────────────────────────────
        #
        # 順序很重要：抓取器把公告寫進 daily_event_calendars，而生成要讀那張表。
        # 反過來的話，今天抓到的公告要等到明天才會出現在玩家眼前。
        #
        # 抓取失敗不中斷生成——當日情境本來就有節慶日曆與輪播池兩個來源，
        # 官網掛掉不該讓整批都不跑（SDD §20.3.3）。
        try:
            added = refresh_official_announcements(
                db,
                load_official_sources(),
                today=taipei_today(datetime.now(timezone.utc)),
            )
            if added:
                logger.info("official announcements added=%s", added)
        except Exception:  # noqa: BLE001
            logger.exception("official announcement refresh failed; continuing")

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
