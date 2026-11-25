"""Public page shells; company data requires an authenticated scoped API."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory='app/dashboard/templates')

@router.get('/')
def home(request: Request):
    return templates.TemplateResponse(request=request, name='company_admin.html', context={})

@router.get('/recruitment')
def recruitment_demo(request: Request):
    return templates.TemplateResponse(request=request, name='recruitment.html', context={})

@router.get('/c/{company_id}')
def legacy_company(company_id: str):
    return RedirectResponse(url='/companies/' + company_id, status_code=303)

@router.get('/companies/{company_id}')
def company_workspace(request: Request, company_id: str):
    return templates.TemplateResponse(request=request, name='recruitment.html', context={'company_id': company_id})

@router.get('/login')
def company_login(request: Request):
    return templates.TemplateResponse(request=request, name='company_login.html', context={})
