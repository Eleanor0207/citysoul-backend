from fastapi import FastAPI

from app.modules.body.router import router as body_router

app = FastAPI(title="城市靈魂 AR — Backend", version="0.1.0-sprint1")

app.include_router(body_router)


@app.get("/health")
def health():
    return {"status": "ok"}
