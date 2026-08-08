"""
B8 記憶摘要 nightly batch 的執行入口（#40）。

用法：

    uv run python -m scripts.run_memory_batch

排程器（cron／Cloud Scheduler）呼叫這支。**冪等**——重試是安全的，只處理還沒
被壓縮過的記錄，所以第二次執行找不到東西可做。

⚠️ 這支腳本只負責「跑一次」。實際的排程佈署屬部署設定，不在 #40 的範圍。
"""
import logging

from app.core.database import SessionLocal
from app.modules.brain.gemini import VertexAIGeminiClient
from app.modules.brain.memory_batch import run_memory_batch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    session = SessionLocal()
    try:
        result = run_memory_batch(session, VertexAIGeminiClient())
    finally:
        session.close()

    logger.info(
        "記憶摘要批次完成：處理 %s 組、跳過 %s 組，記憶列數 %s → %s",
        result.groups_processed,
        result.groups_skipped,
        result.rows_before,
        result.rows_after,
    )
    for failure in result.failures:
        logger.warning("跳過：%s", failure)


if __name__ == "__main__":
    main()
