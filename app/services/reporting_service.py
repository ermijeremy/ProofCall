"""Deterministic batch reporting, SDG mapping, and spreadsheet exports."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from sqlalchemy.orm import Session

from app.models.batch import InterviewBatch
from app.repositories.batches import WorkerAnswerRecordRepository, WorkerAnswersRepository
from app.repositories.beneficiaries import BeneficiaryRepository
from app.repositories.calls import CallRepository
from app.services.batch_service import batch_detail, batch_summary


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
    detail = batch_detail(db, batch_id)
    return _json_safe({
        "report_version": "1.0",
        "batch": {
            "batch_id": batch.batch_id,
            "title": batch.title,
            "language": batch.language,
            "status": batch.status,
            "created_at": batch.created_at,
        },
        "questions": detail["questions"],
        "categories": detail["categories"],
        "progress": detail["progress"],
        "summary": summary,
        "sdg_mapping": sdg_mapping(summary),
        "generated_at": datetime.utcnow(),
    })


def csv_bytes(data: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["section", "sdg", "name", "question", "category", "count", "state", "state_count", "note"])
    for code, item in data["sdg_mapping"].items():
        if not item["source_questions"]:
            writer.writerow(["sdg", code, item["name"], "", "", "", "", "", item["note"]])
            continue
        for question in item["source_questions"]:
            categories = question.get("category_counts") or {"": ""}
            states = question.get("state_counts") or {"": ""}
            for category, count in categories.items():
                writer.writerow(["sdg", code, item["name"], question["question"], category, count, "", "", item["note"]])
            for state, count in states.items():
                writer.writerow(["question_state", code, item["name"], question["question"], "", "", state, count, item["note"]])
    writer.writerow([])
    writer.writerow(["batch", data["batch"]["batch_id"], data["batch"]["title"]])
    for key, value in data["summary"].items():
        if not isinstance(value, (dict, list)):
            writer.writerow(["summary", "", key, value])
    return output.getvalue().encode("utf-8-sig")


def _xlsx_cell(value: Any) -> str:
    value = "" if value is None else str(value)
    return f'<c t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>'


def xlsx_bytes(data: dict[str, Any]) -> bytes:
    """Create a dependency-free XLSX workbook with Report and SDG sheets."""
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
    content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
    workbook = '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/><sheet name="SDG Mapping" sheetId="2" r:id="rId2"/></sheets></workbook>'
    # Correct namespace is accepted by Excel and most readers; keep the package
    # dependency-free for the project environment.
    rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>'
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
            "answers": current.answers,
            "transcript": current.transcript,
            "audio_url": f"/api/teleexpert/calls/{current.call_id}/audio" if current.call_id else None,
            "transcript_url": f"/api/teleexpert/calls/{current.call_id}/transcript" if current.call_id else None,
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


__all__ = ["csv_bytes", "report", "sdg_mapping", "worker_csv_bytes", "worker_report", "xlsx_bytes"]
