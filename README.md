# AI Voice-First Emergency Response & Resource Coordination Platform

> "911, but the operator is an AI agent that can see the map, knows every resource, and never sleeps."

A voice-native emergency dispatcher: a caller phones in, a **realtime AI agent** (Twilio Media Stream ⇄ OpenAI
Realtime, function-calling) understands them mid-sentence, geolocates the incident, cross-checks it against every
other live report, ranks the nearest *capable* resource, dispatches it, escalates automatically, and keeps the
caller safe — while an ops dashboard shows the whole thing live. Built for PS-9 (extended) from the requirements PDF.

The architecture, stack and call plumbing are adapted from the SculptSoft `ai-callcenter` (Baton Rouge 311 "Grace")
project: same FastAPI + Twilio + OpenAI Realtime + SQLAlchemy/Alembic stack, same config/secret conventions.

**Status:** everything that can run without paid services is tested (52 offline tests, incl. the realtime handler
against a fake OpenAI server, the Twilio escalation TwiML flow, and mocked external map services). It has **not** been run against a live OpenAI/Twilio call — see *Known limits*.

---

## Run it (no Twilio, no OpenAI, no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # defaults are fine: SQLite + offline geocoder
ENABLE_DEV_ENDPOINTS=true python main.py       # http://localhost:8000/dashboard/
# in another shell — replay a PDF scenario through the REAL tool executor (dashboard animates):
curl -X POST localhost:8000/api/dev/demo/a     # a = road accident + duplicate merge, b = gas leak,
                                               # c = flood cluster,  d = prank -> human review,
                                               # e = factory fire -> nearest fire station -> Twilio transfer
python -m pytest tests -q                      # 52 tests, offline
python scripts/generate_schema_sql.py          # regenerate db/schema.sql (PostgreSQL + PostGIS DDL)
python scripts/test_emergency_instructions.py  # prompt/tool-schema regression + token budget
```

On first start (SQLite) tables are created and ~30 **synthetic** units + 12 facilities are seeded around Ahmedabad.

### Real phone call
See [`twilio/README.md`](twilio/README.md). Needs `OPENAI_API_KEY`, Twilio creds and a public tunnel. Cached
greeting/goodbye clips are generated once via OpenAI TTS at first startup (no ffmpeg needed).

### Postgres + PostGIS
`docker compose up -d db`, set `DATABASE_URL=postgresql+psycopg2://emergency:emergency@localhost:5432/emergency`,
then `python scripts/init_db.py` (or `alembic upgrade head`). Nearest-unit search then uses `ST_DWithin`.

---

## How a call flows

```
caller ─PSTN─▶ Twilio ─<Connect><Stream>─▶ openai_realtime_emergency.py ◀─▶ OpenAI Realtime (audio in/out)
                                                   │ tool calls (in a worker thread)
                                                   ▼
                                       app/services/tool_executor.py   ── hard gates + audit trail
                        ┌──────────┬───────────┬────────────┬─────────────┬───────────────┐
                    geo_service  severity  incident_svc  resource_svc  escalation/dispatch/hospital
                    (gazetteer/   (rules)   (dedupe/     (PostGIS or    (dept alerts, SMS,
                     Nominatim)              merge)       haversine)     hospital capacity)
                                                   │ events (WebSocket)
                                                   ▼
                              /dashboard/  (Leaflet: pins, live-call dot, radius search, audit trail)
```

The 13 tools are the PDF §4 contract (+ `find_nearest_department_center`): `geocode_location`, `create_incident`, `check_duplicate_incident`,
`estimate_severity`, `find_nearest_resource`, `get_hospital_capacity`, `assign_resource`, `notify_dispatch_team`,
`escalate_incident`, `transfer_to_human_operator`, `give_caller_safety_instructions`,
`find_nearest_department_center`, `end_call`.

**Design points that make it safe, not just impressive**

| Concern | What the code does |
|---|---|
| LLM invents a severity/category | Category is an enum; severity is computed by **rules** from a closed set of *stated facts* (`severity.py`). The model may nudge the score by at most +15. Accent/tone/language are never inputs. |
| Dispatch to the wrong place | `assign_resource` is **code-gated**: severity must be estimated, the duplicate check must have run, and the location must be caller-confirmed — unless severity ≥ 85 (critical: "send help now, refine en route"). Gates reject *once* with instructions, then let through, so a stubborn model can never hang a call (ai-callcenter's pattern). |
| Prank / unclear caller | Low-confidence incidents are parked in a **human review queue**; `assign_resource` hard-refuses them. Abrupt hang-up after a vague report with nothing dispatched is parked too. |
| Two agents, one ambulance | Atomic claim: `SELECT … FOR UPDATE SKIP LOCKED` on Postgres (tested: exactly one winner). |
| "Closest wins" | Weighted score: proximity + capability + availability + travel time − queue load (PDF §5.3). Every unit considered is returned with a reason for rejection. |
| AI dispatching unsupervised | `DISPATCH_MODE=approval` reserves the unit and waits for a human to approve on the dashboard; the tool then tells the agent *not* to promise arrival. `autonomous` is the demo default. |
| Interruptions | Semantic VAD + barge-in: on `speech_started` the handler clears Twilio's audio buffer, cancels the response and sends `conversation.item.truncate` at the milliseconds the caller **actually heard**, so the model's memory matches reality. The lock that protects speech is only around `end_call` / transfer. |
| Human transfer | `transfer_to_human_operator(department)` -> Twilio **escalation ladder**: SMS then ring each on-duty contact (30 s), repeat, then the director; whoever answers hears a whisper of the incident; every attempt is a row in `escalation_attempts`. Falls back to a clean hang-up with no Twilio. |
| Late response | Watchdog escalates `response_delay` when a unit misses ETA + buffer. |
| Lost call record | Post-call persistence runs as a shielded task (found and fixed by the handler test — see CLAUDE.md). |

---

## Instructions: one file per department, swapped in live

```
emergency_instructions/
  global_rules.py       Rules 01-18 — sent on every update
  dispatcher.py         the single agent persona, state machine, tool map
  fire_dept.py  hazmat.py  police.py  traffic_police.py  ems.py  disaster_management.py
  municipal_corp.py  utility_board.py  pollution_control.py  forest_dept.py  supervisor.py
  question_sets/incident_taxonomy.json   categories -> departments, resources, triage questions, safety protocols
```

Each department file is reference-only, like ai-callcenter's per-service files: what it handles, how to tell it from its
neighbours, priority cues, cautions, hand-off. `incident_taxonomy.json` decides *which* departments own a category
(lead first); escalations add more.

**`session.update`, exactly** (`app/utils/realtime_session.py`, used by the phone handler *and* the text harness):

| When | Payload | Why |
|---|---|---|
| Session start (once) | full session: `semantic_vad` + `interrupt_response`, `audio/pcmu` both ways, noise reduction, transcription, voice, **all 13 tools** | the only time `tools` is sent |
| `create_incident` | `instructions` only = global + dispatcher + **ACTIVE DEPARTMENT(S)** + **ACTIVE INCIDENT** | tools/audio untouched (partial merge), history kept — caller never notices |
| duplicate merged into another incident | instructions only, for the *primary* incident's category | the call now follows a different incident |
| escalation (agent or auto at severity >= 85) | instructions only, escalated departments added (cap 4, supervisor dropped first) | responsible departments changed |

`PromptState` returns a new prompt **only if the loaded category+departments really changed**, and the handler sends it
*before* the follow-up `response.create`, so the model's very next words already use the new context. Tested in
`tests/test_session_updates.py` and end-to-end in `tests/test_realtime_handler.py` (exactly two instruction-only
updates for a create + critical-escalation call; none for a repeat).

## External services ("nearest centre of any department")

`find_nearest_department_center(department)` asks a real map service — not a hard-coded list:

| Need | Provider (setting) | Call |
|---|---|---|
| Nearest fire station / police station / hospital / municipal / disaster / utility / forest office | **Overpass (OpenStreetMap)** default, or **Google Places (New)** (`FACILITY_PROVIDER`) | `POST overpass-api.de/api/interpreter` (QL `around:` query) / `POST places.googleapis.com/v1/places:searchNearby` |
| Road distance + ETA to that centre, and to dispatched units | **OSRM** or **Google Routes** (`ROUTING_PROVIDER`) | `GET /route/v1/driving/{lng,lat};{lng,lat}` / `POST routes.googleapis.com/directions/v2:computeRoutes` |

Results are cached in `facility_lookups` (24 h), upserted into `facilities` (with OSM/Google place id), and any
failure falls back to the seeded facilities / straight-line ETA — a dispatch never waits on, or fails because of,
a third-party API. The first live lookup takes ~3 s (public Overpass); repeats are instant from the cache.
Tested against mocked HTTP (request shape, parsing, cache, fallback) **and** run once against the live APIs.

## Database schema

17 tables, defined once in `app/models/` and compiled to PostgreSQL DDL in [`db/schema.sql`](db/schema.sql)
(`python scripts/generate_schema_sql.py`; a test fails if it drifts): JSONB, native UUIDs, CHECK constraints on
every status/enum, FKs with `ON DELETE`, composite indexes, PostGIS GiST expression indexes. No local database is
needed to read it.

```mermaid
erDiagram
    calls ||--o{ call_transcripts : has
    calls ||--o{ agent_tool_calls : "audit trail"
    calls }o--o| incidents : reports
    incidents ||--o{ incident_reports : "merged duplicates"
    incidents ||--o{ incident_events : timeline
    incidents ||--o{ assignments : dispatches
    incidents ||--o{ escalations : escalates
    incidents ||--o{ dispatch_notifications : alerts
    resources ||--o{ assignments : "assigned to"
    resources ||--o{ resource_location_pings : "GPS history"
    departments ||--o{ department_contacts : "on-call ladder"
    departments ||--o{ call_handoffs : "transfer target"
    calls ||--o{ call_handoffs : "live transfer"
    call_handoffs ||--o{ escalation_attempts : "one row per dial"
    department_contacts ||--o{ escalation_attempts : rung
    facilities }o--o{ facility_lookups : "cached from Overpass/Google"
```

| Group | Tables |
|---|---|
| Calls & audit | `calls`, `call_transcripts`, `agent_tool_calls` |
| Incidents | `incidents`, `incident_reports`, `incident_events`, `escalations` |
| Fleet & places | `resources`, `resource_location_pings`, `facilities`, `facility_lookups` |
| Dispatch | `assignments`, `dispatch_notifications` |
| Departments & Twilio transfer | `departments`, `department_contacts`, `call_handoffs`, `escalation_attempts` |

## What was copied from ai-callcenter vs. what is new

| ai-callcenter | Here | Notes |
|---|---|---|
| `openai_realtime_311.py` | `openai_realtime_emergency.py` | Same audio pumps, semantic VAD, barge-in, marks, silence monitor, cached clips, `end_call`, live `session.update` prompt swap. Tool logic moved out (below). |
| inline tool `if/elif` chain | `services/tool_executor.py` | One implementation shared by the phone handler, the text harness and tests. Runs in a thread. |
| `switch_department` + `format_issue_context` | `create_incident` + `format_incident_context` | The category's ACTIVE INCIDENT block replaces the dispatcher's live prompt. |
| `311_instructions/global.py`, `reception.py` | `emergency_instructions/global_rules.py`, `dispatcher.py` | Same rule-numbered style. |
| `311.json` (live data source) | `question_sets/incident_taxonomy.json` | Categories, resources, departments, safety protocols, triage questions. |
| `zone_hierarchy` hard gate, §23 barricade gates | `assign_resource` gates | Same reject-once-then-allow shape. |
| `config.py` (`IS_LOCAL` / Key Vault) | `core/config.py` | Same convention; local is the default here. |
| `test_311_instructions.py`, `test_realtime_text_chat.py` | `scripts/test_emergency_instructions.py`, `scripts/test_realtime_text_chat.py` | |
| `311_instructions/<service>.py` (one file per service) | `emergency_instructions/<department>.py` (one file per department) | Reference-only, loaded live by `format_department_context`. |
| `session.update` inline in the handler | `app/utils/realtime_session.py` (`build_initial_session`, `instructions_update_event`, `PromptState`) | Builders + change-detection in one place, shared with the text harness. |
| Studio Flow escalation ladder + `nurse_handoff_outreach` | `services/twilio_escalation.py` + `/twilio/escalation/*` + `call_handoffs` / `escalation_attempts` | Same behaviour (SMS-then-dial, timeouts, cycles, director, per-attempt log) as plain TwiML endpoints instead of a Studio Flow. |
| — (new) | geocoder, severity engine, duplicate merge, PostGIS ranking, hospital capacity, escalation, ops dashboard, fleet simulator, ETA watchdog, human-approval mode, dev scenario replayer |

Deliberately **not** copied: multi-tenancy/admin portal, QAlert, department scheduling, Studio Flows, recording +
Deepgram re-transcription, LangSmith tracing, Azure deploy scripts.

## Deviations from the PDF (on purpose)

1. `check_duplicate_incident` takes an `incident_id`, not an `embedding` — a voice model cannot emit a 1536-float
   vector; the backend embeds (OpenAI, `USE_EMBEDDINGS=true`) or falls back to token overlap.
2. `create_incident`/`assign_resource`: `location`, `caller_phone`, `call_id`, `eta_minutes` are optional and
   **server-filled** — a hallucinated coordinate or ETA is worse than none.
3. `estimate_severity.signals` is a closed typed object (+ optional `llm_severity_hint`), not free text.
4. Location is plain `lat/lng` columns; PostGIS builds the geography point on the fly (SQLite works too).
5. `pydub`/ffmpeg replaced by stdlib `wave`+`audioop` for the mu-law clips.
6. Transfers are driven by our own TwiML endpoints (visible, testable) rather than a Twilio Studio Flow JSON that must be re-imported by hand.

## Config

Everything is in `.env.example` (same `IS_LOCAL`/Key-Vault rule as ai-callcenter). Key switches: `DISPATCH_MODE`,
`CRITICAL_SEVERITY`, `GEOCODER_PROVIDER` (`gazetteer`|`nominatim`|`hybrid`), `SMS_ENABLED`, `FACILITY_PROVIDER`, `ROUTING_PROVIDER`, `GOOGLE_MAPS_API_KEY`, `ESCALATION_DEMO_NUMBER`,
`DASHBOARD_API_KEY`, `SIMULATE_FLEET`, `ENABLE_DEV_ENDPOINTS`.

## Known limits (be upfront with judges)

- **Not verified against live services:** the OpenAI Realtime event schema, Twilio media, TTS clip generation and
  `scripts/test_realtime_text_chat.py` were written from ai-callcenter's working code but never run live here.
  The handler *is* tested end-to-end against a scripted fake OpenAI server.
- **Twilio escalation transfer** is tested against a fake Twilio client (REST redirect URL/params, TwiML shape,
  attempt logging) — never a real ringing phone. Set `ESCALATION_DEMO_NUMBER` to your own phone to try it for real.
- **External APIs:** public Overpass/OSRM servers are rate-limited and best-effort (fine for a demo; self-host or
  use Google for anything real). The Google Places/Routes code paths are tested against mocks only (no key here).
- **PostGIS branch** (`resource_service._units_within_radius`) and the Postgres migration path were not run (no
  Docker daemon available to the author); the SQLite/haversine path is what the tests cover.
- **Synthetic data:** fleet, hospitals, phone numbers and the landmark gazetteer are demo data, clearly labelled.
  Coordinates are approximate.
- **Safety protocols** (CPR, fire, flood…) are generic pre-arrival guidance and **must be reviewed by the relevant
  authorities before any real use**. This is decision-support software, not a certified dispatch system.
- Dashboard access control is a single optional API key; production needs real role-based access, encryption at
  rest for call audio/transcripts, and a retention policy (PDF §9).
- Recording is not implemented (transcripts only). SQLite is single-writer — use Postgres for real load.
