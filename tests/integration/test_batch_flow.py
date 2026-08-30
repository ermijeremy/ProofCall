"""The batch path end to end, offline.

    CSV -> roster -> questions -> selection -> confirmation -> demo calls
        -> transcripts -> answers -> pass two (categories) -> counts -> chat

No network, no API key, and no telephone: the provider is a fake that answers all
four kinds of model call, and ``submit_teleexpert_call`` falls back to demo mode
because ``TELEXPERT_BASE_URL`` is unset in the test environment.

The two properties worth stating up front, because most of the assertions below
exist to protect them:

* Nothing dials until the admin confirms. The selection is drawn, persisted, and
  echoed first, and a decline places no calls at all.
* An excluded record is counted nowhere. A respondent the model reports as a
  child is flagged, and no count in the summary includes them.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.intelligence.batch_engine import BatchIntelligence
from app.repositories.batches import (
    BatchMessageRepository,
    BatchTargetRepository,
    WorkerAnswersRepository,
)
from app.repositories.calls import CallRepository
from app.services import batch_service
from app.services.teleexpert_service import dispatch_completed_call
from tests.fakes import TRANSCRIPTS, FakeBatchProvider

pytestmark = pytest.mark.integration

ROSTER_CSV = b"name,contact\nAbebe Kebede,+251900000001\nMarta Alemu,+251900000002\nYonas Tesfaye,+251900000003\nHanna Girma,+251900000004\n"
QUESTIONS_MESSAGE = "1 - what is ur age? 2 - Do u get enough compensation?"


def texts(db: Session, batch_id: str) -> list[str]:
    return [message.text for message in BatchMessageRepository(db).for_batch(batch_id)]


def ready_batch(db: Session, intelligence: BatchIntelligence, csv: bytes = ROSTER_CSV):
    batch = batch_service.create_batch(db, title="August round")
    batch_service.handle_upload(db, batch.batch_id, csv, "employees.csv")
    batch_service.handle_message(db, batch.batch_id, QUESTIONS_MESSAGE, intelligence)
    return batch


def complete_all_calls(db: Session, batch, intelligence: BatchIntelligence) -> None:
    names = {person["worker_id"]: person["name"] for person in batch_service.roster(db, batch)}
    for target in BatchTargetRepository(db).for_batch(batch.batch_id):
        if target.call_id:
            batch_service.process_batch_call(
                db, target.call_id, TRANSCRIPTS[names[target.worker_id]], "am", intelligence
            )


# -- the thread ------------------------------------------------------------ #


def test_a_new_batch_asks_for_the_two_things_it_needs(db: Session):
    batch = batch_service.create_batch(db)
    assert batch.status == batch_service.DRAFT
    greeting = texts(db, batch.batch_id)[0]
    assert "CSV" in greeting and "questions" in greeting
    assert "Nobody is called until you confirm." in greeting


def test_the_csv_becomes_a_roster_and_is_echoed_back(db: Session):
    batch = batch_service.create_batch(db)
    replies = batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "employees.csv")
    assert "4 employee(s)" in replies[0].text
    assert [person["name"] for person in batch_service.roster(db, batch)] == [
        "Abebe Kebede",
        "Hanna Girma",
        "Marta Alemu",
        "Yonas Tesfaye",
    ]
    assert replies[0].payload["workers"][0]["phone_number"] == "+251900000001"


def test_a_row_missing_a_contact_is_reported_rather_than_dropped_silently(db: Session):
    batch = batch_service.create_batch(db)
    replies = batch_service.handle_upload(
        db, batch.batch_id, b"name,contact\nAbebe,+251900000001\nMarta,\n", "employees.csv"
    )
    assert "1 row(s) skipped" in replies[0].text
    assert len(batch_service.roster(db, batch)) == 1


def test_a_csv_without_the_expected_columns_is_refused_in_the_thread(db: Session):
    batch = batch_service.create_batch(db)
    replies = batch_service.handle_upload(db, batch.batch_id, b"employee,mobile\nAbebe,+251900\n", "e.csv")
    assert "could not read that CSV" in replies[0].text
    assert batch_service.roster(db, batch) == []


def test_re_uploading_the_same_csv_does_not_double_the_roster(db: Session):
    batch = batch_service.create_batch(db)
    batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "employees.csv")
    batch_service.handle_upload(db, batch.batch_id, ROSTER_CSV, "employees.csv")
    assert len(batch_service.roster(db, batch)) == 4


def test_typed_questions_become_a_question_set_with_age_first(db: Session, intelligence: BatchIntelligence):
    batch = ready_batch(db, intelligence)
    assert batch.status == batch_service.READY
    assert [question["slug"] for question in batch.questions] == [
        "age_years",
        "do_u_get_enough_compensation",
    ]


def test_questions_before_a_roster_leave_the_batch_waiting_for_the_csv(db: Session, intelligence: BatchIntelligence):
    batch = batch_service.create_batch(db)
    replies = batch_service.handle_message(db, batch.batch_id, QUESTIONS_MESSAGE, intelligence)
    assert batch.status == batch_service.DRAFT
    assert "Upload the employee CSV" in replies[-1].text


def test_an_admin_can_set_the_interview_language_from_the_thread(db: Session, intelligence: BatchIntelligence):
    batch = ready_batch(db, intelligence)
    replies = batch_service.handle_message(db, batch.batch_id, "conduct the interviews in Swahili", intelligence)
    assert batch.language == "sw"
    assert "Swahili" in replies[-1].text


# -- selection ------------------------------------------------------------- #


def test_call_all_of_them_echoes_every_name_and_dials_nobody(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    replies = batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)

    assert batch.status == batch_service.SELECTED
    assert "Reply yes to place the calls" in replies[-1].text
    for name in ("Abebe Kebede", "Marta Alemu", "Yonas Tesfaye", "Hanna Girma"):
        assert name in replies[-1].text
    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    assert len(targets) == 4
    assert all(target.call_id is None for target in targets)
    assert CallRepository(db).list() == []


def test_call_half_of_them_at_random_draws_half_and_persists_the_draw(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    rows = "name,contact\n" + "".join(f"Worker {index},+2519100000{index:02d}\n" for index in range(20))
    batch = ready_batch(db, intelligence, rows.encode("utf-8"))
    provider.selection = {"mode": "fraction", "fraction": 0.5, "confidence": "HIGH"}
    replies = batch_service.handle_message(db, batch.batch_id, "call half of them at random", intelligence)

    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    assert len(targets) == 10
    assert "10 to call" in replies[-1].text
    assert all(target.call_id is None for target in targets)


def test_a_named_pair_selects_exactly_that_pair(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "explicit", "names": ["Abebe", "marta"], "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call Abebe and marta", intelligence)

    names = {person["worker_id"]: person["name"] for person in batch_service.roster(db, batch)}
    selected = {names[target.worker_id] for target in BatchTargetRepository(db).for_batch(batch.batch_id)}
    assert selected == {"Abebe Kebede", "Marta Alemu"}


def test_an_unmatched_name_is_reported_in_the_thread(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "explicit", "names": ["Abebe", "Ghost"], "confidence": "HIGH"}
    replies = batch_service.handle_message(db, batch.batch_id, "call Abebe and Ghost", intelligence)
    assert "No match on the roster for: Ghost" in replies[-1].text


def test_an_unreadable_instruction_asks_again_and_selects_nobody(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "unclear", "confidence": "LOW"}
    replies = batch_service.handle_message(db, batch.batch_id, "call the usual people", intelligence)
    assert "could not tell who you meant" in replies[-1].text
    assert BatchTargetRepository(db).for_batch(batch.batch_id) == []
    assert batch.status == batch_service.READY


def test_a_second_instruction_replaces_the_first_draw(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    provider.selection = {"mode": "explicit", "names": ["Marta"], "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "actually just Marta", intelligence)

    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    names = {person["worker_id"]: person["name"] for person in batch_service.roster(db, batch)}
    assert {names[target.worker_id] for target in targets} == {"Marta Alemu"}


# -- confirmation ---------------------------------------------------------- #


def test_declining_places_no_calls_and_clears_the_selection(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    replies = batch_service.handle_message(db, batch.batch_id, "no", intelligence)

    assert "nobody was called" in replies[-1].text
    assert BatchTargetRepository(db).for_batch(batch.batch_id) == []
    assert CallRepository(db).list() == []
    assert batch.status == batch_service.READY


def test_confirming_dials_every_selected_person_once(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    replies = batch_service.handle_message(db, batch.batch_id, "yes", intelligence)

    assert batch.status == batch_service.CALLING
    assert "Calling 4 now" in replies[-1].text
    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    assert all(target.call_id and target.status == "dialing" for target in targets)
    calls = CallRepository(db).list()
    assert len(calls) == 4
    assert all(call.call_id.startswith("demo_call_") for call in calls)


def test_the_interview_prompt_sent_to_each_call_carries_the_typed_questions(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "explicit", "names": ["Abebe"], "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call Abebe", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)

    call = CallRepository(db).list()[0]
    assert "Do u get enough compensation?" in call.prompt
    assert "FIRST QUESTION, ALWAYS" in call.prompt
    # The generated worker id contains digits and is echoed as the interview
    # reference; every other number is banned, because a number in an interview
    # prompt reads to a respondent as the answer we are hoping for.
    assert re.findall(r"\d+", call.prompt.replace(call.worker_id, "")) == []


def test_a_progress_question_while_calling_is_answered_without_a_model_call(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    before = len(provider.calls)
    replies = batch_service.handle_message(db, batch.batch_id, "how is it going?", intelligence)

    assert "0 of 4 interviews are back" in replies[-1].text
    assert len(provider.calls) == before


# -- results --------------------------------------------------------------- #


def test_answers_come_back_and_pass_two_categorizes_the_whole_batch(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    complete_all_calls(db, batch, intelligence)

    assert batch.status == batch_service.COMPLETE
    records = WorkerAnswersRepository(db).for_batch(batch.batch_id)
    assert len(records) == 4
    assert set(batch.categories) == {"age_years", "do_u_get_enough_compensation"}
    # One categorization call for the batch, not one per person.
    assert len(provider.calls_for("You group answers")) == 1


def test_a_child_is_flagged_excluded_and_counted_nowhere(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    complete_all_calls(db, batch, intelligence)

    records = {record.worker_id: record for record in WorkerAnswersRepository(db).for_batch(batch.batch_id)}
    hanna = next(
        person for person in batch_service.roster(db, batch) if person["name"] == "Hanna Girma"
    )
    assert records[hanna["worker_id"]].excluded is True

    summary = batch_service.batch_summary(db, batch)
    assert summary["interviews_counted"] == 3
    assert summary["excluded_total"] == 1
    assert summary["excluded"][0]["name"] == "Hanna Girma"
    for question in summary["questions"].values():
        assert sum(question["category_counts"].values()) <= 3
        assert sum(question["state_counts"].values()) == 3
    assert "15" not in json.dumps(summary)
    # The child's answers are not in the categorization request either.
    categorization = provider.calls_for("You group answers")[0]
    assert hanna["worker_id"] not in categorization["user"]


def test_a_refusal_is_counted_as_a_refusal_rather_than_guessed(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    complete_all_calls(db, batch, intelligence)

    counts = batch_service.batch_summary(db, batch)["questions"]["do_u_get_enough_compensation"]
    assert counts["state_counts"]["REFUSED"] == 1
    assert counts["category_counts"]["Declined to answer"] == 1


def test_a_call_that_never_produced_an_interview_still_finishes_the_batch(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "explicit", "names": ["Abebe", "Marta"], "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call Abebe and Marta", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)

    targets = BatchTargetRepository(db).for_batch(batch.batch_id)
    names = {person["worker_id"]: person["name"] for person in batch_service.roster(db, batch)}
    for target in targets:
        if names[target.worker_id] == "Abebe Kebede":
            batch_service.process_batch_call(db, target.call_id, TRANSCRIPTS["Abebe Kebede"], "am", intelligence)
        else:
            batch_service.mark_call_failed(db, target.call_id, "Nobody answered.")

    assert batch.status == batch_service.COMPLETE
    assert batch_service.batch_summary(db, batch)["interviews_counted"] == 1




def test_the_summary_card_is_posted_with_the_counts_attached(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    complete_all_calls(db, batch, intelligence)

    card = [
        message
        for message in BatchMessageRepository(db).for_batch(batch.batch_id)
        if message.payload.get("kind") == "summary"
    ][-1]
    assert "3 interview(s) counted, 1 excluded and not counted." in card.text
    assert card.payload["summary"]["interviews_counted"] == 3


def test_the_admin_can_then_ask_about_the_results(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider
):
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "all", "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call all of them", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    complete_all_calls(db, batch, intelligence)

    replies = batch_service.handle_message(db, batch.batch_id, "how many said the pay is not enough?", intelligence)
    assert replies[-1].text == "Two of the three counted interviews said the pay is not enough."
    analysis_call = provider.calls_for("You answer an administrator's questions")[0]
    # The model was handed the counts, not the records.
    assert '"interviews_counted": 3' in analysis_call["user"]
    assert "agent: how old are you?" not in analysis_call["user"]


# -- routing --------------------------------------------------------------- #


def test_a_batch_call_is_routed_away_from_the_clause_path(
    db: Session, intelligence: BatchIntelligence, provider: FakeBatchProvider, monkeypatch: pytest.MonkeyPatch
):
    """The clause path would report fourteen fixed facts and none of the typed
    questions, so ``dispatch_completed_call`` has to recognize a batch call."""

    monkeypatch.setattr(batch_service, "build_batch_intelligence", lambda provider=None: intelligence)
    batch = ready_batch(db, intelligence)
    provider.selection = {"mode": "explicit", "names": ["Abebe"], "confidence": "HIGH"}
    batch_service.handle_message(db, batch.batch_id, "call Abebe", intelligence)
    batch_service.handle_message(db, batch.batch_id, "yes", intelligence)
    target = BatchTargetRepository(db).for_batch(batch.batch_id)[0]

    assert batch_service.is_batch_call(db, target.call_id) is True
    assert batch_service.batch_result_pending(db, target.call_id) is True
    dispatch_completed_call(
        db,
        target.call_id,
        {
            "transcript": TRANSCRIPTS["Abebe Kebede"],
            "audio_url": None,
            "transcript_turns": [],
            "language": "am",
        },
    )
    assert batch_service.batch_result_pending(db, target.call_id) is False
    assert len(WorkerAnswersRepository(db).for_batch(batch.batch_id)) == 1
    assert batch.status == batch_service.COMPLETE
