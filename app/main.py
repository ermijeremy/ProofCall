"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from app.core.company_access import is_admin

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


@app.middleware('http')
async def protect_legacy_admin_apis(request, call_next):
    path = request.url.path
    scoped = any(path == p or path.startswith(p + '/') for p in (
        '/api/recruitment', '/api/workspaces', '/api/company-workspaces', '/api/company-auth'))
    if path.startswith('/api/') and not scoped and not is_admin(request.headers.get('authorization')):
        return JSONResponse({'detail': 'Administrator access required'}, status_code=401,
                            headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if path.startswith('/api/') or path.startswith('/companies/') or path == '/login':
        response.headers['Cache-Control'] = 'no-store'
    return response


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
