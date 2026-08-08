from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.modules.body.quota import QuotaExceededError
from app.modules.body.router import router as body_router

app = FastAPI(title="城市靈魂 AR — Backend", version="0.1.0-sprint1")


@app.exception_handler(QuotaExceededError)
async def quota_exceeded_handler(request: Request, exc: QuotaExceededError) -> JSONResponse:
    """
    #32：配額超額轉 429。回應內容只帶這個玩家自己的狀態（資源類型、他自己
    這個分級的上限、重置時間）——不帶 tier_id、player_id 或任何其他玩家的
    數字，429 不該洩漏這些。
    """
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"quota exceeded for {exc.resource_type}",
            "resource_type": exc.resource_type,
            "limit": exc.limit,
            "reset_at": exc.reset_at.isoformat(),
        },
    )

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


@app.get("/health")
def health():
    return {"status": "ok"}
