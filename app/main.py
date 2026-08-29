"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}

