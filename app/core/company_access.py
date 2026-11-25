"""Password authentication and company ownership checks for the customer portal."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.employer import Employer
from app.models.recruitment import CompanyUser, CompanySession, RecruitmentCampaign, RecruitmentCall

COOKIE = 'proofcall_session'
SESSION_SECONDS = 8 * 3600


def is_admin(authorization: str | None) -> bool:
    token = settings.recruitment_operator_token
    return len(token) >= 32 and bool(authorization) and hmac.compare_digest(
        authorization.encode(), ('Bearer ' + token).encode())


def admin_access(authorization: str | None = Header(default=None)):
    if not is_admin(authorization):
        raise HTTPException(status_code=401, detail='Administrator access token required')


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=3, maxmem=64*1024*1024)
    return 'scrypt$32768$8$3$' + salt.hex() + '$' + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt, expected = encoded.split('$')
        if (kind, n, r, p) != ('scrypt', '32768', '8', '3'):
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64*1024*1024)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def csrf_token(raw: str) -> str:
    return hmac.new(raw.encode(), b'proofcall-csrf', hashlib.sha256).hexdigest()


def check_origin(request: Request):
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(status_code=403, detail='Cross-origin request denied')


def session_user(request: Request, db: Session, *, allow_password_change=False):
    raw = request.cookies.get(COOKIE, '')
    row = db.get(CompanySession, hashlib.sha256(raw.encode()).hexdigest()) if raw else None
    user = db.get(CompanyUser, row.username) if row and row.expires_at > datetime.utcnow() else None
    if user is None:
        raise HTTPException(status_code=401, detail='Sign in with your company username and password')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        check_origin(request)
        provided = request.headers.get('x-csrf-token', '')
        if not hmac.compare_digest(provided, csrf_token(raw)):
            raise HTTPException(status_code=403, detail='Session verification failed; sign in again')
    if user.must_change_password and not allow_password_change:
        raise HTTPException(status_code=403, detail='Change your temporary password before opening the workspace')
    return user


def start_session(request: Request, response, db: Session, user: CompanyUser):
    raw = secrets.token_urlsafe(48)
    db.add(CompanySession(token_hash=hashlib.sha256(raw.encode()).hexdigest(), username=user.username,
                          expires_at=datetime.utcnow() + timedelta(seconds=SESSION_SECONDS)))
    db.commit()
    response.set_cookie(COOKIE, raw, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == 'https', samesite='strict', path='/')
    response.headers['Cache-Control'] = 'no-store'
    company = db.get(Employer, user.company_id)
    return {'username': user.username, 'company_id': user.company_id, 'company_name': company.name,
            'must_change_password': user.must_change_password, 'csrf_token': csrf_token(raw),
            'url': '/companies/' + user.company_id}


def reset_password(db: Session, user: CompanyUser) -> str:
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    db.execute(delete(CompanySession).where(CompanySession.username == user.username))
    db.commit()
    return password


def workspace_access(request: Request, authorization: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    company_id = request.path_params.get('company_id')
    if not company_id and len(settings.recruitment_operator_token) < 32:
        raise HTTPException(status_code=503, detail='Administrator access is not configured')
    if not is_admin(authorization):
        user = session_user(request, db)
        if not company_id or user.company_id != company_id:
            raise HTTPException(status_code=404, detail='Workspace not found')
    if company_id:
        if db.get(Employer, company_id) is None:
            raise HTTPException(status_code=404, detail='Workspace not found')
        campaign_id = request.path_params.get('campaign_id')
        request_id = request.path_params.get('request_id')
        if request_id:
            call = db.get(RecruitmentCall, request_id)
            if call is None:
                raise HTTPException(status_code=404, detail='Call not found')
            campaign_id = call.campaign_id
        if campaign_id:
            campaign = db.get(RecruitmentCampaign, campaign_id)
            if campaign is None or campaign.company_id != company_id:
                raise HTTPException(status_code=404, detail='Campaign not found')
