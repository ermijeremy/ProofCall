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

