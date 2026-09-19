# Twilio setup

Nothing here is needed to run the tests, the dashboard, or the scripted demo (`POST /api/dev/demo/a`).
It is only for taking a **real phone call** (PDF §3.1).

## Call path

```
caller -> Twilio number -> webhook (POST /incoming-call)  or  TwiML Bin
       -> <Connect><Stream url="wss://HOST/media-stream-realtime"> (8 kHz mu-law, bidirectional)
       -> app/api/v1/endpoints/openai_realtime_emergency.py  <->  OpenAI Realtime
```

## Steps

1. Expose the app: `ngrok http 8000` (or any tunnel). Note the host, e.g. `abcd-12-34.ngrok-free.app`.
2. `.env`: set `PUBLIC_HOST=abcd-12-34.ngrok-free.app`, `OPENAI_API_KEY`, `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`. Start the app: `python main.py`.
3. Twilio Console -> Phone Numbers -> your number -> **Voice Configuration**:
   - *A call comes in*: **Webhook**, `POST`, `https://abcd-12-34.ngrok-free.app/incoming-call`
     — **or** a TwiML Bin built from `incoming_call_twiml_bin.xml` (no server route needed).
   - *Call status changes* (optional): `POST https://<host>/call-status` — clears the dashboard's
     "caller on the line" marker if a call drops without a clean stop.
4. Call the number. Watch `http://localhost:8000/dashboard/`.

## Live-call transfer to a department (escalation ladder)

`transfer_to_human_operator(department=...)` hands the caller to that department's on-duty contacts. No Studio Flow
is needed — Twilio just calls these routes (all validated with `X-Twilio-Signature` when
`TWILIO_VALIDATE_SIGNATURE=true`; `PUBLIC_HOST` must be your public host):

| Route | Twilio calls it when | Does |
|---|---|---|
| `POST /twilio/escalation/dial?handoff_id&step` | our REST redirect / previous rung failed | SMS the contact, then `<Dial timeout answerOnBridge>` + `<Number url=whisper statusCallback=leg-status>` |
| `POST /twilio/escalation/result?...` | `<Dial action>` returns | answered -> finish; else `<Redirect>` to next rung; last rung -> apology + hangup |
| `POST /twilio/escalation/whisper?...` | the contact picks up, before the bridge | `<Say>` the incident context to them |
| `POST /twilio/escalation/leg-status?...` | each leg event (ringing/answered/...) | appended to `escalation_attempts.raw_status_events` |

Ladder = each department's on-call contacts by priority, repeated twice (30 s each), then the director. Contacts live in
`department_contacts` (E.164, optional duty window). Seeded numbers are synthetic; set `ESCALATION_DEMO_NUMBER` to
have every rung ring your own phone for a real end-to-end test.

## Environment variables (the "Twilio variables")

| Variable | Purpose |
|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | REST client: hang up (`end_call`), redirect to the human line, optional SMS |
| `TWILIO_PHONE_NUMBER` | Sender for crew SMS (only if `SMS_ENABLED=true`) |
| `PUBLIC_HOST` | Host used in the `<Stream>` URL and for webhook-signature validation |
| `TWILIO_VALIDATE_SIGNATURE` | `true` = reject webhooks Twilio did not sign (turn on whenever the URL is public) |
| `HUMAN_OPERATOR_NUMBER` | E.164 number `transfer_to_human_operator` dials; empty = simulated hang-up |
| `SMS_ENABLED` | `true` = actually text crews on dispatch; default only records the alert |

Locally these live in `.env` (git-ignored). With `IS_LOCAL=false` they are read from Azure Key Vault by the
same naming rule as ai-callcenter (`TWILIO_AUTH_TOKEN` -> `TWILIOAUTHTOKEN`).

## Things that will bite you

- **Never put credentials in a Studio Flow / Function / TwiML Bin definition.** Use Function environment
  variables. (ai-callcenter's flow JSONs embed a service-account login; do not copy that pattern.)
- **Trial accounts** only call/text *verified* numbers and play a trial message first.
- **Geo permissions:** outbound SMS/voice to some countries (e.g. India) is blocked until enabled under
  Console -> Voice/Messaging -> Geo permissions.
- `transfer_to_human_operator` redirects the live call with inline TwiML (`<Dial>`); the number is validated
  as E.164 before it is placed in TwiML.
- The media stream needs a **public wss://** URL; `localhost` will not work without a tunnel.
