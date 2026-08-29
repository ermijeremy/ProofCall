"""API route aggregation."""

from fastapi import APIRouter

from app.api.routes import beneficiaries, calls, campaigns, dashboard, employers, verification

api_router = APIRouter(prefix="/api")

api_router.include_router(employers.router)
api_router.include_router(beneficiaries.router)
api_router.include_router(campaigns.router)
api_router.include_router(calls.router)
api_router.include_router(verification.router)
api_router.include_router(dashboard.router)
