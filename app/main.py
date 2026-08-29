"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import settings
from app.db.session import init_db
from app.intelligence.engine import try_register_default_engine

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="app/dashboard/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    try_register_default_engine()


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
