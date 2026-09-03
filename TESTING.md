# How to check and test CallProof

Everything here is runnable from the repository root. The default suite needs no
API key, no network, and no phone.

## Setup, once

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If `.venv/bin/python -m pytest` ever reports `No module named pytest` after this
worked before, the system Python was upgraded underneath the virtualenv. Rebuild
it: `rm -rf .venv && python3 -m venv .venv`, then reinstall.

## 1. The whole suite, offline

```sh
.venv/bin/python -m pytest -q
```

Run the focused Callwise checks below before every commit. Live tests are
excluded automatically by `addopts = -m "not live"` in `pytest.ini`.

The ~1,570 deprecation warnings all come from `datetime.utcnow()` in Member B's
models and `app/main.py`'s `on_event`. None come from the intelligence code.

## 2. One area at a time

```sh
.venv/bin/python -m pytest tests/unit/test_rules.py -q          # thresholds and verdicts
.venv/bin/python -m pytest tests/unit/test_prompts.py -q        # prompt safety invariants
.venv/bin/python -m pytest tests/unit/test_fixtures.py -q       # fixtures match the contract
.venv/bin/python -m pytest tests/unit/test_providers.py -q      # provider port, key rotation
.venv/bin/python -m pytest tests/integration/ -q                # the full chain
```

The Callwise path has its own modules:

```sh
.venv/bin/python -m pytest tests/unit/test_callwise_questionnaire.py -q
.venv/bin/python -m pytest tests/unit/test_categorize.py -q     # pass two: derive, then assign
.venv/bin/python -m pytest tests/unit/test_selection.py -q      # who gets called
.venv/bin/python -m pytest tests/unit/test_sampling.py -q       # the draw itself
.venv/bin/python -m pytest tests/unit/test_analysis.py -q       # counts, and what the model is told
.venv/bin/python -m pytest tests/unit/test_safeguarding.py -q   # age label to exclusion
.venv/bin/python -m pytest tests/integration/test_callwise_pipeline.py -q  # roster to exports
.venv/bin/python -m pytest tests/integration/test_webhook_layer.py -q  # signatures, replay, the drain
.venv/bin/python -m pytest tests/integration/test_batch_pages.py -q    # the two rendered pages
```

Two tests answer "does the whole thing work".
`tests/integration/test_callwise_pipeline.py` covers roster → fixed questionnaire
→ selection → completion → categorization → aggregate → exports, against an
offline provider with no network or telephone.

Useful flags: `-v` for one line per test, `-k salary` to select by name, `-x` to
stop at the first failure, `--tb=short` for shorter tracebacks.

## 3. Against the real Gemini API

This costs money and needs a key, which is why it is opt-in:

```sh
.venv/bin/python -m pytest -m live -v
```

It skips cleanly if no key is configured. What it proves that the offline suite
cannot: the *real* model, reading the real transcripts through the real prompt,
still produces facts the deterministic rules turn into the expected verdict. A
mock only ever confirms our code agrees with itself.

It also probes every configured key against the default model, which is worth
doing before a demo — see the model trap below.

`scripts/live_check.py` is the hand-driven version, and its `--batch` mode is the
fastest way to see whether the categories the model derives are ones a person
would actually count:

```sh
.venv/bin/python scripts/live_check.py --keys      # probe every key, no fixtures
.venv/bin/python scripts/live_check.py --batch --all \
  --questions "1 - what is ur age? 2 - how much do they pay u per month?"
```

That prints one extraction per transcript, the category set derived for the batch
as a whole, and the counts computed from it in Python. A real run over the seven
fixtures produced age bands, pay ranges, `Declined to answer` for the refusal, and
the under-fifteen transcript excluded and counted nowhere.

Put keys in `.env` (gitignored; `.env.example` documents the format):

```sh
GEMINI_API_KEY=["key1","key2","key3"]
```

One key or many. Several are spread round-robin to stretch per-minute quota and
failed over on quota errors.

**The model trap.** Do not set `GEMINI_MODEL=gemini-2.5-flash`. Google gates it to
projects that already used it, and a newer key gets
`404 NOT_FOUND ... no longer available to new users`. The model still appears in
`models.list()` for those keys, so it only fails on a real call. The default is
`gemini-3.6-flash`.

## 4. Run the application

```sh
.venv/bin/python -m uvicorn app.main:app --reload
```

Then visit `http://127.0.0.1:8000/` (the list of companies) and
`http://127.0.0.1:8000/docs`. Clicking a company opens its thread at
`/c/{company_id}`; the "+ Add company" button on the list creates one. The
active Callwise dashboard is also available at `/api/dashboard`.

The schema changed when the thread became company-scoped: `batch_messages.batch_id`
is now nullable, and `create_all` does not alter an existing table. A database file
from before that change raises `NOT NULL constraint failed: batch_messages.batch_id`
on the first message, so move it aside and let the app recreate it.

`app/main.py`'s startup hook calls `init_db()` and `try_register_default_engine()`,
so the engine is registered for you. It uses `try_register_default_engine`, not
`register_default_engine`, because the latter raises when no key is present, which
would stop the app from booting at all. With no key configured, every
call-processing request answers:

```
POST /api/calls/{call_id}/process -> 503
{"detail":"Member A intelligence engine has not been configured"}
```

so a 503 there means the key is missing, not that startup is missing a line.

Check it end to end without a browser:

```sh
.venv/bin/python - <<'EOF'
import asyncio, httpx
from app.main import app
from app.db.session import init_db
from app.intelligence.engine import try_register_default_engine

async def main():
    init_db()
    print("engine registered:", try_register_default_engine() is not None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        print("health   ", (await c.get("/health")).json())
        print("dashboard", (await c.get("/api/dashboard")).status_code)
        r = await c.post("/api/calls/W001/process",
                         json={"transcript": "Interviewer: Hi.\nWorker: Hello."})
        print("process  ", r.status_code, r.text[:120])

asyncio.run(main())
EOF
```

`400 {"detail":"Call not found: W001"}` is the healthy answer on an empty
database — it proves the engine resolved and the request reached the service.
`503` means no key is configured.

## 5. Run a batch in the browser

This is the new path, and it is the demo. Start the app, open
`http://127.0.0.1:8000/api/dashboard`, and press **+ New batch**.

In the thread, in this order:

1. Attach a CSV with `name` and `contact` columns. Optional pilot columns include
   `preferred_language`, `gender`, `age_band`, `training_cohort_id`,
   `training_end_date`, `placement_status_per_besingularity`, `placement_date`,
   and `consent_to_followup_contact`.
2. The fixed 16-question KPI questionnaire is shown automatically. It cannot be
   replaced by administrator-authored questions.
3. Say who to call: `call all of them`, `call half of them at random`,
   `call Abebe and Marta`, or `call the ones we haven't reached yet`. The sample is
   drawn, persisted, and echoed back by name. **No call has been placed yet.**
4. Reply `yes` to dial, or `no` to choose different people.
5. Ask anything once the answers are back. Every figure in the reply is counted in
   Python from stored answers; the model is handed the counts, never the records.

**With `TELEXPERT_BASE_URL` unset, dialing runs in demo mode** — call ids come back
as `demo_call_<hex>` and no telephone rings, which is the safe way to walk the flow
end to end. Point it at `scripts/mock_teleexpert.py` to exercise the webhook path,
and only then at the real service. A placed call cannot be recalled, which is the
whole reason step 4 exists.

The aggregate report and worker-answer exports are available from the Callwise thread.

## 6. Check no secret is about to be committed

Run this before any commit, and especially before opening a PR:

```sh
git ls-files -co --exclude-standard \
  | xargs grep -n "AIzaSy[A-Za-z0-9_-]\{33\}\|AQ\.Ab8[A-Za-z0-9_-]\{20\}" 2>/dev/null \
  | grep -v FAKE \
  && echo "!!! A LIVE KEY IS STAGED !!!" || echo "clean"
```

The `grep -v FAKE` is load-bearing, not laziness. `tests/unit/test_providers.py`
has to hold both key *shapes* to test masking and rotation at all, so its
constants are named `FAKE_KEYS` and every one of them contains `FAKE`
(`tests/unit/test_providers.py:390`). Without that filter the check shouts on
every run, and a check that always shouts is a check nobody reads. Keep the
convention: a key-shaped literal in a tracked file must contain `FAKE`.

Confirm the check still bites, which takes four seconds and is worth doing after
touching the pattern. The probe has to be full length — `AIzaSy` plus at least
33 more characters — because a shorter one passes and looks like proof that
nothing is wrong:

```sh
python3 -c "print('AIzaSy' + 'PR0BE' * 7)" > ./probe-key-check.txt
# ... run the check above: it must print the !!! line ...
rm ./probe-key-check.txt
```

The probe is built by concatenation rather than written out, so that this page
does not itself contain a key-shaped literal and trip the check it documents.

Also confirm the key file is still ignored:

```sh
git check-ignore -v .env      # must print a .gitignore line
```

`.env` and `.env.*` are ignored; `.env.example` is deliberately not.

## 7. Read a fixture yourself

The fixtures are the specification in executable form. `tests/fixtures/README.md`
explains the format and the conventions. Two that surprise people:

The three labour-rights clauses store *whether a violation is present*, so
`forced_labour: {"value": false, "status": "MET"}` means no forced labour.
And `working_hours` is a weekly total the code derives by multiplying days per
week by hours per day — the respondent is never asked for a weekly figure.

## What a green run does and does not prove

A green default suite proves the deterministic logic is correct and the chain
holds together, because it replays recorded extractions and fakes every provider
call. It says nothing about whether today's model still extracts correctly, or
whether the categories it derives are sensible — that is what `-m live` and
`scripts/live_check.py --batch` are for.

Neither suite has placed a real phone call. TeleExpert integration is Member B's
side, and W020 is reserved for the one live call in the demo.
