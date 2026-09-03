# Callwise Validation Architecture

## Purpose and scope

This document defines the Sequa/beSingularity validation pilot described in the
Callwise call-flow and contact-list documents. It is a controlled validation
mode beside the generic admin-defined batch workflow. The existing batch path
may remain as a fallback, but real pilot data must use this versioned workflow.

The central boundary is:

```text
Callwise decides who to call, what to establish, and what evidence means.
TeleExpert dials, conducts the conversation, and returns what happened.
```

## End-to-end flow

```text
approved CSV -> consent/eligibility checks -> stratified sample (100 + 20 spares)
-> versioned questionnaire and language prompt -> TeleExpert call
-> webhook and authoritative result retrieval -> raw transcript/audio reference
-> transcript normalization -> LLM fact extraction -> deterministic KPI rules
-> pseudonymous worker record -> cohort aggregation -> dashboard and exports
-> retention purge
```

Gemini extracts facts. Python applies the rules. Gemini must never directly
decide that a person has a good job.

## Data ownership and privacy

beSingularity keeps the mapping from `beneficiary_id` to a real person. Callwise
receives only the approved contact CSV and returns pseudonymous evidence. No
addresses, identity documents, health data, family information, training files,
or unnecessary financial detail should be transferred.

The proposed controller model is:

```text
beSingularity = data controller
Callwise = processor under written instructions
ImpactProtocol = recipient of pseudonymous evidence
sequa = programme/reporting stakeholder
```

This is an open legal decision and must be agreed in writing before the list is
transferred. The list must move through an expiring shared link restricted to
named people or an encrypted archive with the password sent separately. No
WhatsApp or ordinary email attachment.

Raw CSV, audio, raw transcripts, and intermediate files are temporary. They are
deleted 30 days after the last call. Long-term records contain no names or phone
numbers. Purge events must be auditable.

## Validation batch

Suggested entity: `validation_batch`.

```text
batch_id, client_name, programme_name, questionnaire_version, prompt_version,
schema_version, status, source_checksum, source_row_count, target_count,
spare_count, created_at, started_at, completed_at, retention_deadline
```

States:

```text
draft -> validated -> approved -> calling -> processing -> complete -> closed -> purged
```

The pilot has 100 primary contacts and 20 spares. The sample should be close to
50 placed/gig and 50 not-placed/lost-contact, use more than one cohort where
available, and preserve the source gender distribution.

## Contact import and sampling

Required columns:

```text
beneficiary_id, first_name, phone_e164, preferred_language, gender, age_band,
training_cohort_id, training_end_date, placement_status_per_besingularity,
placement_date, consent_to_followup_contact, notes
```

The importer validates UTF-8, headers, dates, `+2519XXXXXXXX` numbers, duplicate
IDs/numbers, languages, gender, age bands, placement values, and the training
date range `2026-03-01` through `2026-05-31`.

Only consent values beginning with `yes` and containing a consent date are
eligible. Vulnerable and not-to-call records are removed before sampling. The
import result must include accepted rows, rejected rows, warnings, and a
correction report. It must never guess a missing value.

The sampling audit records the placed/not-placed split, gender/cohort balance,
primary/spare status, random selection trace, and every exclusion reason.

## Questionnaire and prompt architecture

The questionnaire is a versioned question graph, not one long prompt. Each turn
expresses one idea and avoids words such as clause, KPI, indicator, or criterion.

The 16-item bank covers:

1. language preference;
2. age;
3. current work status;
4. sales/customer work;
5. employment type;
6. start month and year;
7. continuity for gig/daily/seasonal work;
8. days and hours;
9. take-home salary and deductions;
10. freedom to leave/forced labour;
11. equal treatment/discrimination;
12. worker representation/association;
13. time from training to first work;
14. training contribution;
15. training skills used;
16. satisfaction and other changes.

The non-working branch replaces employment questions with what happened after
training, whether anyone followed up, and what would have enabled work.

The generated prompt is assembled from versioned safety, language, opening,
consent, question-graph, branch, stop, time-control, closing, and extraction
components. It never reveals thresholds such as 20 hours, 4,500 ETB, or six
months.

Amharic is the default. The first sentence asks whether Amharic or English is
easier. Store `am`, `en`, `mixed_am_en`, or `other`, plus whether language
switched. Amharic wording and both stop phrases require bilingual human review
before the first live call; machine translation is not the final script.

Answered time is tracked in code. If the call exceeds the six-minute budget,
drop questions in this order: Q16, Q03, then Q12. Never drop forced labour,
discrimination, or association questions.

## Consent and safeguarding

Consent is a state machine:

```text
not_started, heard, granted_no_name, granted_name, declined, stopped, voided
```

`stop` ends the call and preserves prior answers. `do not record this` voids the
whole call, deletes earlier content, and keeps only a decline count. Silence or
skipping a question is a normal unanswered state.

An age below 15 stops the call, creates an anonymous safeguarding flag, and is
excluded from all job counts. Forced labour, unpaid wage, or withheld wage signs
are escalated and stop that line of questioning. Vulnerable people are removed
from this cold-call test.

## TeleExpert adapter and webhook

Use one provider adapter:

```text
TeleExpertClient.create_call()
TeleExpertClient.get_call()
TeleExpertClient.get_transcript()
TeleExpertClient.get_audio()
TeleExpertClient.cancel_call()
TeleExpertClient.verify_webhook()
```

Every request includes one phone number, the generated prompt,
`response_format=both`, maximum two attempts, retry delay, answer timeout,
webhook details, and an idempotency key:

```text
callwise:{batch_id}:{beneficiary_id}
```

Provider states are `queued`, `dialing`, `retry_wait`, `in_progress`,
`completed`, `failed`, and `cancelled`. Internal dispositions are `completed`,
`partial`, `refused`, `no_answer`, `wrong_number`, and `ineligible`.

Webhook flow:

```text
receive -> verify signature -> record event ID/hash -> acknowledge quickly
-> process once -> retrieve authoritative result -> update call -> extract
```

Store `webhook_event_id`, `provider_call_id`, `event_type`, `payload_hash`,
timestamps, processing status, and error message. Duplicate events must not
duplicate records or rerun analysis.

## Transcript, audio, and parsing

Persist the raw provider result before parsing. Preserve raw payload, speaker
turns, timestamps, original language, detected language, audio reference,
duration, and provider warnings. Keep these representations separate:

```text
raw_transcript
normalized_transcript
summary_en
```

Parsing is staged:

```text
provider payload
-> deterministic turn/language normalization
-> Gemini fact extraction with evidence and confidence
-> deterministic numeric/date normalization
-> deterministic clause evaluation
-> good-job annotation
```

Extracted facts include age, employment status/type, sales relation, start date,
continuity, days/hours, salary/deductions, forced labour, discrimination,
association, training outcomes, and satisfaction. Each fact carries value,
state, confidence, evidence turn, evidence text, and source.

Extraction states include `stated`, `unclear`, `refused`, `not_asked`, and
`not_applicable`. Missing, vague, or conflicting evidence must remain unclear.

## Deterministic KPI rules

Evaluate these nine clauses in code:

```text
age_ok, hours_ok, tenure_ok, wage_ok, no_child_labour,
no_forced_labour, no_discrimination, association_ok, seasonal_over_6m
```

Each clause stores:

```text
status, confidence, evidence_turn, source
```

Rule priority:

```text
explicit harmful fact -> not_met
explicit supporting fact -> met
missing/vague/conflicting fact -> unclear
```

`counted=true` only when every required clause is `met`. Also store
`good_job_status`, `unresolved_clause_count`, `failed_clause_count`, and flags.
Under-age records are excluded; voided consent produces no worker evidence
record.

The supplied example requires clarification: it reports an April start date,
September interview, five months elapsed, and `seasonal_over_6m=met`. The rule
for this case must be confirmed before production.

## Persistence model

Recommended entities:

```text
validation_batches, validation_contacts, validation_calls, validation_attempts,
webhook_events, raw_call_artifacts, normalized_transcripts, extracted_facts,
worker_records, worker_clause_results, worker_flags, cohort_aggregates,
review_annotations, retention_events
```

Index `batch_id`, `beneficiary_id`, `training_cohort_id`, disposition, language,
employment status, `counted`, good-job status, consent state, under-age flag,
satisfaction, and timestamps. Keep identity/contact data separate from durable
pseudonymous evidence data.

## Aggregation and reporting

Calculate aggregates from stored records, never from model prose. Report per
cohort:

- selected, reached, completed, refused, no-answer, wrong-number, and ineligible;enokyoseph@precision-5570:~/Desktop/team-project/TeleExpert$ touch .env
henokyoseph@precision-5570:~/Desktop/team-project/TeleExpert$ touch .api.env
henokyoseph@precision-5570:~/Desktop/team-project/TeleExpert$ 

- reach, completion, consent, Amharic, fallback, and cost metrics;
- worker-reported placement and original beSingularity placement separately;
- good-job rate and nine clause distributions;
- employment type, satisfaction 1–5, gender, and age bands where safe;
- non-working themes, safeguarding totals, duration, and cost.

Small sensitive cells must be suppressed. Confirm the suppression threshold
before the real report. Suppress quotes that could identify a person.

Exports:

```text
JSON = schema-valid pseudonymous evidence
CSV  = one worker per row, fact/question/clause columns
XLSX = monitoring workbook with cohort summaries and clause sheets
```

The ImpactProtocol/sequa export excludes names, phone numbers, audio URLs, and
raw transcripts unless explicitly approved. A separate restricted beSingularity
follow-up export may contain permitted contact details.

## Dashboard and review

Provide views for validation overview, live calls, worker evidence, cohort
report, retention, and bilingual quality review. Worker evidence shows consent,
employment, nine clauses, evidence turns, confidence, flags, and good-job status.

Twenty completed calls are hand-checked by a bilingual reviewer. Store system
and reviewer clause results side by side and calculate agreement and false
positives. The hard requirement is zero cases where the system says `met` while
the transcript supports only `unclear`.

## Pilot success metrics

```text
reach: 60 / 100 within two attempts
completion: 70% of answered calls reach closing
consent: 85% of people hearing consent agree
extraction: >=85% clause agreement over 20 reviewed calls
false positives: 0
Amharic: >=80% complete without English fallback
cost: <=$0.40 per completed interview including failures/retries
schema validation: 100% of completed records
```

## Implementation sequence

```text
0. Confirm business, controller, retention, threshold, suppression, and schema decisions
1. Freeze contact, questionnaire, record, aggregate, and export schemas
2. Implement strict contact validation and stratified sampling
3. Implement versioned bilingual question graph and prompt
4. Implement TeleExpert adapter, retries, webhook idempotency, and retrieval
5. Implement normalization, extraction, KPI rules, safeguarding, and annotation
6. Implement durable records, processing states, and retention purge
7. Implement cohort aggregation, dashboard, review, CSV, XLSX, and JSON exports
8. Run fixture-only validation without phone calls or network
9. Run controlled Amharic/English smoke calls
10. Run the 100-contact pilot with 20 spares
```

## Readiness gates

- Written controller/processor and retention terms are complete.
- Contact transfer uses an approved secure channel.
- Database is persistent and outside `/tmp`; demo and pilot data are separated.
- Amharic wording and stop phrases are reviewed aloud.
- Wage and small-cell thresholds are confirmed.
- Webhook signature, transcript, audio, and retry behavior are tested.
- Fixture records validate against the output schema.
- `unclear` cannot become `met`.
- Purge behavior is tested.
- One Amharic and one English smoke call pass before the full run.

## Open decisions

1. Fixed KPI questionnaire versus generic admin-defined questions for the pilot.
2. Wage threshold and small-cell suppression threshold.
3. Exact ImpactProtocol schema/version.
4. Whether long-term pseudonymous IDs are retained.
5. Allowed fields in internal follow-up exports.
6. Final Amharic wording and stop phrases.
7. Treatment of partial calls and callbacks.
8. Exact completion definition and commercial cost definition.
9. Data-processing signatories and accepted meeting slot.

## Final architecture decision

Keep the existing modular ProofCall foundation and add a dedicated Callwise
validation pipeline beside the generic batch path. Do not mix real pilot data
with demo data, do not let the model decide good-job status, and do not transfer
the contact list until controller, retention, and secure-transfer conditions are
documented.
