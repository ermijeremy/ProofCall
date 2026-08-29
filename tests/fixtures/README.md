# Transcript fixtures

Seven recorded interviews that stand in for TeleExpert calls. They exist so the
whole chain can be tested without a phone, a network, or an API key, and so the
same transcript always produces the same verdict.

```
tests/fixtures/transcripts/*.json
  -> CallProofEngine(RecordedProvider)      app/intelligence/engine.py
  -> CompletedCallResult                    app/contracts/integration.py
  -> store_completed_result                 app/services/completion_service.py
  -> aggregate_company / aggregate_programme
```

Run the chain:

```sh
.venv/bin/python -m pytest tests/integration/test_engine_pipeline.py -q
```

Use them in your own test:

```python
from app.intelligence.engine import CallProofEngine
from app.intelligence.providers.fixture import RecordedProvider

engine = CallProofEngine(RecordedProvider.from_fixture_dir("tests/fixtures/transcripts"))
result = process_completed_call(db, call_id="fixture-W001", transcript=..., engine=engine)
```

`RecordedProvider` keys recordings on the transcript text, not on the worker or
the call, so a recording can never be served for a transcript it was not
produced from. An unknown transcript raises `ProviderError`, which the engine
turns into an empty fact set — the result is `UNCLEAR`, never a confirmation.

## The cases

| Fixture | Worker | Verdict | What it exercises |
| --- | --- | --- | --- |
| `normal_worker` | W001 | `CONFIRMED_GOOD_JOB` | The happy path. Every clause `MET`, no contradictions. |
| `salary_contradiction` | W002 | `CONFIRMED_GOOD_JOB` | A **material** salary contradiction on a worker whose job qualifies. Worker says 4200, employer claims 8500 — 50.6% below. |
| `employment_status_contradiction` | W015 | `NOT_CONFIRMED` | The employer says employed, the worker left two months ago. Carries a *second*, non-material salary contradiction (8000 vs 8500, 5.9%). |
| `ambiguous_duration` | W017 | `UNCLEAR` | "I started after the rains." Three temporal follow-ups all fail; duration stays `null`. |
| `salary_refusal` | W018 | `UNCLEAR` | Pay declined. `REFUSED`, `HIGH` confidence, value `null`, and the employer's figure is never substituted. |
| `under_15_stopped` | W019 | `STOPPED` | Age 14. The interview ends immediately, `safeguarding_flag: true`, every other clause `NOT_ASKED`. |
| `ambiguous_duration_am` | W017 | `UNCLEAR` | Amharic rendering of `ambiguous_duration`. Optional; marked `variant_of`. |

W015 and W002 are the two workers carrying a **material** contradiction. The
specification's third is W016, in the seed data.

## Fixture shape

Every file carries the same keys:

| Key | Purpose |
| --- | --- |
| `fixture`, `description` | Name and what the case is for. |
| `worker_id`, `call_id`, `language` | Identity. `call_id` is `fixture-<worker_id>`. |
| `employer_claims` | What the employer reported for this worker: `{"currently_employed": true, "salary": 8500}`. |
| `transcript` | Normalized plain text, `Interviewer:` / `Worker:` per line. |
| `transcript_turns` | The same content as speaker-labelled turns. |
| `expected_extraction` | What the **LLM** should return. Replayed by `RecordedProvider`. |
| `expected_result` | What the **rule engine** should produce. Asserted exactly. |
| `variant_of`, `needs_native_review` | Present on variants only. |

`expected_extraction` is deliberately threshold-free. Facts carry a `value`, a
`state` of `STATED` / `REFUSED` / `VAGUE` / `NOT_ASKED`, a `confidence` of
`HIGH` / `MEDIUM` / `LOW`, and `evidence` quoted verbatim. Nothing in it says
`MET`, `NOT_MET`, or names a verdict — those are decisions, and decisions are
made by code.

Confidence is recorded per fact because judging how definite the wording was is
the extraction step's job. The rule engine may lower a confidence (a derived
clause takes the weaker of its two facts; a clause with no value drops to `LOW`)
but never raises one.

Every `evidence` string is a verbatim substring of that fixture's own
transcript, and `tests/unit/test_fixtures.py` enforces it. A derived clause may
quote two turns, joined by `" ... "` (`rules.EVIDENCE_JOIN`); each part is
independently verbatim.

## Conventions worth knowing before you read a clause

**The three labour-rights clauses store whether a violation is present.** So
`forced_labour: {"value": false, "status": "MET"}` means *no* forced labour. The
same holds for `discrimination` and `freedom_of_association`. Reading `false` as
a failure inverts the meaning of the whole record.

**`working_hours` is a weekly total the code derives.** Respondents answer in
days per week and hours per day; `rules._evaluate_working_hours` multiplies them.
The clause value is `days × hours`.

**Under the minimum age is `STOPPED`, not `NOT_MET`.** A child working is a
safeguarding event, reported separately and excluded from good-job counts, not
counted as a bad job.

**A refusal is `REFUSED`, not `NOT_MET`.** It blocks confirmation, because pay
must be established, but it is never held against the respondent.

**Salary has no floor.** Any stated amount satisfies the clause. A large gap
against the employer's figure is a contradiction, which is a separate axis.

**Verdict precedence** is `STOPPED` > `NOT_CONFIRMED` > `UNCLEAR` >
`CONFIRMED_GOOD_JOB`. An established failure outranks missing evidence because a
failure is a determination and ambiguity is the absence of one.

**Consent is separate from the verdict.** A refusal to be interviewed is
`UNCLEAR` with `consent: false`, not a fifth verdict and not `STOPPED`, so
`STOPPED` keeps meaning "safeguarding" on the dashboard.

**Salary materiality uses a 10% tolerance.** W002 at 50.6% is material; W015 at
5.9% is not. That is why the `material` flag discriminates instead of always
being true.

**A contradiction is recorded, never adjudicated.** Both values are stored with
the respondent's own words. CallProof does not decide who is lying, and no
contradiction ever changes a clause status or a verdict.

## Running against the live model

The fixtures exist so nothing *needs* a key, but the same transcripts are also
the check that the real model still extracts what the rule engine expects:

```sh
GEMINI_API_KEY='["key1","key2"]' .venv/bin/python -m pytest tests/unit/test_providers.py -q
```

`GEMINI_API_KEY` accepts a JSON array as well as a single key. Calls start at a
different key each time to spread per-minute quota, and fail over on quota or
model-availability errors, so a run of twenty interviews does not stall on one
key's limit. Keys are masked in every log and error message.

Do not set `GEMINI_MODEL=gemini-2.5-flash`. Google gates that model to projects
that already used it, and a newer key gets `404 NOT_FOUND ... no longer available
to new users` — while the model still appears in `models.list()`, so the failure
only surfaces on the first real call. The default is `gemini-3.6-flash`.

## Two things for Member B

**1. `compare_with_employer` is passed the wrong object.**
`app/services/completion_service.py:71` passes the company-level `Employer` row.
`currently_employed` is a *per-worker* claim living on
`Beneficiary.employer_claims` (`app/models/beneficiary.py:19`) and has no column
on `Employer`, so the employment-status contradiction cannot be detected as
wired. The salary comparison only appears to work because
`Employer.average_salary` equals every seeded worker's claim.

The fix is one line — pass `beneficiary` instead. No rules change is needed:
`rules.employer_claims()` already accepts a dict, a `Beneficiary`, or an
`Employer`. Both states are pinned in
`tests/integration/test_engine_pipeline.py`; the tests named `test_defect_…`
assert today's behaviour and will fail when you fix it, which is intentional.

**2. The material-contradiction count.** `_counts` in
`app/services/aggregation_service.py:20-22` counts *workers* with at least one
material contradiction. The spec's headline is 3: W002 (salary), W015
(employment status), W016 (seed). W002 is a `CONFIRMED_GOOD_JOB` worker who
nonetheless carries a material contradiction, so the count must not be derived
from the not-confirmed set.

## Adding a fixture

Copy the closest existing file and keep the key order. Then:

1. Make every `evidence` string a literal substring of your `transcript`.
2. Give each fact a `confidence`; use `HIGH` for a clear refusal and `LOW`
   whenever no value was established.
3. Derive `expected_result` by hand from the thresholds in
   `app/intelligence/criteria.py` — do not copy it from a test run, or the test
   asserts nothing.
4. If it restates an existing case in another language, add
   `"variant_of": "<parent fixture>"` so it is excluded from the
   one-fixture-per-worker check.

`tests/unit/test_fixtures.py` validates the fixture against the contract and
`tests/unit/test_rules.py` asserts the engine reproduces `expected_result`
exactly, so a new fixture is covered the moment you add the file.

`ambiguous_duration_am.json` is marked `needs_native_review: true` and still
needs a native Amharic speaker's check. Its speaker labels are Amharic
(`ጠያቂ:` / `ሠራተኛ:`) on purpose, so nothing downstream can key on the strings
"Interviewer" or "Worker".
