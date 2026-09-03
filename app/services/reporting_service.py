"""Deterministic batch reporting, SDG mapping, and spreadsheet exports."""

from __future__ import annotations

import csv
import copy
import io
import json
import zipfile
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from app.models.batch import InterviewBatch
from app.models.batch import CallAttempt
from app.models.employer import Employer
from app.repositories.batches import BatchTargetRepository, WorkerAnswerRecordRepository, WorkerAnswersRepository
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.services.batch_service import batch_summary


SDG_DEFINITIONS = {
    "8.5": ("Decent work", ("work", "job", "employment", "hours", "pay", "salary", "contract", "wage")),
    "8.6": ("Youth employment", ("age", "young", "youth")),
    "8.8": ("Labour rights", ("forced", "threat", "punish", "discriminat", "association", "safety", "leave")),
    "5.5": ("Gender equality", ("gender", "women", "woman", "female", "equal")),
    "1.2": ("Poverty and income", ("salary", "pay", "income", "wage", "money", "poverty")),
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _suppressed_counts(values: dict[str, Any], threshold: int) -> dict[str, Any]:
    """Suppress small public cells while retaining the internal audit counts."""
    if threshold <= 0:
        return dict(values)
    return {
        key: ("<suppressed>" if isinstance(value, int) and value < threshold else value)
        for key, value in values.items()
    }


def _cohort_summary(rows: list[dict[str, Any]], threshold: int) -> list[dict[str, Any]]:
    """Build the aggregate cohort hand-back without exposing worker identity."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("cohort") or "unspecified", []).append(row)
    result = []
    for cohort, items in sorted(grouped.items()):
        included = [item for item in items if item.get("included_in_analysis") == "yes"]
        excluded = [item for item in items if item.get("included_in_analysis") != "yes"]
        count = len(included)
        result.append({
            "cohort": cohort,
            "count": count if count >= threshold or threshold <= 0 else "<suppressed>",
            "excluded": len(excluded) if len(items) >= threshold or threshold <= 0 else "<suppressed>",
            "placement_rate": (
                round(sum(bool(item.get("placement_status")) and item.get("placement_status") in {"placed_job", "gig"} for item in included) / count * 100, 2)
                if count >= threshold or threshold <= 0 else "<suppressed>"
            ),
            "good_job_rate": (
                round(sum(item.get("good_job_annotation") == "CONFIRMED_GOOD_JOB" for item in included) / count * 100, 2)
                if count >= threshold or threshold <= 0 else "<suppressed>"
            ),
        })
    return result


def _public_summary(summary: dict[str, Any], threshold: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Remove identity/evidence and suppress sensitive small cells."""
    safe = copy.deepcopy(summary)
    safe["excluded"] = [{"reason": item.get("reason")} for item in safe.get("excluded", [])]
    for question in safe.get("questions", {}).values():
        question["quotes"] = []
        question["category_counts"] = _suppressed_counts(question.get("category_counts", {}), threshold)
        question["state_counts"] = _suppressed_counts(question.get("state_counts", {}), threshold)
    safe["cohorts"] = _cohort_summary(rows, threshold)
    return safe


def sdg_mapping(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map available batch questions to SDGs without asking a model to count."""
    questions = summary.get("questions", {})
    result: dict[str, dict[str, Any]] = {}
    for code, (name, keywords) in SDG_DEFINITIONS.items():
        matched = []
        for slug, question in questions.items():
            text = f"{slug} {question.get('question', '')}".lower()
            if any(keyword in text for keyword in keywords):
                matched.append({
                    "question": question.get("question"),
                    "slug": slug,
                    "category_counts": question.get("category_counts", {}),
                    "state_counts": question.get("state_counts", {}),
                    "answered": question.get("answered", 0),
                })
        result[code] = {
            "name": name,
            "status": "available" if matched else "not_available",
            "source_questions": matched,
            "note": (
                "Mapped from worker interview questions and counted answers."
                if matched else "No matching question was included in this batch."
            ),
        }
    return result


def report(db: Session, batch_id: str) -> dict[str, Any]:
    batch = db.get(InterviewBatch, batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    summary = batch_summary(db, batch)
    # Aggregate exports are safe to share: never expose names, phone numbers,
    # transcripts, audio references, or verbatim quotes in the public report.
    targets = BatchTargetRepository(db).for_batch(batch_id)
    company = db.get(Employer, batch.company_id)
    threshold = int(getattr(company, "small_cell_threshold", 5) or 0)
    rows = worker_rows(db, batch_id)
    public_summary = _public_summary(summary, threshold, rows)
    attempts = db.query(CallAttempt).filter(CallAttempt.batch_id == batch_id).all()
    attempt_breakdown: dict[str, int] = {}
    for attempt in attempts:
        key = attempt.disposition or attempt.provider_state or "unknown"
        attempt_breakdown[key] = attempt_breakdown.get(key, 0) + 1
    return _json_safe({
        "report_version": "1.0",
        "batch": {
            "batch_id": batch.batch_id,
            "title": batch.title,
            "language": batch.language,
            "status": batch.status,
            "created_at": batch.created_at,
            "settings": {
                "minimum_wage_etb": getattr(company, "minimum_wage_etb", None),
                "small_cell_threshold": threshold,
            },
        },
        "questions": batch.questions or [],
        "categories": batch.categories or {},
        "progress": {
            "selected": len(targets),
            "returned": len([target for target in targets if target.status == "completed"]),
            "waiting": len([target for target in targets if target.status not in {"completed", "failed"}]),
            "failed": len([target for target in targets if target.status == "failed"]),
            "retry_wait": len([target for target in targets if target.status == "retry_wait"]),
            "attempts": sum(int(target.attempts or 0) for target in targets),
            "attempt_history": len(attempts),
            "attempt_breakdown": dict(sorted(attempt_breakdown.items())),
        },
        "summary": public_summary,
        "sdg_mapping": sdg_mapping(public_summary),
        "retention": {
            "audio_and_intermediate_transcript": "delete after extraction and hand-back; target 30 days after last call",
            "public_export": "aggregate only; no names, contacts, transcripts, or audio references",
        },
        "generated_at": datetime.utcnow(),
    })


def worker_rows(db: Session, batch_id: str) -> list[dict[str, Any]]:
    """Build spreadsheet rows: one employee per row, one question per column."""
    batch = db.get(InterviewBatch, batch_id)
    if batch is None:
        raise ValueError(f"Batch not found: {batch_id}")
    people = {person.worker_id: person for person in BeneficiaryRepository(db).for_company(batch.company_id)}
    answers = {record.worker_id: record for record in WorkerAnswersRepository(db).for_batch(batch_id)}
    targets = {target.worker_id: target for target in BatchTargetRepository(db).for_batch(batch_id)}
    rows: list[dict[str, Any]] = []
    for worker_id, target in targets.items():
        person = people.get(worker_id)
        record = answers.get(worker_id)
        values = (record.answers or {}) if record else {}
        rows.append({
            "worker_id": worker_id,
            "person": person.name if person else worker_id,
            "contact": person.phone_number if person else "",
            "language": record.language if record else (person.preferred_language if person else batch.language),
            "gender": person.gender if person else "",
            "age_band": person.age_band if person else "",
            "cohort": person.training_cohort_id if person else "",
            "placement_status": person.placement_status if person else "",
            "interview_status": target.status,
            "disposition": record.disposition if record else "not_reached",
            "attempts": record.attempts if record else 0,
            "good_job_annotation": record.good_job_annotation if record else "UNCLEAR",
            "kpi_clauses": record.kpi_clauses if record else {},
            "included_in_analysis": "no" if record and record.excluded else "yes" if record else "no",
            "exclusion_reason": record.exclusion_reason if record else "",
            "call_id": target.call_id or "",
            "transcript": record.transcript if record else "",
            "audio_url": record.audio_url if record else "",
            "answers": {
                question["text"]: (values.get(question["slug"]) or {}).get("value", "")
                for question in (batch.questions or [])
            },
        })
    return rows


def csv_bytes(data: dict[str, Any]) -> bytes:
    if "worker_rows" in data:
        questions = [question.get("text", "") for question in data.get("questions", [])]
        columns = [
            "Person", "Contact", "Worker ID", "Language", "Gender", "Age band", "Cohort", "Placement status", "Interview status",
            "Disposition", "Attempts", "Good-job annotation", "Included in analysis", "Exclusion reason", "TeleExpert call ID", "Transcript", "Audio URL", "KPI clauses (JSON)",
            *questions,
        ]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in data["worker_rows"]:
            values = {
                "Person": row["person"], "Contact": row["contact"], "Worker ID": row["worker_id"],
                "Language": row["language"], "Gender": row["gender"], "Age band": row["age_band"],
                "Cohort": row["cohort"], "Placement status": row["placement_status"], "Interview status": row["interview_status"],
                "Disposition": row["disposition"], "Attempts": row["attempts"], "Good-job annotation": row["good_job_annotation"],
                "Included in analysis": row["included_in_analysis"], "Exclusion reason": row["exclusion_reason"],
                "TeleExpert call ID": row["call_id"], "Transcript": row["transcript"], "Audio URL": row["audio_url"],
                "KPI clauses (JSON)": json.dumps(row.get("kpi_clauses", {}), ensure_ascii=False),
                **row["answers"],
            }
            writer.writerow(values)
        return output.getvalue().encode("utf-8-sig")

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "section", "field", "value", "sdg", "sdg_name", "question", "category",
        "count", "percentage", "state", "state_count", "note",
    ])
    batch = data["batch"]
    progress = data.get("progress", {})
    summary = data.get("summary", {})
    for field in ("batch_id", "title", "language", "status", "created_at"):
        writer.writerow(["batch", field, batch.get(field, ""), "", "", "", "", "", "", "", "", ""])
    for field in ("selected", "returned", "waiting", "failed", "retry_wait", "attempts"):
        writer.writerow(["progress", field, progress.get(field, 0), "", "", "", "", "", "", "", "", ""])
    writer.writerow(["summary", "interviews_counted", summary.get("interviews_counted", 0), "", "", "", "", "", "", "", "", ""])
    writer.writerow(["summary", "excluded_total", summary.get("excluded_total", 0), "", "", "", "", "", "", "", "", ""])

    for question in (summary.get("questions") or {}).values():
        categories = question.get("category_counts") or {}
        states = question.get("state_counts") or {}
        denominator = max(int(question.get("answered") or 0), 1)
        for category, count in categories.items():
            writer.writerow(["category", "", "", "", "", question.get("question", ""), category, count, round(count / denominator * 100, 2), "", "", "Batch-wide category distribution"])
        for state, count in states.items():
            writer.writerow(["answer_state", "", "", "", "", question.get("question", ""), "", "", "", state, count, "Extraction state distribution"])

    for code, item in data["sdg_mapping"].items():
        if not item["source_questions"]:
            writer.writerow(["sdg", "status", item["status"], code, item["name"], "", "", "", "", "", "", item["note"]])
            continue
        for question in item["source_questions"]:
            writer.writerow(["sdg_source", "status", item["status"], code, item["name"], question["question"], "", "", "", "", "", item["note"]])
    return output.getvalue().encode("utf-8-sig")


def _xlsx_cell(value: Any) -> str:
    value = "" if value is None else str(value)
    return f'<c t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>'


def xlsx_bytes(data: dict[str, Any]) -> bytes:
    """Create a dependency-free XLSX workbook with summary, SDG, and answers."""
    rows = [["SDG", "Name", "Status", "Question", "Category", "Count", "State", "State count", "Note"]]
    for code, item in data["sdg_mapping"].items():
        questions = item["source_questions"] or [{"question": "", "category_counts": {}, "state_counts": {}}]
        for question in questions:
            for category, count in (question.get("category_counts") or {"": ""}).items():
                rows.append([code, item["name"], item["status"], question.get("question", ""), category, count, "", "", item["note"]])
            for state, count in (question.get("state_counts") or {"": ""}).items():
                rows.append([code, item["name"], item["status"], question.get("question", ""), "", "", state, count, item["note"]])
    sheet_rows = "".join("<row>" + "".join(_xlsx_cell(value) for value in row) + "</row>" for row in rows)
    summary_rows = [["Batch", data["batch"]["batch_id"]], ["Title", data["batch"]["title"]], ["Status", data["batch"]["status"]]]
    summary_rows += [[key, value] for key, value in data["summary"].items() if not isinstance(value, (dict, list))]
    report_xml = "".join("<row>" + "".join(_xlsx_cell(value) for value in row) + "</row>" for row in summary_rows)
    has_workers = bool(data.get("worker_rows"))
    sheet3_types = '<Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' if has_workers else ''
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' + sheet3_types + '</Types>'
    third_sheet = '<sheet name="Worker Answers" sheetId="3" r:id="rId3"/>' if has_workers else ''
    workbook = '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/><sheet name="SDG Mapping" sheetId="2" r:id="rId2"/>' + third_sheet + '</sheets></workbook>'
    # Correct namespace is accepted by Excel and most readers; keep the package
    # dependency-free for the project environment.
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    third_rel = '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/>' if has_workers else ''
    workbook_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>' + third_rel + '</Relationships>'
    def sheet(body: str) -> str:
        return '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + body + "</sheetData></worksheet>"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet(report_xml))
        archive.writestr("xl/worksheets/sheet2.xml", sheet(sheet_rows))
        if has_workers:
            worker_headers = ["Person", "Contact", "Worker ID", "Language", "Gender", "Age band", "Cohort", "Placement status", "Status", "Disposition", "Attempts", "Good-job annotation", "Included", "Exclusion reason", "Transcript", "Audio URL", "KPI clauses (JSON)"]
            worker_headers += [question.get("text", "") for question in data.get("questions", [])]
            worker_xml_rows = [worker_headers]
            for item in data["worker_rows"]:
                worker_xml_rows.append([
                    item.get("person", ""), item.get("contact", ""), item.get("worker_id", ""),
                    item.get("language", ""), item.get("gender", ""), item.get("age_band", ""),
                    item.get("cohort", ""), item.get("placement_status", ""), item.get("interview_status", ""), item.get("disposition", ""),
                    item.get("attempts", 0), item.get("good_job_annotation", "UNCLEAR"), item.get("included_in_analysis", ""), item.get("exclusion_reason", ""),
                    item.get("transcript", ""), item.get("audio_url", ""), json.dumps(item.get("kpi_clauses", {}), ensure_ascii=False),
                    *[item.get("answers", {}).get(question.get("text", ""), "") for question in data.get("questions", [])],
                ])
            worker_xml = "".join("<row>" + "".join(_xlsx_cell(value) for value in row) + "</row>" for row in worker_xml_rows)
            archive.writestr("xl/worksheets/sheet3.xml", sheet(worker_xml))
    return output.getvalue()


def worker_report(db: Session, batch_id: str, worker_id: str) -> dict[str, Any]:
    """Return an admin-only, identity-bearing export for one batch worker."""
    batch = db.get(InterviewBatch, batch_id)
    worker = BeneficiaryRepository(db).get(worker_id)
    current = WorkerAnswersRepository(db).get((batch_id, worker_id))
    if batch is None or worker is None or current is None:
        raise ValueError("Worker result not found in this batch")
    call = CallRepository(db).get(current.call_id) if current.call_id else None
    history = WorkerAnswerRecordRepository(db).for_batch(batch_id)
    return _json_safe({
        "batch": {"batch_id": batch.batch_id, "title": batch.title, "language": batch.language},
        "worker": {"worker_id": worker.worker_id, "name": worker.name, "contact": worker.phone_number},
        "result": {
            "call_id": current.call_id,
            "language": current.language,
            "consent": current.consent,
            "excluded": current.excluded,
            "exclusion_reason": current.exclusion_reason,
            "good_job_annotation": current.good_job_annotation,
            "kpi_clauses": current.kpi_clauses or {},
            "answers": current.answers,
            "transcript": current.transcript,
            "audio_url": (
                f"/api/teleexpert/calls/{current.call_id}/audio"
                if current.call_id and current.consent else None
            ),
            "transcript_url": (
                f"/api/teleexpert/calls/{current.call_id}/transcript"
                if current.call_id and current.consent else None
            ),
            "call_status": call.status if call else None,
        },
        "history": [
            {"call_id": item.call_id, "created_at": item.created_at, "excluded": item.excluded,
             "answers": item.answers, "transcript": item.transcript}
            for item in history if item.worker_id == worker_id
        ],
        "generated_at": datetime.utcnow(),
    })


def worker_csv_bytes(data: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["batch_id", "worker_id", "name", "contact", "call_id", "question", "value", "state", "confidence", "evidence", "excluded"])
    result = data["result"]
    for slug, answer in result.get("answers", {}).items():
        writer.writerow([data["batch"]["batch_id"], data["worker"]["worker_id"], data["worker"]["name"], data["worker"]["contact"], result.get("call_id"), slug, answer.get("value"), answer.get("state"), answer.get("confidence"), answer.get("evidence"), result.get("excluded")])
    return output.getvalue().encode("utf-8-sig")


__all__ = ["csv_bytes", "report", "sdg_mapping", "worker_csv_bytes", "worker_report", "worker_rows", "xlsx_bytes"]
