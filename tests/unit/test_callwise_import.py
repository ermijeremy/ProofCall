from app.models.employer import Employer
from app.services.batch_service import import_company_csv


def test_callwise_csv_requires_complete_consent_and_keeps_beneficiary_id(db):
    company = Employer(company_id="co_import", name="beSingularity")
    db.add(company)
    db.commit()
    csv = (
        "beneficiary_id,first_name,phone_e164,preferred_language,gender,age_band,"
        "training_cohort_id,training_end_date,placement_status_per_besingularity,"
        "placement_date,consent_to_followup_contact,notes\n"
        "BSG-1,Hanna,+251911000042,am,F,25+,COH-2026-04,2026-04-30,placed_job,2026-06-15,yes;2026-04-30;registration_form,ok\n"
        "BSG-2,NoConsent,+251911000043,en,M,15-24,COH-2026-04,2026-04-30,not_placed,,no,,\n"
    ).encode()
    result = import_company_csv(db, company, csv)
    assert result["modern_contract"] is True
    assert result["eligible"] == 1
    assert result["skipped"] and "consent" in result["skipped"][0]
    person = db.get(__import__("app.models.beneficiary", fromlist=["Beneficiary"]).Beneficiary, "BSG-1")
    assert person is not None
    assert person.consent_recorded_at.isoformat() == "2026-04-30"
    assert person.consent_source == "registration_form"


def test_callwise_csv_rejects_invalid_phone_and_training_date(db):
    company = Employer(company_id="co_import_bad", name="beSingularity")
    db.add(company)
    db.commit()
    csv = (
        "beneficiary_id,first_name,phone_e164,preferred_language,gender,age_band,"
        "training_cohort_id,training_end_date,placement_status_per_besingularity,"
        "consent_to_followup_contact\n"
        "BSG-3,Test,0911000044,am,F,25+,COH,2025-01-01,placed_job,yes;2026-04-01;verbal\n"
    ).encode()
    result = import_company_csv(db, company, csv)
    assert result["eligible"] == 0
    assert len(result["skipped"]) == 1
