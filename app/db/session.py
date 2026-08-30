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
        },
        "teleexpert_webhook_events": {
            "attempts": "INTEGER NOT NULL DEFAULT 0",
        },
        "interview_batches": {
            # roster_worker_ids was dropped when the thread became company-scoped:
            # the roster is the company's, so per-round membership was scoping
            # around a duplicate-import bug instead of fixing it. SQLite cannot
            # drop a column in place and a stale one is harmless, so it is left
            # alone on existing databases rather than migrated away.
            "scheduled_at": "DATETIME",
            "retries": "INTEGER NOT NULL DEFAULT 2",
        },
        "batch_messages": {
            "company_id": "VARCHAR(100) NOT NULL DEFAULT ''",
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
