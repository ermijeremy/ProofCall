# Dashboard → local TeleExpert outbound demo

Work branch: `feat/proofcall-local-outbound-demo`, in its own worktree. The
working incoming TeleExpert demo remains separate and keeps its original route.
This branch does not rebuild, restart or modify the TeleExpert container/APK.

## Run

Use the project's virtual environment. Configure a private `.env` with:

- `TELEEXPERT_BASE_URL=http://127.0.0.1:8080`
- `TELEEXPERT_API_KEY`: the existing local demo **tenant** API key.
- `PROOFCALL_RECRUITMENT_OPERATOR_TOKEN`: a separate 32+ character operator token.
- `CALLPROOF_DATABASE_URL`: a fresh SQLite database for this dashboard.
- `CALLPROOF_SCHEDULER_ENABLED=false`
- `PROOFCALL_RECRUITMENT_WEBHOOK_URL=` (polling is sufficient locally).

Start and prepare (neither command places a call):

```bash
.venv/bin/python scripts/run_recruitment_demo.py
# In another terminal:
.venv/bin/python scripts/prepare_outbound_demo.py
```

Open `http://127.0.0.1:8767/recruitment`, unlock with the operator token,
and select **BeSingularity outbound demo — Henok**. The private token-only file
`.env.operator-token` was created for this local setup; it is excluded from Git.

The startup warning about the legacy local intelligence engine does not apply
to recruitment: TeleExpert provides live conversation and structured analysis.
No Gemini key is needed in ProofCall itself for this workflow.

## Place one controlled test call

1. Keep the gateway phone connected over USB, device control ready and gateway
   armed. The dashboard should say the phone is connected and idle.
2. Select the prepared Henok campaign. Review its prompt, opening and sample
   invitation details. If edited, save and prepare again.
3. Add Henok's **second/test phone number**, using full international format.
   Use a contact source that truthfully describes the arranged demo and check
   the permission box. Do not enter the gateway SIM's own number.
4. Select the contact, choose **Review and place one call**, and confirm.
   The gateway phone now dials the test phone. Answer on the test phone; you no
   longer need to call the gateway from it.
5. Follow progress, cancel if needed, and inspect the result after hangup.
   An incomplete or missing analysis is flagged for review, not treated as
   recruitment success. The integration does not automatically redial.

The phone readiness check verifies tenant gateway status. It does not prove
carrier connectivity, audio routing, provider quota or successful outbound
Telecom ownership. The first operator-confirmed outbound call is still required
for full live validation. The previous incoming demo alone does not prove this.

`outbound-prompt.md` is the outbound adaptation of the tested incoming script.
`outbound-campaign.json` supplies fixed demo settings and language behavior.
The preparation script refreshes the opening from the prompt. It preserves
existing campaign edits on repeated runs; use the dashboard to update them.
The default `Amharic` language setting starts in Amharic and honors an explicitly
chosen language. `Auto` follows detected speech instead.

The incoming demo prompt/binding is never updated by these scripts. If this
outbound workflow fails, stop submitting new calls, cancel any active attempt,
and use the original incoming demo described in `FALLBACK.md`. Do not re-enroll
or reinstall the phone as a response to a dashboard error.

## Voice selection

The Agent voice dropdown uses the Gemini Live voices reported by the connected
TeleExpert server. An empty value uses its default. Selecting a voice changes
campaign configuration, so save and prepare a new assistant revision before
calling. Existing calls retain their selected assistant version and voice.
Older saved campaigns without a voice field keep the server default.

The local TeleExpert deployment now enables Kore, Puck, Aoede, Charon and Fenrir
through `TELEEXPERT_ALLOWED_LIVE_VOICES` in its local Compose override. The four
additional voices each generated audio in a provider preflight. Kore remains
the default. A voice-policy change requires a backend restart; changing an
individual campaign voice only needs saving and preparing that campaign.

## Calling the same demo contact again

A provider-confirmed completed call releases the contact's active-call slot,
even if structured analysis is missing or invalid. Its result remains marked
`needs_review`; completion is not recruitment success. A terminal webhook alone
with missing analysis does not release an uncertain call: authoritative provider
polling must confirm completion. Active and unknown submissions stay blocked.
A new call requires an idle phone, an eligible selected contact and a fresh
operator confirmation; a warning highlights previous unverified analysis.
There is no automatic redial. Existing opt-outs remain enforced.

Voice choices display Google's published synthetic voice labels: Kore and Aoede
are Female; Puck, Charon and Fenrir are Male. Only display labels change; the
provider receives the original voice ID. Reference:
https://firebase.google.com/docs/ai-logic/live-api/configuration

## Recover missing analysis without another call

A completed call can retain its transcript while post-call extraction fails.
TeleExpert's configured analysis provider returned a 503 capacity error during
investigation. A simpler generation schema succeeded in a synthetic test; the
original full schema still validates every result locally. The analyzer now
retries transient request failures once within bounded timeouts.

**Retry analysis (no call)** reprocesses the retained transcript through the
configured external analysis provider. It does not dial, re-transcribe audio,
change the pinned assistant/schema, or fabricate missing answers. Only completed
calls are eligible. A successful result is retained and subsequent retries reuse
it. The provider route is `POST /v1/calls/{call_id}/analysis/retry` with `{}` and
requires tenant ownership plus `calls:write`. Concurrent retries are rejected.
ProofCall exposes an operator-protected equivalent for its saved request ID and
validates the returned analysis against the original assistant/version.

Reprocessing sends the saved conversation to the external analysis provider
again. Obtain explicit approval for that recovery when required. During this
investigation, automatic approval review blocked retrying the real saved call;
that retry was not executed pending the user's approval. Synthetic-only tests
and service checks were completed separately.

## Import contacts from Excel

In step 2, select or save a campaign and download the Excel template. Fill the
first worksheet with `Name` and `Phone number` columns, then save it as `.xlsx`.
Amharic names are supported. Format phone cells as Text to retain `+` and leading
zeros. Ethiopian `09…`, `07…`, nine-digit mobile numbers and `251…` mobile numbers
are normalized to `+251…`; other countries must use `+country-code` format.

Choose the file, enter the actual source of the list and click **Preview
spreadsheet**. Review normalized numbers and row errors. Duplicates and contacts
marked do-not-call are skipped. Correct invalid rows in Excel and preview again,
or import only the valid rows. Confirm permission and click **Import valid
contacts**. Contacts are stored in the campaign; select one to review and place
a call separately. Importing never dials. Reimporting the same numbers skips them.

Limits: 2 MB compressed, 20 MB expanded, 1,000 rows after the header, headers in
the first 30 columns. Only the first worksheet is read. Formulas are rejected in
name/phone cells; use plain values. Older `.xls` and macro workbooks are unsupported.

## Organized local campaigns

The active list contains Henok demo, Bezawit demo and Standard recruitment.
The two demos retain their operator-edited scripts. Standard recruitment uses
`standard-prompt.md` and all eight supported variables, including the selected
contact's name/source and the campaign's venue/date/session values. Review the
sample training settings, save edits and prepare the assistant before calling.
Existing calls retain their original snapshots and pinned assistant versions.

Duplicate campaigns are archived, with contacts and call history retained. Use
**Show archived campaigns** to inspect them; new calls from archived campaigns
are blocked. `scripts/organize_recruitment_campaigns.py` performs the local
cleanup, backs up SQLite in the private ignored `.campaign-backups/` directory
and refuses to proceed while any prior call has a nonterminal status. It never
dials. Do not rerun it after intentionally creating other active campaigns.
