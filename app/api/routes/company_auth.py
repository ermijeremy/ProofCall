"""Company sign-in, forced first-login password change and session revocation."""
import hashlib
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, case, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from app.core.company_access import (COOKIE, check_origin, csrf_token, hash_password,
    session_user, start_session, verify_password)
from app.db.session import get_db
from app.models.employer import Employer
from app.models.recruitment import CompanyUser, CompanySession, LoginAttempt
from app.api.routes.recruitment import PrivateRoute

router = APIRouter(prefix='/company-auth', route_class=PrivateRoute)

class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)

class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def throttle(db, request, username):
    now = datetime.utcnow()
    for value, limit in [('ip:' + (request.client.host if request.client else 'unknown'), 40), ('user:' + username, 10)]:
        key = hashlib.sha256(value.encode()).hexdigest()
        if db.bind.dialect.name == 'sqlite':
            expired = LoginAttempt.window_started < now - timedelta(minutes=15)
            statement = sqlite_insert(LoginAttempt).values(key=key, attempts=1, window_started=now)
            statement = statement.on_conflict_do_update(index_elements=['key'], set_={
                'attempts': case((expired, 1), else_=LoginAttempt.attempts + 1),
                'window_started': case((expired, now), else_=LoginAttempt.window_started),
            }).returning(LoginAttempt.attempts)
            attempts = db.execute(statement).scalar_one()
        else:
            row = db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
            if row is None:
                row = LoginAttempt(key=key, attempts=0, window_started=now)
                db.add(row)
            if row.window_started < now - timedelta(minutes=15):
                row.attempts = 0
                row.window_started = now
            row.attempts += 1
            attempts = row.attempts
        db.commit()
        if attempts > limit:
            raise HTTPException(status_code=429, detail='Too many sign-in attempts. Try again in 15 minutes.')


@router.post('/login')
def login(data: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    username = data.username.strip().lower()
    throttle(db, request, username)
    user = db.get(CompanyUser, username)
    # Run the same expensive KDF even for an unknown username.
    encoded = user.password_hash if user else 'scrypt$32768$8$3$' + '00'*16 + '$' + '00'*64
    if not verify_password(data.password, encoded) or user is None:
        raise HTTPException(status_code=401, detail='Incorrect username or password')
    old = request.cookies.get(COOKIE, '')
    if old:
        db.execute(delete(CompanySession).where(CompanySession.token_hash == hashlib.sha256(old.encode()).hexdigest()))
    return start_session(request, response, db, user)


@router.get('/session')
def session(request: Request, db: Session = Depends(get_db)):
    user = session_user(request, db, allow_password_change=True)
    company = db.get(Employer, user.company_id)
    return {'username': user.username, 'company_id': user.company_id, 'company_name': company.name,
            'must_change_password': user.must_change_password,
            'csrf_token': csrf_token(request.cookies[COOKIE]), 'url': '/companies/' + user.company_id}


@router.post('/password')
def change_password(data: PasswordInput, request: Request, response: Response, db: Session = Depends(get_db)):
    user = session_user(request, db, allow_password_change=True)
    throttle(db, request, user.username)
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail='Current password is incorrect')
    if data.current_password == data.new_password:
        raise HTTPException(status_code=400, detail='Choose a different password')
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    db.execute(delete(CompanySession).where(CompanySession.username == user.username))
    return start_session(request, response, db, user)


@router.post('/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    session_user(request, db, allow_password_change=True)
    db.execute(delete(CompanySession).where(CompanySession.token_hash == hashlib.sha256(request.cookies[COOKIE].encode()).hexdigest()))
    db.commit()
    response.delete_cookie(COOKIE, path='/')
    return {'signed_out': True}
