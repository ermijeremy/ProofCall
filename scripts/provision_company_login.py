"""Provision a local company login and optionally attach legacy BeSingularity campaigns.

Stores temporary credentials in an ignored private file; never prints passwords or dials.
Existing passwords are preserved on repeat runs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
from dotenv import load_dotenv
load_dotenv(root / '.env')
from sqlalchemy import select
from app.db.session import engine, init_db, SessionLocal
from app.models.employer import Employer
from app.models.recruitment import CompanyUser, RecruitmentCampaign, RecruitmentCall
from app.core.company_access import reset_password

parser = argparse.ArgumentParser()
parser.add_argument('--company-name', required=True)
parser.add_argument('--username', required=True)
parser.add_argument('--assign-besingularity-campaigns', action='store_true')
parser.add_argument('--reset-password', action='store_true', help='Explicitly reset an existing company login and revoke its sessions')
args = parser.parse_args()
username = args.username.strip().lower()
if not re.fullmatch(r'[a-z0-9_.-]{3,80}', username) or not args.company_name.strip():
    raise SystemExit('Supply a company name and a username using 3–80 letters, numbers, dots, underscores or hyphens.')
if args.assign_besingularity_campaigns and args.company_name.lower() != 'besingularity':
    raise SystemExit('Only BeSingularity can receive the existing recruitment campaigns.')
if engine.dialect.name != 'sqlite':
    raise SystemExit('This local provisioning script requires SQLite.')
backup_dir = root / '.campaign-backups'; backup_dir.mkdir(mode=0o700, exist_ok=True)
backup_path = backup_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
with engine.connect() as conn, sqlite3.connect(backup_path) as destination:
    conn.connection.driver_connection.backup(destination)
backup_path.chmod(0o600)
init_db()
with SessionLocal() as db:
    if db.scalar(select(RecruitmentCall).where(RecruitmentCall.status.not_in(('completed', 'failed', 'cancelled', 'rejected'))).limit(1)):
        raise SystemExit('Finish or reconcile outstanding calls before migrating campaigns.')
    company = db.scalar(select(Employer).where(Employer.name == args.company_name.strip()))
    if company is None:
        company = Employer(company_id='co_' + uuid4().hex, name=args.company_name.strip())
        db.add(company)
        db.flush()
    user = db.get(CompanyUser, username)
    if user and user.company_id != company.company_id:
        raise SystemExit('That username belongs to another company.')
    if user is None:
        if db.scalar(select(CompanyUser).where(CompanyUser.company_id == company.company_id)):
            raise SystemExit('This company already has a login; use the administrator password reset.')
        user = CompanyUser(username=username, company_id=company.company_id, password_hash='')
        db.add(user)
        needs_password = True
    else:
        needs_password = args.reset_password
    if needs_password:
        password = reset_password(db, user)
        credentials_dir = root / '.company-credentials'
        credentials_dir.mkdir(mode=0o700, exist_ok=True)
        credentials_path = credentials_dir / (username + '.json')
        with open(credentials_path, 'w', opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
            json.dump({'url': 'http://127.0.0.1:8767/companies/' + company.company_id,
                       'username': username, 'temporary_password': password}, f, indent=2)
        credentials_path.chmod(0o600)
        print('Initial credentials saved privately:', credentials_path)
    if args.assign_besingularity_campaigns:
        rows = db.scalars(select(RecruitmentCampaign).where(RecruitmentCampaign.company_id.is_(None))).all()
        for row in rows:
            if 'besingularity' not in row.config.get('name', '').lower():
                raise SystemExit('Found an unrelated unowned campaign; inspect it before migrating.')
        for row in rows:
            row.company_id = company.company_id
        db.commit()
        print('Existing campaigns assigned without changing prompts, history or assistant versions:', len(rows))
    print('Company workspace:', 'http://127.0.0.1:8767/companies/' + company.company_id)
    print('Username:', username)
    print('First login requires a password change. No calls placed.')
