# CallProof modular architecture

This repository follows the responsibility boundary in `final_project_spec.md`.

## Member A: intelligence

`app/intelligence/` owns prompt generation, transcript extraction, clause
evaluation, confidence, hard cases, and employer comparison. The implementation
must satisfy `IntelligenceEngine`.

## Member B: operations

Member B owns the HTTP API, persistence, imports, sampling, campaign lifecycle,
TeleExpert transport, call status synchronization, completion processing,
aggregation, and dashboard data/presentation.

## Shared boundary

`app/contracts/integration.py` is the frozen handoff. A completed call is stored
and passed using `CompletedCallResult`; Member B must not infer a good-job verdict
from raw transcript text.

## Planned flow

```text
API routes
  -> application services
      -> repositories / integrations
          -> Member A intelligence contract
              -> evidence persisted
                  -> aggregation and dashboard
```

The four dashboard areas map to the presentation layer: programme overview,
company detail, worker evidence, and live calls.

## BeSingularity recruitment demo

`/recruitment` is the single-contact recruitment console for issue #23. Its
campaign, contact, call-intent and event-inbox tables are independent of the
worker-verification questionnaire and evidence tables.

The operator edits a campaign prompt and invitation details, prepares a versioned
tenant assistant in TeleExpert, adds a permitted contact, and confirms one real
call. `recruitment_service` snapshots the configuration and exact submission
before dispatch. TeleExpert's `feat/vapi-parity-control-plane` API owns the live
conversation and post-call `result.analysis`; ProofCall validates and displays
that analysis without another model call or good-job scoring.

Each intended call has a stable request UUID and an outstanding-contact database
claim. Ambiguous acceptance keeps that claim and can only be reconciled with the
same request body/key, credentials and provider context within the reported
idempotency window. No automatic redial or callback scheduling is enabled.

Signed callbacks at `/api/recruitment/webhook` enter a durable, bounded inbox.
Received events are reconciled even if delivery precedes local call association;
processed payloads are scrubbed. Manual polling, console polling and the existing
scheduler can recover results. Missing or invalid analysis is review work and
blocks another call until a valid result is recovered. Caller stop/suppression
signals remain separate from technical call completion.

The console's data/actions require a separate operator token; its public HTML
shell contains no contact data. This does not retrofit authentication onto the
legacy routes tracked in #6. Shared deployments must protect those routes too.

