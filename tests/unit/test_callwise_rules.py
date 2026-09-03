from app.intelligence.callwise_rules import evaluate


def answers(**values):
    result = {}
    for slug, value in values.items():
        result[slug] = {"value": value, "state": "STATED", "confidence": "HIGH", "evidence": str(value)}
    return result


def complete(**extra):
    base = answers(
        age_years=24, employment_status=True, employment_duration=14,
        working_hours=40, monthly_pay=8500, freedom_to_leave="free",
        equal_treatment="equal", worker_representation="allowed",
    )
    base.update(extra)
    return base


def test_all_required_kpis_are_confirmed_deterministically():
    result = evaluate(complete())
    assert result["overall_verdict"] == "CONFIRMED_GOOD_JOB"
    assert result["clauses"]["working_hours"]["value"] == 40


def test_negative_kpi_is_not_confirmed():
    result = evaluate(complete(working_hours={"value": 10, "state": "STATED", "confidence": "HIGH", "evidence": "10"}))
    assert result["overall_verdict"] == "NOT_CONFIRMED"
    assert result["clauses"]["working_hours"]["status"] == "NOT_MET"


def test_missing_or_refused_answer_is_unclear_not_a_guess():
    refused = {"value": None, "state": "REFUSED", "confidence": "HIGH", "evidence": "I prefer not to say"}
    result = evaluate(complete(monthly_pay=refused))
    assert result["overall_verdict"] == "UNCLEAR"
    assert result["clauses"]["salary"]["status"] == "REFUSED"


def test_child_is_stopped_and_safeguarded():
    result = evaluate(complete(age_years=14))
    assert result["overall_verdict"] == "STOPPED"
    assert result["safeguarding_flag"] is True


def test_wage_is_only_applied_when_configured():
    result = evaluate(complete(monthly_pay=5000), minimum_wage_etb=6000)
    assert result["overall_verdict"] == "NOT_CONFIRMED"
    assert result["clauses"]["salary"]["status"] == "NOT_MET"


def test_days_and_hours_are_multiplied_when_provider_returns_spoken_text():
    result = evaluate(complete(working_hours="5 days each week, 8 hours each day"))
    assert result["clauses"]["working_hours"]["value"] == 40
    assert result["clauses"]["working_hours"]["status"] == "MET"


def test_start_month_is_converted_to_tenure_not_treated_as_a_year_value():
    result = evaluate(
        complete(employment_duration=None, start_date="June 2025"),
        as_of=__import__("datetime").datetime(2026, 9, 1),
    )
    assert result["clauses"]["employment_duration"]["value"] == 15
    assert result["clauses"]["employment_duration"]["status"] == "MET"


def test_bare_start_year_is_not_treated_as_months():
    result = evaluate(complete(employment_duration=None, start_date="2018"))

    assert result["clauses"]["employment_duration"]["value"] is None
    assert result["clauses"]["employment_duration"]["status"] == "UNCLEAR"
