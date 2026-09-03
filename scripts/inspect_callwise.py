"""Inspect the live Callwise pipeline without exposing secrets.

Usage examples:
    python scripts/inspect_callwise.py
    python scripts/inspect_callwise.py --batch batch_123
    python scripts/inspect_callwise.py --call 627d1d85029345a4a9c767a896231e7d

This is intentionally read-only. It is useful while a real TeleExpert call is
queued, active, completed, or waiting for webhook processing.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.batch import CallAttempt, InterviewBatch, WorkerAnswerRecord, WorkerAnswers  # noqa: E402
from app.models.call import TeleExpertCall  # noqa: E402
from app.models.webhook import TeleExpertWebhookEvent  # noqa: E402


def value(item, name: str, default="—"):
    result = getattr(item, name, None)
    return default if result in (None, "") else result


def stamp(item, name: str) -> str:
    result = getattr(item, name, None)
    return result.isoformat(timespec="seconds") if isinstance(result, datetime) else value(item, name)


def print_config() -> None:
    print("CONFIG")
    print(f"  database: {settings.database_url}")
    print(f"  TeleExpert: {settings.teleexpert_base_url or 'NOT SET'}")
    print(f"  webhook URL: {settings.teleexpert_webhook_url or 'NOT SET'}")
    print(f"  API key configured: {'yes' if settings.teleexpert_api_key else 'no'}")
    print(f"  webhook secret configured: {'yes' if settings.teleexpert_webhook_secret else 'no'}")
    print(f"  scheduler: {'enabled' if settings.scheduler_enabled else 'disabled'} ({settings.scheduler_interval_seconds}s)")


def inspect(args: argparse.Namespace) -> None:
    print_config()
    with SessionLocal() as db:
        batches = list(db.scalars(select(InterviewBatch).order_by(InterviewBatch.created_at.desc())).all())
        if args.batch:
            batches = [batch for batch in batches if batch.batch_id == args.batch]
        print(f"\nBATCHES ({len(batches)})")
        for batch in batches:
            targets = list(db.scalars(select(CallAttempt).where(CallAttempt.batch_id == batch.batch_id)).all())
            print(f"  {batch.batch_id} | {batch.status} | {batch.title}")
            print(f"    roster rows={value(batch, 'source_row_count', 0)} eligible={value(batch, 'eligible_row_count', 0)} "
                  f"started={stamp(batch, 'started_at')} completed={stamp(batch, 'completed_at')}")
            print(f"    attempts={len(targets)} questionnaire={value(batch, 'questionnaire_version')}")

        calls_query = select(TeleExpertCall).order_by(TeleExpertCall.created_at.desc())
        calls = list(db.scalars(calls_query).all())
        if args.call:
            calls = [call for call in calls if call.call_id == args.call]
        elif args.batch:
            batch_ids = {batch.batch_id for batch in batches}
            call_ids = {attempt.call_id for attempt in db.scalars(select(CallAttempt).where(CallAttempt.batch_id.in_(batch_ids))).all()} if batch_ids else set()
            calls = [call for call in calls if call.call_id in call_ids]
        print(f"\nTELEXPERT CALLS ({len(calls)})")
        for call in calls:
            print(f"  {call.call_id} | worker={call.worker_id} | status={call.status} | "
                  f"created={stamp(call, 'created_at')} completed={stamp(call, 'completed_at')}")
            print(f"    language={value(call, 'language')} transcript={'yes' if call.transcript else 'no'} "
                  f"turns={len(call.transcript_turns or [])} audio={'yes' if call.audio_url else 'no'}")
            if call.failure_reason:
                print(f"    failure={call.failure_reason}")

            attempts = list(db.scalars(select(CallAttempt).where(CallAttempt.call_id == call.call_id).order_by(CallAttempt.attempt_number)).all())
            for attempt in attempts:
                print(f"    attempt {attempt.attempt_number}: provider={attempt.provider_state} "
                      f"disposition={value(attempt, 'disposition')} ended={stamp(attempt, 'ended_at')}")

            events = list(db.scalars(select(TeleExpertWebhookEvent).where(TeleExpertWebhookEvent.call_id == call.call_id)).all())
            for event in events:
                print(f"    webhook {event.event_id}: {event.status} attempts={event.attempts} "
                      f"processed={stamp(event, 'processed_at')} error={value(event, 'error')}")

            answers = list(db.scalars(select(WorkerAnswers).where(WorkerAnswers.call_id == call.call_id)).all())
            for answer in answers:
                print(f"    result: disposition={answer.disposition} excluded={answer.excluded} "
                      f"verdict={answer.good_job_annotation} answers={len(answer.answers or {})}")
                if answer.exclusion_reason:
                    print(f"    exclusion={answer.exclusion_reason}")

        if not calls and not batches:
            print("  No Callwise records found.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", help="show one batch only")
    parser.add_argument("--call", help="show one TeleExpert call only")
    args = parser.parse_args()
    inspect(args)


if __name__ == "__main__":
    main()
