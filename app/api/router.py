"""API route aggregation."""

from fastapi import APIRouter

from app.api.routes import beneficiaries, calls, campaigns, companies, dashboard, employers, interviews, verification

api_router = APIRouter(prefix="/api")

api_router.include_router(companies.router)
api_router.include_router(employers.router)
api_router.include_router(beneficiaries.router)
api_router.include_router(campaigns.router)
api_router.include_router(calls.router)
api_router.include_router(interviews.router)
api_router.include_router(verification.router)
api_router.include_router(dashboard.router)
