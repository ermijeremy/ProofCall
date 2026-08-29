"""Run the CallProof interview scheduler as a small standalone worker.

The worker checks for due administrator schedules and submits them to
TeleExpert. Member A's intelligence engine must be configured by the
application deployment before this process is started.
"""

import argparse
import time

from app.db.session import SessionLocal, init_db
from app.services.interview_service import run_due_interviews


def main() -> None:
    parser = argparse.ArgumentParser(description="Run due CallProof interviews")
    parser.add_argument("--once", action="store_true", help="process due schedules once and exit")
    parser.add_argument("--interval", type=int, default=30, help="seconds between checks")
    args = parser.parse_args()
    init_db()

    while True:
        with SessionLocal() as db:
            processed = run_due_interviews(db)
            if processed:
                print(f"Processed {len(processed)} scheduled interview(s).")
        if args.once:
            return
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
