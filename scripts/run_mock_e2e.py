"""Run a black-box CallProof demo against local Mock TeleExpert."""

import argparse
import json
from pathlib import Path

import httpx


def request(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path} failed: {response.status_code} {response.text}")
    return response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--callproof", default="http://127.0.0.1:8000")
    parser.add_argument("--teleexpert", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    workers = json.loads((Path(__file__).parents[1] / "data" / "demo_workers.json").read_text())

    with httpx.Client(base_url=args.callproof, timeout=10) as proof, httpx.Client(
        base_url=args.teleexpert, timeout=10
    ) as teleexpert:
        assert request(proof, "GET", "/health").json()["status"] == "ok"
        assert request(teleexpert, "GET", "/health").json()["status"] == "ok"

        request(
            proof,
            "POST",
            "/api/employers",
            json={
                "company_id": "ABC",
                "name": "ABC Construction",
                "reported_good_jobs": 20,
                "average_salary": 8500,
                "training_participation": 18,
                "worker_count": 20,
            },
        )
        for worker in workers:
            request(proof, "POST", "/api/beneficiaries", json=worker)

        campaign = request(
            proof,
            "POST",
            "/api/campaigns",
            json={
                "programme_name": "Mock E2E Cohort",
                "company_id": "ABC",
                "sample_size": 20,
                "language": "am",
                "criteria": {"minimum_age": 15, "minimum_weekly_hours": 20},
            },
        ).json()

        for index, worker in enumerate(workers):
            call = request(
                proof,
                "POST",
                "/api/teleexpert/calls",
                json={
                    "worker_id": worker["worker_id"],
                    "campaign_id": campaign["campaign_id"],
                    "phone_number": worker["phone_number"],
                    "prompt": "Mock decent-work interview.",
                },
            )
            assert call.status_code == 202
            call_id = call.json()["call_id"]
            verdict = (
                "CONFIRMED_GOOD_JOB"
                if index < 14
                else "NOT_CONFIRMED"
                if index < 16
                else "UNCLEAR"
                if index < 19
                else "STOPPED"
            )
            request(
                teleexpert,
                "POST",
                f"/v1/calls/{call_id}/simulate-complete",
                json={
                    "transcript": "I am fourteen years old." if verdict == "STOPPED" else "Synthetic worker response.",
                    "transcript_turns": [{"speaker": "worker", "text": "Synthetic worker response."}],
                    "language": "am",
                    "audio_url": f"http://mock/audio/{call_id}.wav",
                },
            )
            synced = request(proof, "GET", f"/api/teleexpert/calls/{call_id}").json()
            assert synced["status"] == "completed"
            result = {
                "worker_id": worker["worker_id"],
                "call_id": call_id,
                "transcript": synced["transcript"],
                "transcript_turns": synced["transcript_turns"],
                "language": synced["language"],
                "consent": True,
                "clauses": {
                    "age": {
                        "value": 14 if verdict == "STOPPED" else 19,
                        "status": "STOPPED" if verdict == "STOPPED" else "MET",
                        "confidence": "HIGH",
                    }
                },
                "contradictions": (
                    [{"type": "employment_status", "description": "Employer and worker disagree", "material": True}]
                    if verdict == "NOT_CONFIRMED"
                    else []
                ),
                "safeguarding_flag": verdict == "STOPPED",
                "overall_verdict": verdict,
            }
            request(proof, "POST", "/api/calls/process-completed", json=result)

        summary = request(proof, "GET", "/api/programmes/summary").json()
        expected = {"employer_claimed": 20, "worker_confirmed": 14, "not_confirmed": 2, "unclear": 3, "stopped": 1}
        for key, value in expected.items():
            assert summary[key] == value, (key, summary)
        print("Black-box E2E passed:", summary)


if __name__ == "__main__":
    main()
