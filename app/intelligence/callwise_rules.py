"""Deterministic KPI annotation for the Callwise pilot questionnaire."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

MET, NOT_MET, UNCLEAR, REFUSED, NOT_ASKED, STOPPED = ("MET", "NOT_MET", "UNCLEAR", "REFUSED", "NOT_ASKED", "STOPPED")
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_DATE = re.compile(r"(?P<month>\d{1,2}|January|February|March|April|May|June|July|August|September|October|November|December)[ ,/-]+(?P<year>20\d{2})", re.I)
_ISO_MONTH = re.compile(r"^(?P<year>20\d{2})-(?P<month>0?[1-9]|1[0-2])(?:-\d{1,2})?$")
_UNSET = object()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER.search(value.replace(" ", ""))
        if match:
            try:
                return float(match.group().replace(",", ""))
            except ValueError:
                pass
    return None


def _months_from_start(value: Any, as_of: datetime | None = None) -> float | None:
    """Convert a month/year answer into elapsed months without guessing."""
    if isinstance(value, dict) and value.get("months") is not None:
        return _number(value["months"])
    if not isinstance(value, str):
        return None
    iso = _ISO_MONTH.fullmatch(value.strip())
    if iso:
        month, year = int(iso.group("month")), int(iso.group("year"))
        now = as_of or datetime.utcnow()
        return max(0, (now.year - year) * 12 + now.month - month)
    match = _DATE.search(value)
    if not match:
        return None
    month_text = match.group("month").lower()
    if month_text.isdigit():
        month = int(month_text)
    else:
        month = datetime.strptime(month_text, "%B").month
    year = int(match.group("year"))
    now = as_of or datetime.utcnow()
    return max(0, (now.year - year) * 12 + now.month - month)


def _entry(answers: dict[str, Any], slug: str) -> dict[str, Any]:
    value = answers.get(slug)
    if isinstance(value, dict):
        return value
    # Useful for deterministic callers and migration of early fixture data;
    # provider output is normally already in the structured shape.
    return {"value": value, "state": "STATED"} if value is not None else {}


def _clause(entry: dict[str, Any], status: str, value: Any = _UNSET) -> dict[str, Any]:
    return {"value": entry.get("value") if value is _UNSET else value, "status": status,
            "confidence": entry.get("confidence") or "LOW", "evidence": entry.get("evidence")}


def _plain_status(entry: dict[str, Any], good: set[str]) -> tuple[str, Any]:
    state, value = entry.get("state"), entry.get("value")
    if state == "REFUSED": return REFUSED, value
    if state == "NOT_ASKED": return NOT_ASKED, value
    if state != "STATED" or value is None: return UNCLEAR, value
    return (MET if str(value).strip().lower() in good else NOT_MET), value


def evaluate(answers: dict[str, Any], *, age_assessment: str = "UNKNOWN",
             safeguarding: bool = False, minimum_wage_etb: int | float | None = None,
             as_of: datetime | None = None) -> dict[str, Any]:
    """Evaluate KPI prerequisites; no model call and no inferred missing facts."""
    if safeguarding or age_assessment == "CHILD":
        age = _entry(answers, "age_years")
        return {"overall_verdict": STOPPED, "safeguarding_flag": True,
                "clauses": {"age": _clause(age, NOT_MET)}}
    clauses: dict[str, dict[str, Any]] = {}
    age_entry = _entry(answers, "age_years")
    age = _number(age_entry.get("value"))
    if age is not None and age < 15:
        return {"overall_verdict": STOPPED, "safeguarding_flag": True,
                "clauses": {"age": _clause(age_entry, NOT_MET, age)}}
    clauses["age"] = _clause(age_entry, MET if age is not None and age >= 15 or age_assessment == "ADULT" else UNCLEAR, age)

    status_entry = _entry(answers, "employment_status")
    state, value = _plain_status(status_entry, {"true", "yes", "working", "employed"})
    clauses["employment_status"] = _clause(status_entry, state, value)

    duration_entry = _entry(answers, "employment_duration")
    duration_source = "employment_duration"
    if not duration_entry or duration_entry.get("value") is None:
        duration_entry = _entry(answers, "start_date")
        duration_source = "start_date"
    if duration_source == "employment_duration":
        duration = _number(duration_entry.get("months", duration_entry.get("value")))
    else:
        # A start date is not a duration. In particular, a bare year such as
        # ``2018`` must never become 2018 months and incorrectly pass the KPI.
        duration = _months_from_start(duration_entry.get("value"), as_of)
    clauses["employment_duration"] = _clause(
        duration_entry,
        UNCLEAR if duration is None else (MET if duration >= 6 else NOT_MET),
        duration,
    )

    hours_entry = _entry(answers, "working_hours")
    raw = hours_entry.get("value")
    hours = _number(hours_entry.get("hours_per_week"))
    if hours is None and isinstance(raw, str):
        numbers = [_number(item) for item in _NUMBER.findall(raw)]
        numbers = [item for item in numbers if item is not None]
        if len(numbers) >= 2 and ("day" in raw.lower() or "ቀን" in raw):
            hours = numbers[0] * numbers[1]
        else:
            hours = _number(raw)
    if isinstance(raw, dict):
        days, per_day = _number(raw.get("days_per_week")), _number(raw.get("hours_per_day"))
        hours = days * per_day if days is not None and per_day is not None else None
    elif hours is None:
        hours = _number(raw)
    clauses["working_hours"] = _clause(hours_entry, UNCLEAR if hours is None else (MET if hours >= 20 else NOT_MET), hours)

    pay_entry = _entry(answers, "monthly_pay")
    pay = _number(pay_entry.get("amount_etb", pay_entry.get("value")))
    if minimum_wage_etb is None:
        pay_status = MET if pay is not None else _plain_status(pay_entry, set())[0]
    else:
        pay_status = UNCLEAR if pay is None else (MET if pay >= minimum_wage_etb else NOT_MET)
    clauses["salary"] = _clause(pay_entry, pay_status, pay)

    for source, name, good in (
        ("freedom_to_leave", "forced_labour", {"true", "free", "yes"}),
        ("equal_treatment", "discrimination", {"true", "no", "none", "never", "equal", "yes"}),
        ("worker_representation", "freedom_of_association", {"true", "allowed", "free", "yes"}),
    ):
        entry = _entry(answers, source)
        state, value = _plain_status(entry, good)
        clauses[name] = _clause(entry, state, value)
    states = [item["status"] for item in clauses.values()]
    verdict = "NOT_CONFIRMED" if NOT_MET in states else "UNCLEAR" if any(s in {UNCLEAR, REFUSED, NOT_ASKED} for s in states) else "CONFIRMED_GOOD_JOB"
    return {"overall_verdict": verdict, "safeguarding_flag": False, "clauses": clauses}


__all__ = ["evaluate"]
