"""SQLAlchemy engine and request-scoped session management."""

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables for the demo deployment.

    Model imports are intentionally local so importing the session module does
    not create circular imports during application startup.
    """

    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _upgrade_legacy_sqlite_schema()


def _upgrade_legacy_sqlite_schema() -> None:
    """Add columns introduced after the original demo database was created.

    The project does not yet use Alembic, and ``create_all`` deliberately does
    not alter existing tables.  These small, idempotent SQLite upgrades keep a
    developer's existing dashboard database usable after a model change.
    """

    if engine.dialect.name != "sqlite":
        return

    employer_columns = {
        "job_positions": "JSON NOT NULL DEFAULT '{}'",
        "gender_breakdown": "JSON NOT NULL DEFAULT '{}'",
        "age_band_breakdown": "JSON NOT NULL DEFAULT '{}'",
        "timezone": "VARCHAR(64) NOT NULL DEFAULT ''",
        "clock_convention": "VARCHAR(20) NOT NULL DEFAULT ''",
        "minimum_wage_etb": "INTEGER",
        "small_cell_threshold": "INTEGER NOT NULL DEFAULT 5",
    }
    table_upgrades = {
        "employers": employer_columns,
        "teleexpert_calls": {
            "answer_timeout_seconds": "INTEGER NOT NULL DEFAULT 45",
        },
        "interview_schedules": {
            "answer_timeout_seconds": "INTEGER NOT NULL DEFAULT 45",
        },
        "beneficiaries": {
            "name": "VARCHAR(255) NOT NULL DEFAULT ''",
            "gender": "VARCHAR(20)",
            "age_band": "VARCHAR(20)",
            "training_cohort_id": "VARCHAR(100)",
            "training_end_date": "DATE",
            "placement_status": "VARCHAR(30)",
            "placement_date": "DATE",
            "consent_to_followup_contact": "BOOLEAN NOT NULL DEFAULT 0",
            "consent_recorded_at": "DATE",
            "consent_source": "VARCHAR(30)",
            "notes": "TEXT",
        },
        "teleexpert_webhook_events": {
            "attempts": "INTEGER NOT NULL DEFAULT 0",
        },
        "interview_batches": {
            # Keep this compatibility column available for databases created by
            # either batch design. New code does not use it, but SQLAlchemy still
            # selects mapped columns when loading scheduled batches.
            "roster_worker_ids": "JSON NOT NULL DEFAULT '[]'",
            "scheduled_at": "DATETIME",
            "retries": "INTEGER NOT NULL DEFAULT 2",
            "questionnaire_version": "VARCHAR(30) NOT NULL DEFAULT 'callwise-v1'",
            "prompt_version": "VARCHAR(30) NOT NULL DEFAULT 'callwise-prompt-v1'",
            "record_schema_version": "VARCHAR(30) NOT NULL DEFAULT 'callwise-record-v1'",
            "maximum_duration_seconds": "INTEGER NOT NULL DEFAULT 360",
            "retry_delay_hours": "INTEGER NOT NULL DEFAULT 24",
            "source_checksum": "VARCHAR(64)",
            "source_row_count": "INTEGER NOT NULL DEFAULT 0",
            "eligible_row_count": "INTEGER NOT NULL DEFAULT 0",
            "primary_target_count": "INTEGER NOT NULL DEFAULT 0",
            "spare_target_count": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
        },
        "batch_messages": {
            "company_id": "VARCHAR(100) NOT NULL DEFAULT ''",
        },
        "worker_answers": {
            "transcript_turns": "JSON",
            "audio_url": "TEXT",
            "disposition": "VARCHAR(30) NOT NULL DEFAULT 'completed'",
            "attempts": "INTEGER NOT NULL DEFAULT 1",
            "good_job_annotation": "VARCHAR(30) NOT NULL DEFAULT 'UNCLEAR'",
            "kpi_clauses": "JSON NOT NULL DEFAULT '{}'",
            "consent_json": "JSON NOT NULL DEFAULT '{}'",
        },
        "worker_answer_records": {
            "transcript_turns": "JSON",
            "audio_url": "TEXT",
            "disposition": "VARCHAR(30) NOT NULL DEFAULT 'completed'",
            "attempts": "INTEGER NOT NULL DEFAULT 1",
            "good_job_annotation": "VARCHAR(30) NOT NULL DEFAULT 'UNCLEAR'",
            "kpi_clauses": "JSON NOT NULL DEFAULT '{}'",
            "consent_json": "JSON NOT NULL DEFAULT '{}'",
        },
        "batch_targets": {
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "DATETIME",
            "failure_reason": "TEXT",
            "is_primary_sample": "BOOLEAN NOT NULL DEFAULT 1",
            "is_spare": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in table_upgrades.items():
            existing = {column["name"] for column in inspect(engine).get_columns(table_name)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {definition}'))

        # Company-level thread messages are valid before the first batch exists,
        # so ``batch_id`` must be nullable.  Older demo databases were created
        # while every message belonged to a batch; SQLite cannot alter a column's
        # nullability in place, therefore rebuild this one small table once.
        message_columns = inspect(engine).get_columns("batch_messages")
        batch_id = next((column for column in message_columns if column["name"] == "batch_id"), None)
        if batch_id is not None and not batch_id["nullable"]:
            connection.execute(text("PRAGMA foreign_keys=OFF"))
            connection.execute(text("""
                CREATE TABLE batch_messages_new (
                    message_id VARCHAR(100) NOT NULL PRIMARY KEY,
                    company_id VARCHAR(100) NOT NULL DEFAULT '',
                    batch_id VARCHAR(100),
                    role VARCHAR(20) NOT NULL,
                    text TEXT NOT NULL,
                    payload JSON NOT NULL,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES interview_batches (batch_id)
                )
            """))
            connection.execute(text("""
                INSERT INTO batch_messages_new
                    (message_id, company_id, batch_id, role, text, payload, created_at)
                SELECT message_id, company_id, batch_id, role, text, payload, created_at
                FROM batch_messages
            """))
            connection.execute(text("DROP TABLE batch_messages"))
            connection.execute(text("ALTER TABLE batch_messages_new RENAME TO batch_messages"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_batch_messages_company_id "
                "ON batch_messages (company_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_batch_messages_batch_id "
                "ON batch_messages (batch_id)"
            ))
            connection.execute(text("PRAGMA foreign_keys=ON"))
