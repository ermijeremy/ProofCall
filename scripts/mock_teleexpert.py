"""Local Mock TeleExpert server for black-box CallProof testing."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class CallRequest(BaseModel):
    phone_number: str
    prompt: str
    response_format: str = "both"
    retries: int = Field(default=2, ge=0)
    retry_delay_seconds: int = Field(default=30, ge=0)


class CompletionRequest(BaseModel):
    transcript: str = ""
    transcript_turns: list[dict[str, Any]] = Field(default_factory=list)
    language: str = "am"
    audio_url: str | None = None


app = FastAPI(title="Mock TeleExpert")
calls: dict[str, dict[str, Any]] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/calls", status_code=202)
def create_call(data: CallRequest) -> dict[str, Any]:
    call_id = f"mock_call_{uuid4().hex}"
    calls[call_id] = {
        "call_id": call_id,
        "phone_number": data.phone_number,
        "prompt": data.prompt,
        "response_format": data.response_format,
        "retries": data.retries,
        "retry_delay_seconds": data.retry_delay_seconds,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat(),
        "transcript": None,
        "transcript_turns": [],
        "language": None,
        "audio_url": None,
    }
    return {"call_id": call_id, "status": "queued"}


@app.get("/v1/calls/{call_id}")
def get_call(call_id: str) -> dict[str, Any]:
    if call_id not in calls:
        raise HTTPException(status_code=404, detail="Call not found")
    return calls[call_id]


@app.post("/v1/calls/{call_id}/simulate-complete")
def simulate_complete(call_id: str, data: CompletionRequest) -> dict[str, Any]:
    if call_id not in calls:
        raise HTTPException(status_code=404, detail="Call not found")
    calls[call_id].update(
        {
            "status": "completed",
            "transcript": data.transcript,
            "transcript_turns": data.transcript_turns,
            "language": data.language,
            "audio_url": data.audio_url,
            "completed_at": datetime.utcnow().isoformat(),
        }
    )
    return calls[call_id]


@app.post("/v1/calls/{call_id}/cancel")
def cancel_call(call_id: str) -> dict[str, Any]:
    if call_id not in calls:
        raise HTTPException(status_code=404, detail="Call not found")
    calls[call_id]["status"] = "cancelled"
    return calls[call_id]

