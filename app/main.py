from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.modules.body.router import router as body_router

app = FastAPI(title="城市靈魂 AR — Backend", version="0.1.0-sprint1")

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
