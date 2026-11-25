"""Administrator provisions companies and initial company logins."""
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.company_access import admin_access, reset_password
from app.db.session import get_db
from app.models.employer import Employer
from app.models.recruitment import CompanyUser
from sqlalchemy.exc import IntegrityError
from app.api.routes.recruitment import PrivateRoute, router as campaign_router

router = APIRouter(prefix='/company-workspaces', dependencies=[Depends(admin_access)], route_class=PrivateRoute)
scoped_router = APIRouter(prefix='/workspaces/{company_id}')
scoped_router.include_router(campaign_router)


class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=3, max_length=80, pattern=r'^[A-Za-z0-9_.@-]+$')


class UsernameInput(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r'^[A-Za-z0-9_.@-]+$')


@router.get('')
def list_workspaces(db: Session = Depends(get_db)):
    users = {u.company_id: u.username for u in db.scalars(select(CompanyUser))}
    return {'companies': [{'company_id': c.company_id, 'name': c.name, 'username': users.get(c.company_id), 'url': '/companies/' + c.company_id}
                          for c in db.scalars(select(Employer).order_by(Employer.created_at.desc()))]}


@router.post('', status_code=201)
def create_workspace(data: CompanyInput, db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail='Enter a company name')
    company = Employer(company_id='co_' + uuid4().hex, name=name)
    user = CompanyUser(username=data.username.lower(), company_id=company.company_id, password_hash='')
    db.add_all([company, user])
    try:
        password = reset_password(db, user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail='Choose a different username') from None
    return {'company_id': company.company_id, 'name': name, 'url': '/companies/' + company.company_id,
            'username': user.username, 'temporary_password': password}


@router.post('/{company_id}/reset-password')
def reset_company_password(company_id: str, db: Session = Depends(get_db)):
    if db.get(Employer, company_id) is None:
        raise HTTPException(status_code=404, detail='Company not found')
    user = db.scalar(select(CompanyUser).where(CompanyUser.company_id == company_id))
    if user is None:
        raise HTTPException(status_code=404, detail='Company login has not been provisioned')
    return {'username': user.username, 'temporary_password': reset_password(db, user)}


@router.post('/{company_id}/initial-login', status_code=201)
def initial_login(company_id: str, data: UsernameInput, db: Session = Depends(get_db)):
    if db.get(Employer, company_id) is None:
        raise HTTPException(status_code=404, detail='Company not found')
    if db.scalar(select(CompanyUser).where(CompanyUser.company_id == company_id)):
        raise HTTPException(status_code=409, detail='Company already has a login; use password reset')
    user = CompanyUser(username=data.username.lower(), company_id=company_id, password_hash='')
    db.add(user)
    try:
        password = reset_password(db, user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail='Choose a different username') from None
    return {'username': user.username, 'temporary_password': password}
