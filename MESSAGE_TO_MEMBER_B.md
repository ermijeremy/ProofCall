# Message to Member B

Member A intelligence is complete on `member-a-intelligence`. Full suite is 365
passed, and the pipeline is now verified against the live Gemini API, not only
against fixtures: all 7 fixture transcripts produce their expected verdict when
extracted by the real model.

Four things need something from you. The first one is demo-critical.

## 1. The engine is never registered, so the live app answers 503

This is the one to fix first. I verified it against the real ASGI app:

```
POST /api/calls/W001/process -> 503
{"detail":"Member A intelligence engine has not been configured"}
```

`app/main.py:16` startup only calls `init_db()`. Nothing ever calls
`configure_engine`, so `get_engine()` raises at
`app/api/routes/completions.py:38` and your handler correctly turns it into 503.
Every test passed because tests inject the engine explicitly — no test touched
the registration path, which is exactly why this stayed invisible. I have added
tests that cover it now.

The fix is one line in your startup:

```python
from app.intelligence.engine import try_register_default_engine

@app.on_event("startup")
def startup() -> None:
    init_db()
    try_register_default_engine()
```

Use `try_register_default_engine`, not `register_default_engine`. The latter
raises `ProviderError` when no API key is present, which would stop the app from
booting for anyone without a key. The `try_` variant logs a warning and returns
`None` instead, leaving the endpoint to answer 503 with its explanatory message.

It deliberately does **not** fall back to fixture data when the key is missing.
Registering the recorded provider there would make the dashboard present invented
interviews as real ones, which is the one failure this project cannot ship.

## 2. Dependencies, keys, and one model trap

Two lines changed in `requirements.txt`, and the second touches a pin you own:

```
httpx==0.28.1          # raised from 0.27.2
google-genai==2.8.0    # new
```

`httpx` had to move. Every published `google-genai` release requires
`httpx>=0.28.1`, so with `httpx==0.27.2` the dependency does not install at all —
pip fails with `ResolutionImpossible`. I checked both places this repository
touches httpx and neither uses anything 0.28 removed:
`tests/integration/test_api_surface.py:15` drives the app through
`httpx.ASGITransport`, and `TeleExpertClient` constructs `httpx.Client` with only
`base_url`, `timeout`, and `headers` (`app/integrations/teleexpert_client.py:28`).
Your API tests pass on 0.28.1.

`google-genai` is held at 2.8.0 on purpose. 2.9.0 and later require
`pydantic>=2.12.5`, which would force a bump of the `pydantic==2.9.2` pin the
application is built on. Say the word if you would rather bump pydantic and I
will move to the latest.

**The model trap, worth reading before the demo.** Do not use
`gemini-2.5-flash`. Google now gates it to projects that already used it, and a
newer key gets `404 NOT_FOUND ... no longer available to new users`. It still
appears in `models.list()` for those keys, so the failure only surfaces on the
first real call. I found this the hard way: 8 of our 12 keys 404'd on it. The
default is now `gemini-3.6-flash`, which all 12 serve. `GEMINI_MODEL` overrides
it.

**Key rotation.** `GEMINI_API_KEY` now accepts a JSON array of keys as well as a
single key. Calls start at a different key each time to spread per-minute quota,
and a quota or availability failure fails over to the remaining keys rather than
losing the interview. A key that fails authentication is parked after one failure
instead of costing a retry on every later call. Keys are masked in all log and
error output.

Real keys live in `.env`, which I added to `.gitignore` — please do not commit
one. `.env.example` documents the format.

**One thing you may want to fix in your own file.** `app/core/config.py` is
titled "Environment-backed application settings" but `Settings` is a plain
`BaseModel` with hardcoded defaults, so it never reads the environment at all —
`teleexpert_base_url` and `teleexpert_api_key` cannot currently be configured
without editing code. Nothing of mine depends on it: the Gemini provider reads
`os.environ` directly and falls back to parsing `.env` itself, precisely because
nothing else in the app loads that file.

## 3. `compare_with_employer` is passed the wrong object

`app/services/completion_service.py:71` passes the company-level `Employer` row.
The claim it needs — `currently_employed` — is a *per-worker* claim living on
`Beneficiary.employer_claims` (`app/models/beneficiary.py:19`), and `Employer` has
no such column. As wired, the employment-status contradiction cannot be detected:
W015 says they left the job two months ago while the employer reports them
employed, and the dashboard shows no such finding.

The salary comparison only *appears* to work because `Employer.average_salary`
happens to equal every seeded worker's claimed 8500. It breaks the moment one
worker's claim differs from the company average.

The fix is one line — pass `beneficiary` instead of `employer`. No rules change is
needed: `rules.employer_claims()` already normalizes a plain dict, a
`Beneficiary`, or an `Employer`. Both states are pinned in
`tests/integration/test_engine_pipeline.py`; the two tests named `test_defect_…`
assert today's behaviour and will fail when you fix it. That is intentional, and
their docstrings say so.

## 4. The material-contradiction count

`_counts` in `app/services/aggregation_service.py:20-22` counts *workers* with at
least one material contradiction. The spec's headline is 3: W002 (salary), W015
(employment status), and W016 (your seed).

W002 is the one worth checking, because it is a `CONFIRMED_GOOD_JOB` worker that
nonetheless carries a material contradiction — the job qualifies, and the
employer's salary figure for that worker is wrong by half. So the count cannot be
derived from the not-confirmed set. With finding 3 unfixed these fixtures produce
1 rather than 2, quantified in
`test_defect_costs_the_dashboard_one_material_contradiction`.

## Minor: `datetime.utcnow()`

Your models and `completion_service.py:32` use `datetime.utcnow()`, which emits a
`DeprecationWarning` on Python 3.13 — about 980 across a full run. Nothing breaks;
it is noise that hides real warnings. Your code, so I left it alone.

## What is on the branch

Six required fixtures plus an optional Amharic variant, in
`tests/fixtures/transcripts/`, with a README covering the format, the clause
conventions, and how to add a case.

| Fixture | Worker | Verdict |
| --- | --- | --- |
| `normal_worker` | W001 | `CONFIRMED_GOOD_JOB` |
| `salary_contradiction` | W002 | `CONFIRMED_GOOD_JOB` + material salary contradiction |
| `employment_status_contradiction` | W015 | `NOT_CONFIRMED` |
| `ambiguous_duration` | W017 | `UNCLEAR` |
| `salary_refusal` | W018 | `UNCLEAR` |
| `under_15_stopped` | W019 | `STOPPED`, safeguarding flag |
| `ambiguous_duration_am` | W017 | Amharic variant of W017, optional |

The engine is `app/intelligence/engine.py` (`CallProofEngine`), implementing your
Protocol from `app/intelligence/interface.py:12-19`, synchronous as you asked.
Thresholds live in `criteria.py`, the deterministic verdict logic in `rules.py`,
prompts in `prompts.py`, and the provider port in `providers/` with Gemini as the
default and a `RecordedProvider` that replays fixtures offline.

The chain runs end to end with no network and no API key:

```sh
.venv/bin/python -m pytest tests/integration/test_engine_pipeline.py -q
```

fixture transcript → engine → `CompletedCallResult` → `store_completed_result` →
`aggregate_company` / `aggregate_programme`.

## What I need from you

1. Add the startup line from finding 1 — without it no call can be processed.
2. Decide on finding 3: pass `beneficiary`, or tell me you want it handled
   differently and I will adapt the rules side.
3. Confirm W002 carries a material salary contradiction in your seed, so the
   material-contradiction headline reaches 3.

One open item on my side: `ambiguous_duration_am.json` is marked
`needs_native_review: true` and still needs a native Amharic speaker to check the
wording. The six required fixtures are English, so nothing demo-critical depends
on it.
