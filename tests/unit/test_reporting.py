import io
import json
import zipfile

from app.services.reporting_service import csv_bytes, sdg_mapping, xlsx_bytes


def _summary():
    return {
        "interviews_counted": 2,
        "records_total": 2,
        "excluded_total": 0,
        "questions": {
            "age_years": {
                "question": "How old are you?",
                "category_counts": {"Adult": 2},
                "state_counts": {"STATED": 2},
                "answered": 2,
            },
            "monthly_salary": {
                "question": "What is your monthly salary?",
                "category_counts": {"Low": 1, "High": 1},
                "state_counts": {"STATED": 2},
                "answered": 2,
            },
        },
    }


def test_sdg_mapping_always_returns_all_required_goals():
    mapping = sdg_mapping(_summary())
    assert set(mapping) == {"8.5", "8.6", "8.8", "5.5", "1.2"}
    assert mapping["8.5"]["status"] == "available"
    assert mapping["8.6"]["status"] == "available"
    assert mapping["5.5"]["status"] == "not_available"


def test_csv_export_contains_counts_and_sdg_rows():
    data = {"batch": {"batch_id": "b1", "title": "Round", "status": "complete"}, "summary": _summary(), "sdg_mapping": sdg_mapping(_summary())}
    output = csv_bytes(data).decode("utf-8-sig")
    assert "8.5" in output
    assert "monthly salary" in output
    assert "High" in output


def test_csv_export_contains_one_worker_row_and_question_columns():
    summary = _summary()
    data = {
        "batch": {"batch_id": "b1", "title": "Round", "status": "complete"},
        "questions": [{"slug": "age_years", "text": "How old are you?"}],
        "summary": summary,
        "sdg_mapping": sdg_mapping(summary),
        "worker_rows": [{
            "person": "Hanna", "contact": "+251911000042", "worker_id": "BSG-1",
            "language": "am", "gender": "F", "age_band": "25+", "cohort": "COH-1",
            "placement_status": "placed_job", "interview_status": "completed",
            "disposition": "completed", "attempts": 1, "good_job_annotation": "UNCLEAR",
            "included_in_analysis": "yes", "exclusion_reason": "", "call_id": "call-1",
            "transcript": "", "audio_url": "", "kpi_clauses": {},
            "answers": {"How old are you?": "25 to 35"},
        }],
    }
    output = csv_bytes(data).decode("utf-8-sig")
    assert "Person,Contact" in output
    assert "Hanna,+251911000042" in output
    assert "How old are you?" in output
    assert "25 to 35" in output


def test_xlsx_export_is_a_readable_zip_package():
    summary = _summary()
    data = {"batch": {"batch_id": "b1", "title": "Round", "status": "complete"}, "summary": summary, "sdg_mapping": sdg_mapping(summary)}
    output = xlsx_bytes(data)
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
        assert "xl/worksheets/sheet2.xml" in archive.namelist()
        assert "monthly salary" in archive.read("xl/worksheets/sheet2.xml").decode()
