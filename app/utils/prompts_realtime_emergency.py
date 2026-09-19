# ── Emergency Realtime: universal tool set + instruction loader ──────────────────────────────
#
# Counterpart of ai-callcenter's prompts_realtime_311.py, same design:
#
#   • TOOLING MODEL — "Option A": GLOBAL_TOOLS is sent ONCE at session start and never resent.
#     Only `instructions` change afterwards (session.update is a partial merge).
#   • ONE agent (the dispatcher) instead of 11 departments. What swaps mid-call is the *ACTIVE
#     INCIDENT* block: the moment create_incident registers a category, the handler pushes
#     dispatcher + that category's triage questions / resource list / safety-protocol pointers
#     (the analogue of switch_department + format_issue_context).
#   • Each tool's `description` is the real behavioural spec (free against the instruction budget).
#
# Schema follows PDF §4 with three deliberate, documented differences (README "Deviations"):
#   1. check_duplicate_incident takes an incident_id, not an `embedding` — a voice model cannot emit a
#      1536-float vector; the backend embeds the incident text itself.
#   2. create_incident / assign_resource take `call_id`, `caller_phone`, `eta_minutes`, `location` as
#      OPTIONAL — the server fills them (a hallucinated coordinate or ETA is worse than none).
#   3. estimate_severity's `signals` is a closed, typed object of stated facts (no free text), plus an
#      optional `llm_severity_hint` the rules may nudge by at most +15.

import importlib
import os
from datetime import datetime

import pytz

from app.core.config import settings
from app.models.resource import RESOURCE_TYPES
from app.services.facility_locator import supported_departments
from app.services.severity import BOOL_POINTS, INJURY_POINTS, WATER_POINTS
from app.utils import taxonomy

GREETING_TEXT = "Emergency line. Tell me what's happening and where you are. I'm here with you."
GOODBYE_TEXT = "Help is on the way. Stay safe. Goodbye."
HANDOFF_TEXT = "I'm connecting you to a human dispatcher now. Please stay on the line."

# Any of these at the END of the final AI transcript -> schedule the end-call fallback (same idea as
# ai-callcenter). Deliberately narrow: an emergency caller must never be hung up on by a stray phrase.
end_call_messages = [
    "stay safe. goodbye",
    "stay safe, goodbye",
    "stay safe goodbye",
]

CATEGORY_NAMES = taxonomy.category_names()
DEPARTMENT_KEYS = taxonomy.department_keys()
CENTER_DEPARTMENTS = supported_departments()

_LOCATION_SCHEMA = {
    "type": "object",
    "description": "Optional. Coordinates are filled in by the server from geocode_location — omit this.",
    "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}},
}

_SIGNALS_SCHEMA = {
    "type": "object",
    "description": (
        "STATED FACTS ONLY — things the caller said or you clearly heard. Omit anything unknown; never "
        "guess true. Never infer from accent, language, tone, panic or neighbourhood."
    ),
    "properties": {
        "injuries": {"type": "string", "enum": list(INJURY_POINTS.keys())},
        "water_level": {"type": "string", "enum": list(WATER_POINTS.keys()),
                        "description": "Flood only."},
        "people_affected": {"type": "integer", "minimum": 0},
        **{key: {"type": "boolean", "description": label} for key, (_pts, label) in BOOL_POINTS.items()},
    },
    "additionalProperties": False,
}

_geocode_location_tool = {
    "type": "function",
    "name": "geocode_location",
    "description": (
        "Convert what the caller said about WHERE they are into map coordinates. Call it the moment you have "
        "ANY usable location words — a landmark, a highway, a junction, an area — while you keep talking. A "
        "house number is never required.\n\n"
        "Result: `found` + `formatted` (the place name) + `confidence`. If not found, ask ONE clarifying "
        "question (a landmark or cross road) and retry — never guess a location.\n\n"
        "CONFIRMATION: read `formatted` back to the caller in natural words and get a clear yes. Then call "
        "this tool AGAIN with caller_confirmed=true (raw_location_text may be omitted then). For an obviously "
        "critical situation (Rule 08) do not wait for the confirmation — dispatch on the best guess and "
        "keep refining. If the caller CORRECTS the location later, call this tool again with the new words: "
        "the incident's location is updated automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "raw_location_text": {"type": "string",
                                  "description": "The location exactly as the caller said it."},
            "nearby_landmark": {"type": "string", "description": "A landmark the caller mentioned, if any."},
            "city_hint": {"type": "string", "description": "City / area if the caller named one."},
            "caller_confirmed": {"type": "boolean",
                                 "description": "true ONLY after the caller said yes to the read-back."},
        },
        "required": [],
    },
}

_create_incident_tool = {
    "type": "function",
    "name": "create_incident",
    "description": (
        "Register the emergency in the incident engine. Call it as soon as you know roughly WHAT is "
        "happening and WHERE — an approximate location is fine, do not wait for the read-back. Pick the "
        "closest `category` from the enum (the dispatcher instructions list them); for a mixed event choose the "
        "most life-threatening and describe the rest in `description`.\n\n"
        "The response returns `incident_id` — keep it. Right after this call: check_duplicate_incident, then "
        "estimate_severity. If the response says the report is flagged for review (low confidence), do NOT "
        "dispatch: call transfer_to_human_operator.\n\n"
        "`confidence` is YOUR read of the call: 'low' for contradictory, nonsensical, joking or abusive "
        "callers (never accuse them — see Rule 13); 'medium' if key facts are unclear; otherwise 'high'. The "
        "backend cross-checks it with its own rules."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORY_NAMES},
            "sub_type": {"type": "string", "description": "Optional sub-type, e.g. 'gas_leak', 'hit_and_run'."},
            "description": {"type": "string",
                            "description": "One or two factual sentences in English: what happened, who is "
                                           "affected, hazards. No opinions."},
            "reported_people_affected": {"type": "integer", "minimum": 0},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "location": _LOCATION_SCHEMA,
            "caller_phone": {"type": "string", "description": "Optional — the server uses the caller ID."},
            "call_id": {"type": "string", "description": "Optional — the server fills this in."},
            "location_confirmed": {"type": "boolean"},
        },
        "required": ["category", "description"],
    },
}

_check_duplicate_incident_tool = {
    "type": "function",
    "name": "check_duplicate_incident",
    "description": (
        "Compare this incident with every active incident nearby (distance + time window + category + "
        "text similarity) and MERGE it if it is the same real-world event. Call it immediately after "
        "create_incident. It is instant — do not narrate it.\n\n"
        "If `duplicate` is true, the response gives the primary `incident_id`: use THAT id for every later tool "
        "call and never create another incident (Rule 06). Several callers reporting one event become ONE "
        "incident whose severity rises as reports add facts. If the caller then tells you anything NEW, call "
        "estimate_severity again with just the new facts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string", "description": "Optional — defaults to this call's incident."},
            "time_window_minutes": {"type": "integer", "minimum": 5, "maximum": 720,
                                    "description": "How far back to look. Default 60."},
        },
        "required": [],
    },
}

_estimate_severity_tool = {
    "type": "function",
    "name": "estimate_severity",
    "description": (
        "Score severity 0-100 from the STATED FACTS in `signals`. The score is computed by explicit rules, "
        "not by you — your job is only to report facts accurately. Call it after check_duplicate_incident and "
        "again whenever the caller gives an important new fact (someone stopped breathing, fire spreading, "
        "water rising). Only send the facts you learned since last time; earlier facts are kept and merged.\n\n"
        "Severity >= 85 is CRITICAL: the platform escalates to the supervisor and the relevant departments "
        "automatically, and you must dispatch immediately on the best-guess location (Rule 08). Below 85, "
        "dispatch also requires the duplicate check and a confirmed location.\n\n"
        "`llm_severity_hint` is optional; the rules may raise the score by at most 15 points from it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string", "description": "Optional — defaults to this call's incident."},
            "signals": _SIGNALS_SCHEMA,
            "llm_severity_hint": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["signals"],
    },
}

_find_nearest_resource_tool = {
    "type": "function",
    "name": "find_nearest_resource",
    "description": (
        "Search the live resource map for the best-fit AVAILABLE unit of one type near the incident. Ranking "
        "is never 'closest wins': it weighs distance, capability match, availability, travel time and how "
        "loaded the unit is, and the ops dashboard shows every unit considered and why the others were "
        "rejected. Call it once per resource type listed in the ACTIVE INCIDENT section, in the SAME turn as "
        "give_caller_safety_instructions.\n\n"
        "Use `required_equipment` when the situation demands it (e.g. ['jaws_of_life'] for a trapped "
        "occupant, ['industrial_fire_equipment'] for an industrial fire). The response lists candidates best-"
        "first with a `recommended` resource_id. Then call assign_resource. If none is available, widen "
        "max_radius_km, try another type, or escalate_incident."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resource_type": {"type": "string", "enum": list(RESOURCE_TYPES)},
            "required_equipment": {"type": "array", "items": {"type": "string"},
                                   "description": "Capabilities the unit must have, if any."},
            "max_radius_km": {"type": "number", "minimum": 1, "maximum": 100, "description": "Default 15."},
            "location": _LOCATION_SCHEMA,
            "incident_id": {"type": "string", "description": "Optional — defaults to this call's incident."},
        },
        "required": ["resource_type"],
    },
}

_get_hospital_capacity_tool = {
    "type": "function",
    "name": "get_hospital_capacity",
    "description": (
        "Check the nearest hospitals' free beds / ICU / trauma / burn / toxicology capacity so the "
        "dispatcher can route a patient. Call it after dispatch for medical or injury incidents. Use "
        "`required_specialty` when the case needs it (icu, trauma, burn_unit, toxicology, cardiac). This is a "
        "briefing for the dispatcher — NEVER promise the caller a specific hospital."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "required_specialty": {"type": "string",
                                   "enum": ["general", "icu", "trauma", "burn_unit", "toxicology", "cardiac"]},
            "location": _LOCATION_SCHEMA,
            "incident_id": {"type": "string"},
        },
        "required": [],
    },
}

_assign_resource_tool = {
    "type": "function",
    "name": "assign_resource",
    "description": (
        "Formally assign a unit from find_nearest_resource to the incident and dispatch it. The ETA is "
        "computed by the server — never quote your own.\n\n"
        "THE RESPONSE TELLS YOU WHAT TO SAY: it returns `say_to_caller`; say that sentence (in the caller's "
        "language) and nothing that contradicts it. In human-approval mode it says the request is waiting "
        "for confirmation — then you must NOT tell the caller a unit is on the way.\n\n"
        "This call is protected by code-level checks (enforced, not just requested): the incident must have a "
        "severity estimate, and — unless severity is critical — the duplicate check must have run and the "
        "location must be confirmed. A rejection tells you exactly which step to do; do it and retry. A "
        "low-confidence incident is never dispatched: call transfer_to_human_operator.\n\n"
        "After a successful assignment call notify_dispatch_team for that unit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string", "description": "The resource_id from find_nearest_resource."},
            "incident_id": {"type": "string", "description": "Optional — defaults to this call's incident."},
            "eta_minutes": {"type": "number", "description": "Optional and ignored — the server computes it."},
        },
        "required": ["resource_id"],
    },
}

_notify_dispatch_team_tool = {
    "type": "function",
    "name": "notify_dispatch_team",
    "description": (
        "Push a structured dispatch alert (SMS / push / radio relay) to an ASSIGNED unit's crew with the "
        "incident summary and coordinates. Call it once per assigned unit, right after assign_resource. "
        "Fails if that unit is not assigned to this incident."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string"},
            "summary": {"type": "string",
                        "description": "One factual sentence for the crew: what, who, hazards."},
            "incident_id": {"type": "string"},
        },
        "required": ["resource_id", "summary"],
    },
}

_escalate_incident_tool = {
    "type": "function",
    "name": "escalate_incident",
    "description": (
        "Raise priority, alert the supervisor, and trigger a multi-department response (e.g. fire + hazmat + "
        "police together). Call it when the event outgrows one team or gets worse — the platform already "
        "escalates automatically at critical severity, so you rarely need it first. Give a factual `reason`. "
        "Leave `escalate_to` empty to use the category's default departments."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "escalate_to": {"type": "array", "items": {"type": "string", "enum": DEPARTMENT_KEYS}},
            "incident_id": {"type": "string"},
        },
        "required": ["reason"],
    },
}

_transfer_to_human_operator_tool = {
    "type": "function",
    "name": "transfer_to_human_operator",
    "description": (
        "Transfer the LIVE call to a human through a department's escalation ladder: the on-duty contacts are "
        "rung one after another (an SMS goes to each just before the dial, each rings ~30 seconds, the whole "
        "ladder repeats, then the department director) and whoever answers hears a short whisper of your "
        "`context_summary` before being bridged to the caller.\n\n"
        "Use it when: the report is flagged low-confidence; the caller is abusive, joking or contradictory; you "
        "cannot understand them after two attempts; they insist on a human; the situation needs a decision only a "
        "person can make; or tools keep failing and dispatch is at stake. Pick the `department` that fits (default "
        "'supervisor' = the duty desk); e.g. 'fire_dept' or 'police' when a live person there should take over.\n\n"
        f"SAY THE HOLD LINE OUT LOUD FIRST, in the same turn: \"{HANDOFF_TEXT}\" — then call this tool. "
        "Calling it silently makes the caller hear dead air. Put everything the human needs in `context_summary` "
        "(location, what happened, what you already dispatched) so nobody has to re-ask. After this tool the call "
        "leaves you — say nothing more."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "context_summary": {"type": "string"},
            "department": {"type": "string", "enum": DEPARTMENT_KEYS,
                           "description": "Whose escalation ladder to ring. Default: supervisor."},
            "call_id": {"type": "string", "description": "Optional — the server fills this in."},
        },
        "required": ["context_summary"],
    },
}

_find_nearest_department_center_tool = {
    "type": "function",
    "name": "find_nearest_department_center",
    "description": (
        "Find the NEAREST CENTRE of any department — fire station, police station, hospital, municipal or "
        "disaster-management office, utility or forest office — to the incident (or to a location you pass), using "
        "a live external map/places service, with the real road distance and ETA. Use it when the caller asks where "
        "to go (\"where is the nearest hospital / police station?\"), when they can move to safety or get "
        "someone to a centre, or to tell the dispatcher which centre is closest. Pass the responsible `department` "
        "(ems = hospitals, fire_dept = fire stations, police = police stations, ...).\n\n"
        "Tell the caller only what the result contains — the centre's name and roughly how far/how long; read any "
        "phone number in grouped digits. Never invent an address or number. This is information, not a dispatch: "
        "it does not send anyone anywhere (use find_nearest_resource + assign_resource for that)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": CENTER_DEPARTMENTS},
            "radius_km": {"type": "number", "minimum": 1, "maximum": 50, "description": "Default 10."},
            "location": _LOCATION_SCHEMA,
            "incident_id": {"type": "string", "description": "Optional — defaults to this call's incident."},
        },
        "required": ["department"],
    },
}

_give_caller_safety_instructions_tool = {
    "type": "function",
    "name": "give_caller_safety_instructions",
    "description": (
        "Retrieve the standard pre-arrival safety instructions for the incident category — fire evacuation, "
        "flood high-ground guidance, CPR steps and so on — to speak while help is en route. Call it IN PARALLEL "
        "with find_nearest_resource (real 911 protocol: dispatch and instruct at the same time).\n\n"
        "Put the caller's actual situation in `situation_detail` (e.g. 'man not breathing', 'heavy "
        "bleeding', 'woman in labour'): it selects the right sub-protocol (CPR, bleeding, choking, "
        "childbirth). Then speak the returned steps in short sentences in the caller's language, one or two "
        "at a time, checking they are doing them. Add no other medical or safety advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": CATEGORY_NAMES},
            "situation_detail": {"type": "string"},
        },
        "required": ["category"],
    },
}

_end_call_tool = {
    "type": "function",
    "name": "end_call",
    "description": (
        "End the call with a polite closing, ONLY when help is dispatched and the caller is safe or has "
        "nothing else (Rule 16). ABSOLUTE REQUIREMENT: the goodbye MUST be spoken as audio FIRST in the SAME "
        "response turn — this tool plays nothing. Never end a call with a caller in danger, and never because "
        "of silence alone."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "closing_message": {"type": "string",
                                "description": "e.g. 'Help is on the way. Stay safe. Goodbye.'"},
        },
        "required": ["closing_message"],
    },
}

# ── GLOBAL_TOOLS — the single toolset, loaded once at session start, never swapped ─────────────
GLOBAL_TOOLS = [
    _geocode_location_tool,
    _create_incident_tool,
    _check_duplicate_incident_tool,
    _estimate_severity_tool,
    _find_nearest_resource_tool,
    _get_hospital_capacity_tool,
    _assign_resource_tool,
    _notify_dispatch_team_tool,
    _escalate_incident_tool,
    _transfer_to_human_operator_tool,
    _give_caller_safety_instructions_tool,
    _find_nearest_department_center_tool,
    _end_call_tool,
]


# ── Instruction assembly ─────────────────────────────────────────────────────────────────────
def _category_table() -> str:
    rows = []
    for name in CATEGORY_NAMES:
        cat = taxonomy.get_category(name)
        rows.append(f'  {name:<20} {cat["label"]} — {", ".join(cat["sub_types"])}')
    return "\n".join(rows)


def _inject(text: str, from_number: str) -> str:
    """Runtime placeholders via str.replace (not str.format) so prompt authors can use literal braces."""
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    return (
        text.replace("{caller_id_number}", from_number or "unknown")
        .replace("{current_date}", now.strftime("%A, %B %d, %Y"))
        .replace("{current_time}", now.strftime("%H:%M"))
        .replace("{city}", settings.DEFAULT_CITY)
        .replace("{category_table}", _category_table())
    )


def format_incident_context(category: str) -> str:
    """Render the ACTIVE INCIDENT block for a registered category from incident_taxonomy.json — the
    analogue of ai-callcenter's format_issue_context(). Injected via session.update the instant
    create_incident succeeds; a later incident/category simply REPLACES it (no accumulation)."""
    cat = taxonomy.get_category(category) or taxonomy.get_category("other")
    lines = [
        f"========== ACTIVE INCIDENT: {cat['label']} (category=\"{category}\") ==========",
        "",
        f"Sub-types: {', '.join(cat['sub_types'])}.",
        "",
        "--- RESOURCES TO SEND (call find_nearest_resource once per line, in one turn) ---",
    ]
    if cat["resources"]:
        for r in cat["resources"]:
            eq = f", required_equipment={r['required_equipment']}" if r["required_equipment"] else ""
            lines.append(f"  - {r['type']}{eq}  ({r['note']})")
    else:
        lines.append("  - none by default — describe the situation to the supervisor via escalate_incident.")
    lines += [
        "",
        "--- TRIAGE QUESTIONS (one at a time, AFTER dispatch is moving; skip anything already answered) ---",
    ]
    lines += [f"  {i}. {q}" for i, q in enumerate(cat["triage_questions"], 1)]
    lines += [
        "",
        "Before each question re-check everything the caller has already said — one answer often covers "
        "another item. Ask only what is genuinely missing, phrased naturally in the caller's language.",
        "",
        "--- SAFETY INSTRUCTIONS ---",
        f"Call give_caller_safety_instructions(category=\"{category}\", situation_detail=<the caller's actual "
        "situation>) in the SAME turn as your first find_nearest_resource. Speak its steps one or two at a "
        "time. If the person stops breathing or collapses mid-call, call it again with that detail.",
        "",
        "--- ESCALATION ---",
        "Departments alerted on escalation: "
        + ", ".join(taxonomy.department_label(d) for d in taxonomy.escalation_targets(category)) + ". "
        "Critical severity escalates automatically; use escalate_incident yourself if the situation "
        "grows beyond what is dispatched.",
    ]
    if cat.get("needs_hospital"):
        spec = "burn_unit for burns, toxicology for poisoning/chemicals, cardiac for chest pain, trauma for injuries"
        lines += [
            "",
            "--- HOSPITAL BRIEFING ---",
            f"After dispatch, call get_hospital_capacity ({spec}). It is a dispatcher briefing only — never "
            "promise the caller a hospital.",
        ]
    lines += [
        "",
        "--- REPEAT REPORTS ---",
        "If check_duplicate_incident merged this call into an earlier incident, the resources may already "
        "be moving: do not re-dispatch the same unit types unless the new facts justify more (estimate_severity "
        "first).",
        "",
    ]
    return "\n".join(lines)


# Department instruction files live next to global_rules.py / dispatcher.py, one per department key — the counterpart of
# ai-callcenter's one-file-per-service 311_instructions/. Adding a department = add the key to incident_taxonomy.json
# and a <key>.py file here (scripts/test_emergency_instructions.py fails if either is missing).
_DEPT_DIR = os.path.join(taxonomy.INSTRUCTIONS_DIR)


def department_instruction(key: str) -> str:
    """SYSTEM_INSTRUCTION text of one department file ('' if the department has no file)."""
    if key not in DEPARTMENT_KEYS or not os.path.exists(os.path.join(_DEPT_DIR, f"{key}.py")):
        return ""
    return importlib.import_module(f"emergency_instructions.{key}").SYSTEM_INSTRUCTION.strip()


def format_department_context(department_keys: list[str]) -> str:
    """The ACTIVE DEPARTMENT(S) section: each active department's own instruction file, lead department first.
    Rendered fresh on every session.update (a REPLACE, never an accumulation), like format_incident_context."""
    keys = [k for k in dict.fromkeys(department_keys) if k in DEPARTMENT_KEYS][: taxonomy.MAX_ACTIVE_DEPARTMENTS]
    if not keys:
        return ""
    lead = taxonomy.department_label(keys[0])
    parts = [
        "========== SECTION 20 — ACTIVE DEPARTMENT(S) ==========",
        "",
        f"Lead department for this incident: {lead}. The sections below are REFERENCE for what each responsible",
        "department handles, its priority cues and cautions. Global Rules still govern every word you say.",
        "",
    ]
    for k in keys:
        text = department_instruction(k)
        if text:
            parts += [text, ""]
    return "\n".join(parts)


def load_dispatcher_instructions(from_number: str = "", category: str = "", departments: list[str] | None = None) -> str:
    """Build the system prompt: GLOBAL_CONTEXT + dispatcher persona
       + (once an incident is registered)  ACTIVE DEPARTMENT(S)  +  ACTIVE INCIDENT for the category.

    `departments` defaults to the category's own departments; escalations add more (lead department stays first)."""
    global_mod = importlib.import_module("emergency_instructions.global_rules")
    disp_mod = importlib.import_module("emergency_instructions.dispatcher")
    combined = f"{global_mod.GLOBAL_CONTEXT}\n\n{disp_mod.SYSTEM_INSTRUCTION}"
    if category and taxonomy.get_category(category):
        keys = list(departments) if departments else taxonomy.category_departments(category)
        dept_block = format_department_context(keys)
        if dept_block:
            combined = f"{combined}\n\n{dept_block}"
        combined = f"{combined}\n\n{format_incident_context(category)}"
    return _inject(combined, from_number)
