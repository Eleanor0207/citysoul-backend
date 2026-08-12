from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.modules.body.quota import QuotaExceededError
from app.modules.body.router import router as body_router

app = FastAPI(title="城市靈魂 AR — Backend", version="0.1.0-sprint1")


def quota_exceeded_response(exc: QuotaExceededError) -> JSONResponse:
    """
    把配額例外轉成 429。

    做成獨立函式而不是只寫在 handler 裡，是為了讓「回應內容不洩漏什麼」這件事
    可以被直接測試，不必先架一支會超額的端點。

    ⚠️ **回應只含這個玩家自己的狀態。** 沒有 `player_id`、沒有全站統計，
    也**沒有上限值**——上限是分級設定，屬於內部組態。回傳它等於讓任何人用一次
    超額請求就問出我們的商業分級。

    `Retry-After` 用秒數而不是日期字串：那是 HTTP 標準的形式，客戶端不需要
    再解析時區。
    """
    from datetime import datetime, timezone

    retry_after = max(int((exc.reset_at - datetime.now(timezone.utc)).total_seconds()), 1)

    return JSONResponse(
        status_code=429,
        content={
            "detail": "今日額度已用完",
            "resource": exc.resource_type,
            "reset_at": exc.reset_at.isoformat(),
        },
        headers={"Retry-After": str(retry_after)},
    )


@app.exception_handler(QuotaExceededError)
async def _handle_quota_exceeded(request: Request, exc: QuotaExceededError) -> JSONResponse:
    return quota_exceeded_response(exc)

if settings.app_env == "local":
    # 正式版client是Android/iOS（不受CORS限制），這裡只為了本機用瀏覽器
    # 開發/除錯（例如 Flutter Web 開發模式）時能直接打本機API，不代表
    # 產品支援網頁版。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(body_router)

if settings.dev_console_enabled:
    # 開發測試主控台。整組 `include_in_schema=False`，所以它不會進
    # `contracts/openapi.json`——契約是後端與 citysoul-client 之間的東西，
    # 測試工具不該出現在裡面。
    from app.modules.dev.router import router as dev_router

    app.include_router(dev_router)


@app.get("/health")
def health():
    return {"status": "ok"}
