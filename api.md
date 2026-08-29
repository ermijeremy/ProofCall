# TeleExpert HTTP API

This document is the integration reference for submitting one outbound AI interview call,
tracking it asynchronously, cancelling it, and receiving the final transcript and/or audio.

The API is intended to be called from another service's backend. Do not place the API bearer
token or webhook secret in browser, mobile, or other public client code.

## Integration flow

1. The operator gives the integrating service an HTTPS base URL and bearer token.
2. Optionally configure the interviewer's personality once with `PUT /v1/personality`.
3. Submit one phone number with `POST /v1/calls`.
4. Store the returned call `id`. A `202 Accepted` response means the job was queued; it does not
   mean the recipient answered.
5. Receive the signed terminal webhook. Poll `GET /v1/calls/{call_id}` only as a fallback.
6. For a completed call, read the inline transcript and/or download the result URLs.
7. Use `DELETE /v1/calls/{call_id}` if the originating service wants to stop a pending or active
   call.

TeleExpert has a single-SIM phone gateway, so calls run one at a time. Multiple accepted calls
wait in the server queue.

## Base URL and authentication

Examples use these environment variables:

```bash
export TELEEXPERT_BASE_URL='https://teleexpert.example.com'
export TELEEXPERT_API_KEY='the-token-issued-by-the-operator'
```

All `/v1` endpoints use a bearer token:

```http
Authorization: Bearer <token>
```

`GET /healthz` is intentionally unauthenticated. An external deployment must set
`TELEEXPERT_API_KEY` and put the API behind HTTPS. The built-in server speaks plain HTTP; TLS is
normally terminated by a reverse proxy. Plain HTTP is acceptable only on a trusted development
LAN.

Request and response JSON is UTF-8. JSON request bodies must not exceed 256 KiB.

## Quick start

Check whether the API is running and whether the Android phone gateway is connected:

```bash
curl -sS "$TELEEXPERT_BASE_URL/healthz"
```

```json
{
  "ok": true,
  "gateway_connected": true
}
```

Submit one call and ask for both result formats:

```bash
curl -sS -X POST "$TELEEXPERT_BASE_URL/v1/calls" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "phone_number": "+251911111111",
    "prompt": "Interview the recipient about clinic appointment satisfaction. Ask one question at a time.",
    "response_format": "both",
    "retries": 2,
    "retry_delay_seconds": 30,
    "answer_timeout_seconds": 60,
    "webhook": {
      "url": "https://client.example.com/webhooks/teleexpert",
      "secret": "replace-with-a-random-secret-at-least-32-characters"
    }
  }'
```

The API responds immediately after validating the request and webhook destination:

```http
HTTP/1.1 202 Accepted
Content-Type: application/json; charset=utf-8
```

```json
{
  "id": "627d1d85029345a4a9c767a896231e7d",
  "batch_id": "",
  "phone_number": "*********1111",
  "status": "queued",
  "created_at": "2026-08-29T12:25:00.000000+00:00",
  "updated_at": "2026-08-29T12:25:00.000000+00:00",
  "error": "",
  "response_format": "both",
  "attempts": 0,
  "max_attempts": 3,
  "retry_delay_seconds": 30.0,
  "answer_timeout_seconds": 60.0,
  "result": {},
  "status_url": "/v1/calls/627d1d85029345a4a9c767a896231e7d",
  "webhook_configured": true
}
```

`status_url` and all result URLs are relative to `TELEEXPERT_BASE_URL`.

## Endpoint reference

### `GET /healthz`

Returns service and phone-gateway health. No authentication is required.

```json
{
  "ok": true,
  "gateway_connected": true
}
```

`ok` means the HTTP service is running. `gateway_connected` means an Android gateway currently
has a WebSocket connection to the backend. Health can change immediately after the response, so
call submission remains asynchronous.

### `GET /v1/personality`

Returns the global personality used as the starting point for newly submitted calls.

```bash
curl -sS "$TELEEXPERT_BASE_URL/v1/personality" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY"
```

```json
{
  "instructions": "Be warm, concise, neutral, and respectful.",
  "greeting": "Introduce TeleExpert as an AI interviewer and ask whether now is a good time."
}
```

### `PUT /v1/personality`

Replaces the global personality for future calls. A call snapshots the personality when it is
submitted, so changing it does not alter already queued or active calls.

```bash
curl -sS -X PUT "$TELEEXPERT_BASE_URL/v1/personality" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "instructions": "You are a patient, neutral research interviewer. Keep questions short.",
    "greeting": "Disclose that you are TeleExpert, an AI interviewer, and ask whether now is a good time."
  }'
```

| Field | Type | Required | Constraint |
|---|---|---:|---|
| `instructions` | string | yes | 1-4,000 characters after trimming |
| `greeting` | string | yes | 1-500 characters after trimming |

The current Gemini Live policy speaks the first turn in Amharic, then detects and exclusively
uses the recipient's language. A configured greeting supplies its intent; it cannot override the
mandatory Amharic-first, one-language-per-response policy.

### `POST /v1/calls`

Creates one asynchronous outbound call. Submit one phone number per request so the integrating
service controls pacing and cancellation.

| Field | Type | Required | Default | Constraint |
|---|---|---:|---|---|
| `phone_number` | string | yes | - | 7-15 digits after removing spaces, parentheses, and hyphens; optional leading `+` |
| `prompt` | string | yes | - | Interview brief, 1-8,000 characters after trimming |
| `response_format` | string | no | `text` | `text`, `audio`, or `both` |
| `retries` | integer | no | `0` | 0-5 additional attempts |
| `retry_delay_seconds` | number | no | `15` | 0-3,600 seconds |
| `answer_timeout_seconds` | number | no | `60` | 10-180 seconds |
| `webhook` | object | no | none | Signed terminal-event callback; see [Webhooks](#webhooks) |

The phone number is normalized before dialing and masked in every API response. Use an
international E.164-style number such as `+251911111111` when possible.

`retries` is the number of additional attempts, so `retries: 2` produces at most three total
attempts. Retries apply when Android does not begin dialing in time or when the recipient does
not answer before `answer_timeout_seconds`. A connected call is not retried after it ends.

The response is `202 Accepted` and contains the call object plus:

- `status_url`: relative URL for the call.
- `webhook_configured`: whether a webhook was accepted for this call.

There is currently no submission idempotency key. Retrying a `POST /v1/calls` request can create
a second real phone call. Store a successful `202` response and do not blindly retry a submission
whose outcome is unknown.

### `GET /v1/calls/{call_id}`

Returns the current call object. This is the polling fallback when a webhook is delayed or cannot
be delivered.

```bash
CALL_ID='627d1d85029345a4a9c767a896231e7d'
curl -sS "$TELEEXPERT_BASE_URL/v1/calls/$CALL_ID" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY"
```

#### Call statuses

| Status | Meaning |
|---|---|
| `queued` | Accepted and waiting for the single-SIM worker |
| `dispatching` | The backend sent the dial command to the Android gateway |
| `dialing` | Android Telecom confirmed a real outgoing-call state |
| `retry_wait` | Waiting before another dialing attempt |
| `in_progress` | The carrier call connected and the interview is active |
| `completed` | A connected call ended and requested results were finalized |
| `failed` | The call could not complete; inspect `error` |
| `cancelled` | The originating service cancelled the call |

`completed`, `failed`, and `cancelled` are terminal. `attempts` counts attempts already started;
`max_attempts` equals `retries + 1`.

### `DELETE /v1/calls/{call_id}`

Cancels any nonterminal call, including a queued, dispatching, dialing, retrying, or active call.
Cancelling the active call instructs the Android gateway to hang up.

```bash
curl -sS -X DELETE "$TELEEXPERT_BASE_URL/v1/calls/$CALL_ID" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY"
```

The response is the updated call object. Cancelling an already terminal call returns that
unchanged terminal object. An unknown ID returns `404`.

### `GET /v1/calls/{call_id}/transcript`

Available only after a `completed` call requested `response_format: "text"` or `"both"`.

```bash
curl -sS "$TELEEXPERT_BASE_URL/v1/calls/$CALL_ID/transcript" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY" \
  -o transcript.json
```

```json
{
  "turns": [
    {
      "role": "assistant",
      "text": "ሰላም፣ እኔ TeleExpert የተባልኩ የAI ቃለ መጠይቅ አድራጊ ነኝ።",
      "offset_seconds": 0.0
    },
    {
      "role": "caller",
      "text": "እሺ፣ መነጋገር እችላለሁ።",
      "offset_seconds": 4.281
    }
  ]
}
```

`role` is `assistant` or `caller`. `offset_seconds` is measured from the connected-call result
timeline. Transcript text is emitted as readable UTF-8 and preserves the model's detected
language and writing system. Speech transcription is probabilistic and may contain mistakes.

For `text` and `both`, the same transcript is also included inline at
`result.transcript` in a completed call object and webhook payload.

### `GET /v1/calls/{call_id}/audio`

Available only after a `completed` call requested `response_format: "audio"` or `"both"`.

```bash
curl -sS "$TELEEXPERT_BASE_URL/v1/calls/$CALL_ID/audio" \
  -H "Authorization: Bearer $TELEEXPERT_API_KEY" \
  -o conversation.wav
```

The response is a downloadable 16 kHz, mono, 16-bit PCM WAV that mixes the recipient and
TeleExpert on the connected-call timeline. Audio bytes are not embedded in JSON or webhooks.

Requesting a result before completion, requesting a format that was not selected, or using an
unknown call ID returns `404 result is not available`.

## Webhooks

Add a callback configuration to `POST /v1/calls`:

```json
{
  "webhook": {
    "url": "https://client.example.com/webhooks/teleexpert",
    "secret": "a-unique-random-secret-with-at-least-32-characters"
  }
}
```

| Field | Constraint |
|---|---|
| `url` | Absolute HTTPS URL, at most 2,048 characters, with no embedded credentials or fragment |
| `secret` | 32-256 UTF-8 bytes |

The hostname must resolve within three seconds during call submission. By default, every
resolved address must be publicly routable; loopback, private, link-local, reserved, and cloud
metadata destinations are blocked. Redirects are not followed and normal TLS certificate
verification applies. For trusted LAN development only, the TeleExpert operator can set
`TELEEXPERT_WEBHOOK_ALLOW_PRIVATE=1`; HTTPS is still required.

TeleExpert attempts exactly one kind of terminal event for a configured call:

- `call.completed`
- `call.failed`
- `call.cancelled`

Intermediate states do not produce webhook events.

### Webhook request

```http
POST /webhooks/teleexpert HTTP/1.1
Content-Type: application/json
User-Agent: TeleExpert-Webhook/1.0
Webhook-Id: 091cddc28ba74ea5a38d0ba9fb3b4d21
Webhook-Timestamp: 1788006645
Webhook-Signature: v1,<base64-hmac>
```

```json
{
  "id": "091cddc28ba74ea5a38d0ba9fb3b4d21",
  "type": "call.completed",
  "created_at": "2026-08-29T12:30:45.123456+00:00",
  "data": {
    "call": {
      "id": "627d1d85029345a4a9c767a896231e7d",
      "batch_id": "",
      "phone_number": "*********1111",
      "status": "completed",
      "created_at": "2026-08-29T12:25:00.000000+00:00",
      "updated_at": "2026-08-29T12:30:45.123456+00:00",
      "error": "",
      "response_format": "both",
      "attempts": 1,
      "max_attempts": 3,
      "retry_delay_seconds": 30.0,
      "answer_timeout_seconds": 60.0,
      "result": {
        "transcript_url": "/v1/calls/627d1d85029345a4a9c767a896231e7d/transcript",
        "audio_url": "/v1/calls/627d1d85029345a4a9c767a896231e7d/audio",
        "transcript": {
          "turns": []
        }
      }
    }
  }
}
```

The callback URL and secret are never returned in API status objects, webhook bodies, or logs.
Result URLs are relative to the TeleExpert API origin and still require the API bearer token.

### Verify a webhook signature

The signature is `v1,` followed by standard Base64-encoded HMAC-SHA256. The signed bytes are:

```text
<Webhook-Id>.<Webhook-Timestamp>.<exact raw HTTP request body>
```

Verify the exact raw body before decoding or re-encoding JSON:

```python
import base64
import hashlib
import hmac
import time


def verify_teleexpert_webhook(headers, raw_body: bytes, secret: str) -> bool:
    event_id = headers["Webhook-Id"]
    timestamp_text = headers["Webhook-Timestamp"]
    supplied = headers["Webhook-Signature"]

    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return False

    if abs(int(time.time()) - timestamp) > 300:
        return False

    signed = (
        event_id.encode("utf-8")
        + b"."
        + timestamp_text.encode("ascii")
        + b"."
        + raw_body
    )
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    expected = "v1," + base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, supplied)
```

After signature verification, the receiver must:

1. Confirm the JSON body's `id` equals `Webhook-Id`.
2. Reject timestamps outside a five-minute window.
3. Store processed `Webhook-Id` values and ignore duplicates.
4. Commit or durably enqueue the event before returning a fast `2xx` response.

### Delivery retries

TeleExpert makes at most five attempts: immediately, then after delays of 5, 30, 120, and 600
seconds. It retries DNS, connection, TLS, network, and ten-second timeout failures, any `5xx`, and
HTTP `408`, `409`, `425`, or `429`. Any `2xx` succeeds. Other responses, including `3xx` and other
`4xx` statuses, permanently stop delivery.

Retries use the same event ID and exact JSON body. Each attempt receives a fresh timestamp and
signature, so deduplicate using `Webhook-Id`, not the signature.

## Errors

Errors currently use UTF-8 plain text rather than a JSON error envelope.

| HTTP status | Meaning |
|---|---|
| `200` | Read, update, cancel, or result download succeeded |
| `202` | A valid call was accepted asynchronously |
| `400` | Invalid request field or webhook configuration |
| `401` | Missing or incorrect bearer token |
| `404` | Unknown call or unavailable/unrequested result |
| `413` | Request body exceeds 256 KiB |

Typical error bodies include `Bearer token required`, `call not found`, and
`result is not available`.

## Operational limitations

- Calls, queue state, webhook registrations, and pending webhook retries are currently held in
  memory. Restarting the backend can lose unfinished jobs and callbacks.
- Result files are stored on the TeleExpert host under `TELEEXPERT_RESULTS_DIR` (default
  `interview-results`). The deployment operator is responsible for retention and protected
  backups.
- Only one global personality and one shared API bearer token currently exist. This deployment is
  suitable for one trusted integration, not mutually isolated third-party tenants.
- There is no call-list endpoint, submission idempotency key, webhook delivery history, or manual
  webhook replay endpoint yet.
- The phone gateway and carrier determine caller ID: outbound calls originate from the SIM in the
  configured Android phone.
- Only call recipients who consent, disclose that an AI is speaking, honor stop and do-not-call
  requests, and follow applicable calling and recording laws.
