"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes import pages
from app.core.config import settings
from app.db.session import init_db
from app.intelligence.engine import try_register_default_engine
from app.services.scheduler_service import InterviewScheduler

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.include_router(api_router)
# Mounted at the root, outside /api: the two pages an administrator opens are
# the product, not an API surface.
app.include_router(pages.router)
app.mount("/static", StaticFiles(directory="app/dashboard/static"), name="static")
scheduler = InterviewScheduler()


@app.on_event("startup")
def startup() -> None:
    init_db()
    try_register_default_engine()
    if settings.scheduler_enabled:
        scheduler.start()


@app.on_event("shutdown")
def shutdown() -> None:
    if settings.scheduler_enabled:
        scheduler.stop()


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
