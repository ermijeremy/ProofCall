# Company workspaces

Run the existing local dashboard with:

```bash
.venv/bin/python scripts/run_recruitment_demo.py
```

Open `http://127.0.0.1:8767/` for administration. The administrator still uses the
private operator token in `.env.operator-token`. That token is never issued to a
company. The home page has no company data until administrator access succeeds.

## Onboard a company

1. Open administration. Enter a company name and a unique initial username.
2. Copy the returned workspace link, username and temporary password before
   hiding the credentials. Give them directly to that company's operator.
3. For an existing company, use **Create initial login** beside its name.
4. The operator opens the link or `/login`, signs in and changes the temporary
   password. A new password must contain at least 12 characters. Campaign access
   is blocked until that change succeeds.
5. Subsequent logins use the chosen password. **Change password** is available
   inside the company workspace. **Reset company password** in administration
   issues a fresh temporary password and invalidates existing sessions.

Passwords are stored as salted scrypt hashes, never recoverable plaintext.
Server-side sessions last eight hours; the database stores hashes of session
identifiers. Cookies are HttpOnly/SameSite=Strict and Secure on HTTPS. Mutating
company requests require a session-bound CSRF token and reject foreign origins.
Login attempts are limited by username and client IP in persistent storage.

Company pages are public shells, with no private bootstrap data. Company APIs
require a signed-in user for the company in the path; every campaign/call ID is
checked against that owner. Company sessions cannot access the global recruitment
API or the administrator APIs. Historical Callwise/KPI APIs are administrator-only.
Opt-outs are scoped to a company across its campaigns.

## Configure calls

Each company can create campaigns with its own company name, greeting, language
behavior, voice, prompt and questions. Select **Company questionnaire**, enter one
question per line (up to 20), save and prepare. No fixed employment/KPI questions
are added. The assistant and analysis schema are versioned together. Each call
keeps its original question list and prompt version even if the campaign changes.
Results contain an answer, short supporting quote and state (stated, unclear,
refused, not asked) for every question. Technical completion is separate from
questionnaire completion. Recruitment campaigns retain their recruitment results.

Excel import, contact permission, per-call confirmation, no automatic redial,
archived campaigns and call history work within each company workspace.

## Existing BeSingularity data

The local onboarding helper creates the company/login and attaches the existing
BeSingularity campaigns without changing prompts, pinned assistants or results:

```bash
.venv/bin/python scripts/provision_company_login.py \
  --company-name BeSingularity --username besingularity \
  --assign-besingularity-campaigns
```

It refuses migration with nonterminal calls, backs up SQLite first, preserves
existing passwords on repeat runs, and writes initial credentials into ignored
`.company-credentials/besingularity.json` with owner-only permissions. It prints
the file location and workspace URL, never the password. BeSingularity retains
its Henok demo, Bezawit demo and parameterized standard recruitment campaign;
redundant campaigns remain archived.

## Deployment boundary

The local server binds to 127.0.0.1. The URL is not reachable by another company
until the application is hosted. Use an HTTPS public domain before handing out
remote login access; set the reverse proxy/forwarded-header configuration so the
application recognizes HTTPS. Keep the database and credentials private and back
up persistent storage. This implementation has one initial operator per company;
email-based password recovery and company-managed team accounts are not included.
TeleExpert currently uses the server's shared provider/gateway configuration;
company login isolation does not provision a dedicated phone or provider tenant.

## Validation

The affected recruitment/company API, page and health tests pass (65 tests),
including first-login enforcement, changed-password persistence, session expiry
and revocation, CSRF/origin checks, cross-company ID access, Excel import and
questionnaire answer validation. The JavaScript suite passes for scoped requests,
question editing, evidence display and import behavior. Local HTTP smoke checks
verified the actual temporary login and first-password-change gate without
changing the recipient's password or placing a call.

The full offline suite reported 549 passed, 38 failed and 1 skipped (18 live tests
deselected). The same 38 failures were reproduced on a clean HEAD snapshot; they
are in the pre-existing batch/selection/provider contracts and a missing legacy
route assertion. They are not claimed as passing by this change.
