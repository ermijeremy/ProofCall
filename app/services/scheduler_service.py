"""Application-managed scheduler for administrator interview requests."""

from __future__ import annotations

import logging
from threading import Event, Thread

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.interview_service import run_due_interviews
from app.services.call_service import sync_active_calls

logger = logging.getLogger(__name__)


class InterviewScheduler:
    """Poll due interview schedules while the CallProof web app is running."""

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval_seconds = interval_seconds or settings.scheduler_interval_seconds
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="callproof-interview-scheduler", daemon=True)
        self._thread.start()
        logger.info("Automatic interview scheduler started (every %ss)", self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(2, self.interval_seconds + 1))
        self._thread = None
        logger.info("Automatic interview scheduler stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    processed = run_due_interviews(db)
                    synchronized = sync_active_calls(db)
                    if processed:
                        logger.info("Automatically processed %s due interview(s)", len(processed))
                    if synchronized:
                        logger.info("Automatically synchronized %s active call(s)", len(synchronized))
            except Exception:
                # A failed provider or missing intelligence engine must not kill
                # the web server; the next poll can retry the schedule safely.
                logger.exception("Automatic interview scheduler poll failed")
            self._stop.wait(self.interval_seconds)
