# Callwise Implementation Plan

## 1. Document purpose

This is the final implementation plan for the Callwise/ProofCall validation
system. It replaces the previous generic batch architecture. The codebase will
be refactored around this plan only; there will be no separate legacy batch path,
fallback implementation, or parallel generic-question workflow. The new Callwise
flow is the only supported product flow.

The system calls beSingularity trainees, asks a fixed conversational KPI
questionnaire, receives TeleExpert voice results, extracts facts, applies
deterministic rules, stores pseudonymous records, aggregates results, and
exports an operational report.

The definitive pipeline is:

```text
admin settings in UI
    -> approved contact CSV
    -> validation and sampling
    -> fixed questionnaire + language prompt
    -> TeleExpert call
    -> webhook / provider status
    -> transcript and audio retrieval
    -> normalized transcript
    -> structured fact extraction
    -> deterministic KPI rules
    -> worker record and good-job annotation
    -> aggregation and quality metrics
    -> dashboard and CSV/XLSX/JSON export
    -> retention purge
```

## 2. Confirmed decisions

### Questionnaire

The pilot uses the fixed KPI questionnaire from this document. It does not use
arbitrary admin-defined questions.

The questionnaire wording, order, branch conditions, consent block, closing,
stop phrases, and time-control policy are versioned. The UI may show and preview
the fixed questionnaire, but the administrator cannot replace it with unrelated
questions.

### Configurable settings

Settings that affect the rules or reporting are configurable through an admin UI
and stored with each validation batch:

```text
minimum_wage_etb
small_cell_suppression_threshold
maximum_attempts
retry_delay_hours
call_window
maximum_call_duration_seconds
```

Changing settings never changes historical records. A batch stores a snapshot of
the settings used to evaluate it.

### ImpactProtocol

Direct ImpactProtocol integration and certificate generation are out of scope
for this implementation. The output must still be clean, pseudonymous, and
schema-valid so a future adapter can consume it.

### Pseudonymous IDs

Long-term records retain `beneficiary_id` and `record_id`. Names and phone
numbers are kept only in the restricted operational contact area and temporary
call material. Durable evidence records contain no names or phone numbers.

### Partial calls

Partial information is retained. Fields that were not reached or not answered
are stored as `null` with an explicit state such as `not_asked`, `unanswered`, or
`unclear`. The system must not invent values or mark a missing field as false.

## 3. Pilot requirements

The real validation contains:

- 100 primary contacts;
- 20 spare contacts held back for wrong numbers and disconnected lines;
- trainees who completed training between 2026-03-01 and 2026-05-31;
- approximately 50 placed/gig and 50 not-placed/lost-contact people;
- more than one cohort where available;
- gender distribution close to the source cohort.

The test measures:

```text
reach: 60 of 100 within two attempts
completion: 70% of answered calls reach closing
consent: 85% of people hearing consent agree
extraction: >=85% clause agreement on 20 bilingual reviews
false positives: zero met results unsupported by the transcript
Amharic: 80% complete in Amharic without English fallback
cost: <=$0.40 per completed interview including failures/retries
output: 100% of completed records validate against the schema
```

The false-positive target is a hard fail. `unclear` must never become `met`.

## 4. Target repository structure

Refactor the application into one coherent validation system:

```text
app/
  api/
    router.py
    routes/
      validation_batches.py
      contacts.py
      calls.py
      webhooks.py
      reports.py
      settings.py
      pages.py
  core/
    config.py
    security.py
    time.py
    retention.py
  db/
    base.py
    session.py
    migrations/
  models/
    validation_batch.py
    contact.py
    call.py
    attempt.py
    webhook_event.py
    artifact.py
    transcript.py
    worker_record.py
    clause_result.py
    aggregate.py
    review.py
    retention_event.py
  repositories/
    batches.py
    contacts.py
    calls.py
    attempts.py
    webhooks.py
    artifacts.py
    transcripts.py
    records.py
    aggregates.py
    reviews.py
  schemas/
    contact.py
    questionnaire.py
    call.py
    transcript.py
    worker_record.py
    aggregate.py
    export.py
    settings.py
  services/
    contact_import_service.py
    sampling_service.py
    questionnaire_service.py
    prompt_service.py
    teleexpert_service.py
    webhook_service.py
    completion_service.py
    extraction_service.py
    rules_service.py
    aggregation_service.py
    export_service.py
    review_service.py
    retry_service.py
    retention_service.py
  intelligence/
    extractor.py
    provider.py
    prompts.py
    schemas.py
  dashboard/
    templates/
    static/
tests/
  unit/
  integration/
  fixtures/
    contacts/
    transcripts/
    teleexpert/
```

The intelligence layer must not own database writes. Services orchestrate the
pipeline and repositories persist results.

## 5. Domain entities and database schema

### `validation_batches`

One complete validation run.

```text
batch_id                    primary key
client_name                 beSingularity
programme_name
status                      draft/validated/approved/calling/processing/complete/closed/purged
questionnaire_version
prompt_version
record_schema_version
language_default
minimum_wage_etb
small_cell_threshold
maximum_attempts
retry_delay_hours
calling_window_json
maximum_duration_seconds
source_file_checksum
source_row_count
eligible_row_count
primary_target_count
spare_target_count
created_at
started_at
completed_at
retention_deadline
```

### `contacts`

Operational contact data imported from the approved CSV.

```text
contact_id                  internal primary key
batch_id
beneficiary_id              pseudonymous external ID
first_name                  temporary greeting field, nullable
phone_e164                 temporary operational field
preferred_language
gender
age_band
training_cohort_id
training_end_date
placement_status_per_besingularity
placement_date
consent_to_followup_contact
notes
is_primary_sample
is_spare
eligible
rejection_reason
created_at
deleted_at
```

Phone numbers and names belong here, not in durable evidence records. Access to
this table must be restricted to authorized operators.

### `calls`

One logical interview for one contact.

```text
call_id                      internal primary key
batch_id
contact_id
beneficiary_id
teleexpert_call_id
state                        queued/dialing/retry_wait/in_progress/completed/failed/cancelled
disposition                  completed/partial/refused/no_answer/wrong_number/ineligible
attempt_count
language_opened
language_final
language_switched
closing_reached
consent_heard
consent_state
duration_seconds
cost_usd
failure_reason
created_at
updated_at
completed_at
```

### `call_attempts`

Every provider attempt is retained for reach, cost, and retry analysis.

```text
attempt_id
call_id
attempt_number
teleexpert_call_id
scheduled_at
started_at
ended_at
provider_state
disposition
failure_reason
duration_seconds
cost_usd
created_at
```

### `webhook_events`

Idempotency and debugging record.

```text
event_id
provider_event_id
provider_call_id
event_type
payload_hash
raw_payload_temporary
received_at
processed_at
processing_status
processing_error
```

The same provider event must be safe to deliver repeatedly.

### `call_artifacts`

Temporary source material.

```text
artifact_id
call_id
artifact_type                 raw_payload/transcript/audio
storage_reference
content_hash
content_type
retention_deadline
deleted_at
```

### `transcripts`

Normalized source conversation.

```text
transcript_id
call_id
raw_artifact_id
normalized_text
turns_json
detected_language
detected_languages_json
provider_warning
normalization_version
created_at
```

### `worker_records`

Durable pseudonymous result per call.

```text
record_id
batch_id
call_id
beneficiary_id
training_cohort_id
language
channel
interview_date
consent_json
employment_json
training_json
call_json
counted
good_job_status
unresolved_clause_count
failed_clause_count
aggregation_key_json
quotes_json
summary_en
flags_json
created_at
```

No names, phone numbers, audio URLs, or raw transcript text belong in this
durable table.

### `clause_results`

Normalized clause results for efficient querying.

```text
clause_result_id
record_id
clause_code
status                      met/not_met/unclear
confidence                 0.0 to 1.0
evidence_turn
evidence_text_temporary_or_approved
source
created_at
```

### `cohort_aggregates`

Cached, reproducible reporting output.

```text
aggregate_id
batch_id
training_cohort_id
metric_key
metric_value
denominator
suppressed
calculation_version
created_at
```

### `review_annotations`

The bilingual quality-review record.

```text
review_id
batch_id
record_id
reviewer_id
clause_code
system_status
reviewer_status
agreement
false_positive
comment
reviewed_at
```

### `retention_events`

Audit trail for deletion.

```text
retention_event_id
batch_id
artifact_type
items_due
items_deleted
executed_at
error
```

## 6. Contact CSV contract

The accepted CSV is UTF-8, comma-separated, with one header row and ISO dates.

```text
beneficiary_id
first_name
phone_e164
preferred_language
gender
age_band
training_cohort_id
training_end_date
placement_status_per_besingularity
placement_date
consent_to_followup_contact
notes
```

Validation rules:

- `beneficiary_id` is present and unique.
- `phone_e164` matches `+2519XXXXXXXX` with no spaces.
- Phone numbers are unique within the import.
- Language is `am`, `en`, or `other`.
- Gender is `F` or `M` where supplied.
- Age band is `15-24` or `25+` where supplied.
- Training date is between 2026-03-01 and 2026-05-31.
- Placement status is `placed_job`, `gig`, `not_placed`, `unknown`, or `lost_contact`.
- Consent starts with `yes` and has a date.
- Vulnerable or not-to-call records are excluded.
- Empty optional values remain empty.

The UI must show accepted, rejected, warning, and excluded counts before the
batch can be approved.

## 7. Sampling

Sampling is deterministic and auditable:

1. Filter eligible consented records.
2. Remove vulnerable/not-to-call records.
3. Split placed/gig and not-placed/lost-contact groups.
4. Draw approximately 50 from each group.
5. Check gender and cohort balance against the source pool.
6. Select 20 additional spares.
7. Store the selected/spare decision for every contact.

Spares stay out of the initial run. They may be activated by an administrator
after a primary contact is terminally unreachable or invalid.

## 8. Fixed questionnaire and conversation sequence

### Before the questions

The exact sequence is:

```text
language preference
opening
spoken consent
actual questionnaire
closing
```

The agent must not ask age or any other data question before consent is granted.

### Language preference

The first sentence is spoken in Amharic and asks whether Amharic or English is
easier. The selected language is used for the call. Amharic is the default.
Mixed language is recorded as `mixed_am_en`; fallback is explicitly flagged.

### Opening

The opening says, in one short sequence:

```text
who is calling -> on whose behalf -> why -> approximate duration
```

It explains that the call concerns the beSingularity sales training, takes about
five minutes, and does not affect training or employment.

### Spoken consent

The consent block is approximately 40 seconds and must occur before Q01:

```text
Before I start, three things.

One. I do not need your name. I can write your answers without it.

Two. You can stop at any time. Say stop and I stop. Say do not record this and I
delete this whole call, including what you already told me.

Three. You can answer some questions and leave others. Saying nothing to a
question is a normal answer.

May I begin?
```

Items stay separate and default to no. The final Amharic wording must be
bilingual-reviewed and approved before live calling.

### Sixteen-question bank

| ID | Question | Branch | Purpose |
|---|---|---|---|
| Q01 | How old are you? | all | age and child-labour safeguard |
| Q02 | Are you working at the moment, in any kind of work, paid by someone or on your own? | all | activity |
| Q03 | Is that work in sales or dealing with customers? | working | programme placement intent |
| Q04 | Who pays you: a company, your own business, or is it day by day or by season? | working | employment type and tenure branch |
| Q05 | Which month and year did you start this work? | working | tenure |
| Q06 | Since you started, has it run without a break, or were there times with no work? | gig/daily/seasonal | continuity |
| Q07 | In a normal week, how many days do you work, and about how many hours on a day? | working | hours |
| Q08 | In a normal month, how much do you take home? Is anything taken off before you get it? | working | wage and deductions |
| Q09 | Are you free to leave this work whenever you want, with nothing owed and nobody holding your papers? | working | forced labour |
| Q10 | Are you paid and treated the same as other people doing the same work there? | working | discrimination |
| Q11 | Can workers raise a problem together, and is there someone who speaks for them? | working | association |
| Q12 | After the training ended, how long was it until your first work? | all | training-to-work time |
| Q13 | How much did the training help you get that work: a lot, some, a little, or not at all? | all | training contribution |
| Q14 | Which part of the training do you use most in your work today? | all | skills used |
| Q15 | From 1 to 5, how satisfied are you with the training? | all | satisfaction |
| Q16 | What else has changed for you since the training? | all | other changes |

After Q05, the optional company-name question is asked only with consent. A
refusal leaves the field null and the employer is never contacted.

### Non-working branch

For a person who is not working or searching, replace Q03–Q11 with:

```text
N01 After the training ended, what happened?
N02 Did anyone contact you after the training, from beSingularity or from a company?
N03 What would have had to be different for you to be working now?
```

Capture these answers as verbatim themes. Do not defend or promote the training.
Then ask the non-working versions of Q12–Q16.

### Closing

The closing is approximately 20 seconds and must be delivered after the final
available question:

```text
Thank you. I wrote down what you said about your work and about the training,
without your name. beSingularity sees the summary of the whole group. If you
want your answers removed later, tell beSingularity and they will be deleted.
Good luck.
```

## 9. Conversation guardrails

The agent must:

- ask one idea at a time;
- clarify vague answers without leading;
- establish age early, immediately after consent;
- never reveal thresholds;
- never promise a job, money, placement, or remedy;
- never mention another beneficiary;
- never contact an employer;
- respect silence, refusal, and callback requests;
- stop immediately on an under-15 answer;
- stop the relevant line on forced-labour or wage-danger signals.

The agent must not say `clause`, `KPI`, `indicator`, or `criterion` to the worker.

## 10. Duration control

The complete call should be under 360 seconds. The timer starts after the call is
answered and includes language preference and consent.

If the call is running long, omit questions in this order:

```text
Q16, then Q03, then Q12
```

Never omit Q09, Q10, or Q11 because the KPI result cannot be safely determined
without them. Store omitted questions as `not_asked`, not as null without state.

## 11. Unsuccessful-call policy

The system must not report a call as successful merely because TeleExpert
accepted the HTTP request.

### Default retry policy

Maximum two phone attempts per person:

```text
attempt 1 -> if no answer/provider temporary failure
wait at least 24 hours -> attempt 2 at a different time window
```

The 24-hour delay is measured from the previous attempt’s end time. The retry
must also respect the calling window and avoid the documented Friday and Sunday
periods.

### Failure categories

| Failure | Action |
|---|---|
| No answer | Schedule one retry after 24 hours |
| Busy/temporary provider failure | Retry after 24 hours, subject to maximum two attempts |
| Wrong number | Mark terminal; do not retry automatically; activate a spare if appropriate |
| Invalid number | Mark terminal and flag import data |
| Declined consent | Do not retry unless the person explicitly requests a callback |
| `stop` during call | Preserve partial data; no automatic retry |
| `do not record this` | Void all content; no retry unless separately consented |
| Under 15 | Stop and exclude permanently |
| Forced labour/wage safeguarding | Stop that line and escalate; no automatic pressure call |
| TeleExpert technical failure before dialing | Retry provider request safely using idempotency, without creating another phone attempt |
| Completed without transcript | Mark failed processing; retrieve again, do not count yet |

After the second unsuccessful attempt, set:

```text
disposition = no_answer
call state = failed
```

The worker remains visible in the operational list but is not counted as a
completed or excluded interview. The dashboard must show why it was not reached.

### Rescheduling UI

The UI should show a retry queue with:

- next eligible time;
- reason for retry;
- attempt number;
- allowed calling window;
- whether a spare may be activated.

The scheduler runs automatically. An administrator may postpone or cancel a
retry, but cannot exceed the configured maximum without an explicit batch setting
change recorded in the audit log.

## 12. TeleExpert request and response adapter

The request adapter sends:

```json
{
  "phone_number": "+2519XXXXXXXX",
  "prompt": "versioned generated prompt",
  "response_format": "both",
  "retries": 0,
  "retry_delay_seconds": 0,
  "answer_timeout_seconds": 60,
  "webhook": {
    "url": "http://CALLPROOF_HOST/api/teleexpert/webhook",
    "secret": "configured secret"
  }
}
```

Application-level retries are scheduled by Callwise so the 24-hour policy is
enforced consistently. Provider-level automatic retries must not silently create
more than the configured attempts.

Each request uses:

```text
Idempotency-Key: callwise:{batch_id}:{beneficiary_id}:attempt:{n}
```

TeleExpert responses may contain nested transcript data, top-level turns,
speaker roles, audio references, detected language, and warnings. The adapter
must normalize all supported shapes into one internal response.

## 13. Webhook lifecycle

```text
receive webhook
  -> verify HMAC/signature
  -> check event ID and payload hash
  -> acknowledge quickly
  -> update provider call state
  -> fetch authoritative call result
  -> fetch transcript and audio metadata
  -> persist temporary artifacts
  -> queue completion processing
```

Webhook processing is idempotent. A duplicate event returns success without
duplicating a call, record, aggregate, or retry.

## 14. Transcript and audio processing

When a call is completed:

1. Fetch the authoritative call status.
2. Fetch the raw conversation/transcript.
3. Fetch or record the audio reference.
4. Store raw material temporarily.
5. Normalize speaker turns and timestamps.
6. Preserve Amharic text exactly.
7. Preserve detected language and provider warnings.
8. Send the normalized transcript to extraction.
9. Delete temporary material after the retention deadline.

The system stores both mixed conversation turns and a normalized text form. It
does not replace Amharic with English. `summary_en` is a separate generated
field and must not be treated as the source evidence.

## 15. Extraction contract

Gemini receives only the normalized transcript, questionnaire version, and
relevant contact metadata. It returns facts, not a good-job decision.

Every extracted field has:

```json
{
  "value": null,
  "state": "stated|unclear|refused|not_asked|unanswered|not_applicable",
  "confidence": 0.0,
  "evidence_turn": null,
  "evidence_text": null,
  "source": "worker|system|none"
}
```

Fact groups:

```text
age_years
employment_status
employment_type
sales_related
start_date
continuous
days_per_week
hours_per_day
hours_per_week
monthly_take_home_etb
deductions_reported
free_to_leave
equal_treatment
worker_representation
months_to_first_placement
training_helped
skills_used
satisfaction_1_5
other_changes
```

The extractor must preserve refusals and distinguish missing from negative.

## 16. Deterministic KPI rules

The rule engine receives extracted facts and batch settings.

It calculates:

```text
age_ok
hours_ok
tenure_ok
wage_ok
no_child_labour
no_forced_labour
no_discrimination
association_ok
seasonal_over_6m
```

The thresholds are never disclosed in the interview prompt. The UI can display
and edit the active settings before batch approval.

Rule behavior:

```text
explicit supporting evidence -> met
explicit harmful evidence -> not_met
missing/vague/refused/conflicting evidence -> unclear
```

The rule engine must calculate weekly hours from days and hours when necessary,
normalize dates, handle employment type, and apply the configured wage value.

Every clause stores status, confidence, evidence turn, source, and calculation
explanation for internal review.

`counted=true` only when every required clause is `met`. Otherwise the record is
not a confirmed good job. Under-age and voided records are excluded separately.

## 17. Worker record output

Each contact has exactly one final disposition record. A completed record has:

```json
{
  "record_id": "CW-014",
  "beneficiary_id": "BSG-2026-0143",
  "training_cohort_id": "COH-2026-04",
  "language": "am",
  "channel": "voice",
  "interview_date": "2026-09-18",
  "call": {
    "attempts": 2,
    "disposition": "completed",
    "duration_seconds": 331,
    "language_switched": false,
    "cost_usd": 0.28
  },
  "consent": {
    "state": "granted_no_name",
    "name": false,
    "quote": false,
    "voice": false,
    "photo": false,
    "voided_at_turn": null,
    "vulnerable_group_script": false
  },
  "employment": {},
  "clauses": {},
  "counted": false,
  "unresolved_clause_count": 0,
  "training": {},
  "aggregation_key": {"age_band": "25+", "gender": "F"},
  "quotes": [],
  "summary_en": "",
  "flags": []
}
```

Partial records preserve known facts and represent missing facts explicitly:

```json
{
  "call": {"disposition": "partial"},
  "employment": {
    "status": {"value": "working", "state": "stated"},
    "hours_per_week": {"value": null, "state": "not_asked"}
  },
  "clauses": {
    "age_ok": {"status": "met"},
    "hours_ok": {"status": "unclear"}
  }
}
```

Refused, no-answer, wrong-number, and ineligible records have empty or null
facts, the correct disposition, and an explicit aggregation exclusion reason.

## 18. Aggregation

Aggregation is code-driven and recalculable from records. Produce per cohort and
overall results for:

- selected, reached, completed, partial, refused, no-answer, wrong-number;
- consent and completion rates;
- worker-reported employment/placement rate;
- original beSingularity placement rate;
- good-job rate;
- all nine clause statuses;
- employment type;
- satisfaction distribution 1–5;
- training contribution;
- non-working themes;
- gender and age bands where safe;
- Amharic completion and fallback;
- average duration, total cost, and cost per completed interview;
- safeguarding and under-age totals.

The original placement claim and worker answer remain separate fields. A
disagreement is reported as a difference for review, not as proof that either
party is wrong.

### Small-cell suppression

The configured suppression threshold is applied to sensitive cohort/category
cells. Suppressed cells expose `suppressed=true` and not the exact number.
Quotes are suppressed when they could identify a person.

## 19. Export design

### Pseudonymous evidence CSV

One row per beneficiary and columns for facts, clauses, status, confidence,
language, cohort, cost, and flags. Include `beneficiary_id` but never name,
phone, audio URL, or raw transcript.

### Internal follow-up CSV

Restricted operational export containing permitted name/contact details,
reached/not-reached status, working/not-working status, and follow-up notes.

### XLSX

Workbook sheets:

```text
Overview
Cohort summary
Worker evidence (pseudonymous)
Clause results
Satisfaction
Non-working themes
Quality review
```

### JSON

Schema-valid batch metadata, records, clause results, aggregates, flags, and
processing timestamps. ImpactProtocol upload is not implemented now, but the
schema remains stable for a future adapter.

## 20. Dashboard pages

### Admin settings

Configure and preview:

- fixed questionnaire version;
- minimum wage;
- small-cell threshold;
- retry policy;
- calling window;
- duration limit;
- default language;
- retention deadline.

The UI must show that the question set is fixed and which rule settings are
active.

### Batch overview

Show:

- import and sampling status;
- primary and spare counts;
- reach rate;
- completion rate;
- consent rate;
- Amharic rate;
- cost;
- processing status;
- false-positive review status;
- cohort charts;
- export buttons.

### Live calls

Show every call and attempt:

```text
beneficiary_id, attempt, provider call ID, state, scheduled time,
language, duration, retry time, failure reason
```

### Worker evidence

Show consent, disposition, employment facts, all nine clauses, confidence,
evidence turns, flags, partial fields, and good-job annotation. Audio and raw
transcript links are temporary and restricted.

### Quality review

Show transcript, extracted fact, clause result, reviewer result, agreement, and
false-positive status side by side.

## 21. Background jobs

The scheduler performs:

```text
due call dispatch
retry scheduling after 24 hours
provider status reconciliation
webhook recovery
completed-call processing
aggregate refresh
retention purge
```

Each job is idempotent and logs a batch ID, call ID, attempt number, and result.
One failed item must not stop the rest of the batch.

## 22. API contract

Recommended endpoints:

```text
POST   /api/validation-batches
GET    /api/validation-batches
GET    /api/validation-batches/{batch_id}
POST   /api/validation-batches/{batch_id}/contacts/import
POST   /api/validation-batches/{batch_id}/validate
POST   /api/validation-batches/{batch_id}/sample
POST   /api/validation-batches/{batch_id}/approve
POST   /api/validation-batches/{batch_id}/start
GET    /api/validation-batches/{batch_id}/calls
GET    /api/validation-batches/{batch_id}/records
GET    /api/validation-batches/{batch_id}/report
GET    /api/validation-batches/{batch_id}/export?format=csv
GET    /api/validation-batches/{batch_id}/export?format=xlsx
GET    /api/validation-batches/{batch_id}/export?format=json
POST   /api/teleexpert/webhook
GET    /api/calls/{call_id}
GET    /api/calls/{call_id}/transcript
GET    /api/calls/{call_id}/audio
POST   /api/calls/{call_id}/retry
POST   /api/calls/{call_id}/cancel
GET    /api/settings
PUT    /api/settings
```

Starting a batch requires an explicit confirmation because 100 calls cannot be
recalled. The scheduler then dispatches calls automatically according to the
approved policy.

## 23. Testing plan

### Unit tests

Test:

- CSV encoding and every validation rule.
- Consent filtering.
- Sampling balance.
- Question ordering and branch selection.
- Consent state machine.
- Stop and void behavior.
- Six-minute question dropping.
- Retry timing and maximum attempts.
- TeleExpert payload normalization.
- Webhook signature and duplicate handling.
- Fact extraction schema.
- Deterministic KPI rules.
- Under-15 exclusion.
- Partial null/state behavior.
- Small-cell suppression.
- Cost and rate calculations.
- CSV/XLSX/JSON schema output.
- Retention purge.

### Fixture transcripts

Include at least:

```text
normal Amharic employee
normal English employee
working gig worker
non-working trainee
ambiguous duration
salary refusal
under-15 stop
forced-labour signal
wage complaint
discrimination concern
association not met
language fallback
mixed Amharic/English
stop phrase
do-not-record void
partial call
no answer
wrong number
```

### End-to-end fixture test

```text
fixture provider payload
-> webhook event
-> call completion
-> transcript normalization
-> extraction fake/provider
-> deterministic rules
-> worker record
-> aggregate
-> dashboard report
-> CSV/XLSX/JSON export
```

This test must run without a Gemini key, TeleExpert network, or phone call.

### Live smoke test

Before the 100-person pilot:

1. Test one Amharic call.
2. Test one English call.
3. Test both Amharic stop phrases.
4. Test consent refusal.
5. Test webhook duplication.
6. Test audio/transcript retrieval.
7. Test no-answer retry after 24 hours.
8. Test under-15 stopping.
9. Test partial record storage.
10. Confirm no threshold is spoken.

## 24. Implementation order by team

### Shared foundation

1. Freeze this document and schema versions.
2. Decide data-controller and processing terms.
3. Choose persistent database location outside `/tmp`.
4. Create a separate real-pilot database.
5. Add migrations and audit logging.

### Backend/operations team

1. Replace old batch models with validation-batch models.
2. Implement contact import and strict validation.
3. Implement stratified sampling and spare management.
4. Implement fixed questionnaire storage and settings snapshot.
5. Implement TeleExpert adapter and attempt records.
6. Implement webhook verification and idempotency.
7. Implement transcript/audio artifact storage.
8. Implement automatic scheduler and 24-hour retry policy.
9. Implement record persistence and retention jobs.
10. Implement aggregation and export services.
11. Implement dashboard pages and review UI.

### Intelligence/rules team

1. Freeze the question graph and prompt versions.
2. Produce reviewed Amharic wording.
3. Implement language/opening/consent/closing prompt blocks.
4. Implement working and non-working branches.
5. Implement structured fact extraction.
6. Implement evidence and confidence handling.
7. Implement deterministic nine-clause rules.
8. Implement good-job annotation and flags.
9. Implement English summary and non-working theme extraction.
10. Provide fixture outputs for every hard case.

### Integration checkpoint

The shared handoff is:

```text
TeleExpert result
-> normalized transcript
-> extracted facts
-> deterministic worker record
-> persisted aggregate
```

The intelligence team owns extraction and rules. The operations team owns
transport, persistence, retries, aggregation, and presentation. Neither team
should bypass the contract.

## 25. Production readiness gates

Do not import the real 100-person list until:

- controller/processor terms are signed;
- secure transfer method is ready;
- persistent database is configured outside `/tmp`;
- demo and pilot data are separated;
- Amharic script is reviewed aloud;
- stop phrases are tested;
- wage and suppression settings are configured in the UI;
- contact import rejects invalid consent and numbers;
- webhook signatures and duplicates are tested;
- retries obey the 24-hour policy;
- partial records preserve known values and null missing values;
- under-15 records are excluded;
- `unclear` cannot become `met`;
- exports validate;
- purge behavior is tested;
- one Amharic and one English smoke call pass.

## 26. Final architecture decision

Refactor the full codebase into one Callwise validation product:

```text
fixed questionnaire
 + configurable rule/reporting settings
 + TeleExpert voice execution
 + evidence extraction
 + deterministic KPI evaluation
 + pseudonymous worker records
 + cohort analysis
 + operational retry management
 + dashboard and exports
```

Delete or replace the old generic admin-question path during the refactor. Do
not retain it as a fallback, compatibility route, demo route, or parallel
pipeline. All UI routes, services, models, exports, background jobs, and tests
must target the Callwise fixed-questionnaire flow. Existing data should be
migrated only when it can be mapped safely to the new schema; otherwise it must
be archived or reset before pilot use. Do not implement direct ImpactProtocol
integration in this phase. The system must be privacy-aware, auditable,
retry-safe, and conservative whenever evidence is incomplete.
