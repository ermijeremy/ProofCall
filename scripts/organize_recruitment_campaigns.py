"""Keep two local demos and one parameterized campaign; archive duplicate history.

Never places calls. Run from this worktree; backs up SQLite before any mutation.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
from dotenv import load_dotenv
load_dotenv(root / '.env')
from sqlalchemy import select
from app.db.session import engine, init_db, SessionLocal
from app.models.recruitment import RecruitmentCampaign, RecruitmentCall, RecruitmentLead
from app.schemas.recruitment import RecruitmentConfig
from app.services.recruitment_service import save_campaign

if engine.dialect.name != 'sqlite':
    raise SystemExit('This local cleanup requires SQLite.')
backup_dir = root / '.campaign-backups'
backup_dir.mkdir(mode=0o700, exist_ok=True)
backup_path = backup_dir / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
with engine.connect() as conn, sqlite3.connect(backup_path) as destination:
    conn.connection.driver_connection.backup(destination)
backup_path.chmod(0o600)
init_db()
with SessionLocal() as db:
    campaigns = [c for c in db.scalars(select(RecruitmentCampaign).order_by(RecruitmentCampaign.created_at))
                 if 'besingularity' in c.config.get('name', '').lower()]
    owners = {c.company_id for c in campaigns}
    if len(owners) > 1:
        raise SystemExit('BeSingularity campaigns have different owners; inspect before cleanup.')
    active = db.scalar(select(RecruitmentCall).where(RecruitmentCall.status.not_in(
        ('completed', 'failed', 'cancelled', 'rejected'))).limit(1))
    if active:
        raise SystemExit('A previous call is still nonterminal. Finish/reconcile it before cleanup.')
    counts = {model.__tablename__: db.query(model).count() for model in (RecruitmentCall, RecruitmentLead)}
    choices = []
    for label, english, amharic in [('Henok', 'Henok', 'ሄኖክ'), ('Bezawit', 'Bezawit', 'ቤዛዊት')]:
        candidates = [c for c in campaigns if english in c.config['prompt'] or amharic in c.config['prompt']]
        if not candidates:
            raise SystemExit(f'Missing {label} demo; no campaigns have been archived.')
        chosen = max(candidates, key=lambda c: c.revision)
        choices.append(chosen)
    standard_name = 'BeSingularity — Standard recruitment'
    standard = next((c for c in campaigns if c.config['name'] == standard_name), None)
    if standard is None:
        standard = next((c for c in campaigns if '{{recipient_name}}' in c.config['prompt'] and c not in choices), None)
    standard_data = dict(choices[0].config)
    standard_data.update(name=standard_name, prompt=(root / 'demo/besingularity/standard-prompt.md').read_text(),
        greeting='ሰላም {{recipient_name}}፣ ጤና ይስጥልኝ። የBeSingularity ሰው ሠራሽ የስልክ ረዳት ነኝ። በየትኛው ቋንቋ ማነጋገር ይመችዎታል—በአማርኛ፣ በአፋን ኦሮሞ፣ በትግርኛ፣ በእንግሊዝኛ ወይስ በሌላ ቋንቋ?')
    standard_config = RecruitmentConfig.model_validate(standard_data)
    if standard is None:
        standard = save_campaign(db, standard_config, company_id=choices[0].company_id)
    elif standard.config != standard_config.model_dump(mode='json'):
        standard = save_campaign(db, standard_config, standard.campaign_id, standard.revision)
    choices.append(standard)
    for chosen, name in zip(choices[:2], ('Henok', 'Bezawit')):
        # Keep operator-edited demo wording and pinned assistant versions intact.
        config = dict(chosen.config)
        config['name'] = f'BeSingularity — {name} demo'
        chosen.config = config
    keep = {c.campaign_id for c in choices}
    for c in campaigns:
        c.archived = c.campaign_id not in keep
    db.commit()
    for model in (RecruitmentCall, RecruitmentLead):
        assert db.query(model).count() == counts[model.__tablename__]
    assert sum(not c.archived for c in choices) == 3
    print('Active campaigns:')
    for c in choices:
        print(' -', c.config['name'], c.campaign_id)
    print('Call history and contacts preserved. SQLite backup:', backup_path)
    print('No calls placed. Prepare the standard campaign assistant before calling.')
