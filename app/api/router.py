"""API route aggregation."""

from fastapi import APIRouter

from app.api.routes import beneficiaries, calls, campaigns, companies, dashboard, employers, interviews, verification, recruitment
from app.api.routes import workspaces, company_auth

api_router = APIRouter(prefix="/api")

api_router.include_router(companies.router)
api_router.include_router(employers.router)
api_router.include_router(beneficiaries.router)
api_router.include_router(campaigns.router)
api_router.include_router(calls.router)
api_router.include_router(interviews.router)
api_router.include_router(verification.router)
api_router.include_router(dashboard.router)
api_router.include_router(recruitment.router)
api_router.include_router(recruitment.webhook_router)
api_router.include_router(workspaces.router)
api_router.include_router(workspaces.scoped_router)
api_router.include_router(company_auth.router)
