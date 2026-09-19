"""Tool executor — the "hands" of the voice agent (PDF §4).

WHY THIS FILE EXISTS (deliberate difference from ai-callcenter): Grace keeps every tool handler
inline in the 5,000-line WebSocket handler and mirrors each one by hand into its text-test harness.
Here the tool logic lives once, in this module, and is called by
  * app/api/v1/endpoints/openai_realtime_emergency.py  (live phone calls), and
  * scripts/test_realtime_text_chat.py + tests/          (harness / offline tests)
so they can never drift apart. It is synchronous and runs in a worker thread
(asyncio.to_thread) so DB / geocoder latency never stalls the audio loop.

Hard code-level gates (ai-callcenter's proven pattern — reject with an instructive error, ONE-SHOT
escape valve so a stubborn model can never hang a live call):
  assign_resource is rejected when, for this incident,
    * the report is flagged low-confidence            (hard — human review, no escape valve)
    * severity has not been estimated                 (one-shot)
    * the duplicate check has not run [sev < critical] (one-shot)
    * the location was not confirmed  [sev < critical] (one-shot)
  Critical severity bypasses the last two: "send help now, refine en route" (PDF §9).

Tool results carry an optional `_effects` dict for the live handler (e.g. swap the prompt to the
category block, start a human transfer). The handler strips it before replying to the model.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import session_scope
from app.models.call import AgentToolCall, Call
from app.models.dispatch import Assignment
from app.models.incident import Incident
from app.models.resource import RESOURCE_TYPES, Resource
from app.services import (
    dispatch_service, escalation_service, facility_locator, geo_service, hospital_service, incident_service,
    resource_service, routing_service, severity, twilio_escalation,
)
from app.services.dashboard_hub import hub
from app.utils import taxonomy

logger = logging.getLogger(__name__)

RESOURCE_LABELS = {
    "ambulance": "an ambulance", "fire_truck": "a fire engine", "police_unit": "a police unit",
    "rescue_team": "a rescue team", "flood_rescue_boat": "a flood rescue boat",
    "hazmat_team": "a hazmat team", "tow_truck": "a tow truck", "helicopter": "a helicopter",
}
SIGNAL_BOOLS = list(severity.BOOL_POINTS.keys())
# Tools that end or hand off the call — the handler locks barge-in only around these.
TERMINAL_TOOLS = {"end_call", "transfer_to_human_operator"}


class ToolError(Exception):
    """Raised by a handler to return a clean error to the model (no traceback, no DB commit)."""


@dataclass
class ToolContext:
    call_id: Optional[uuid.UUID] = None
    call_sid: str = ""
    caller_phone: str = ""
    incident_id: Optional[str] = None  # the call's current (primary) incident
    state: dict = field(default_factory=lambda: {"gates": {}, "dup_checked": set(), "last_search": {}})


def _ok(**kw) -> dict:
    return {"status": "success", **kw}


def _err(message: str, status: str = "error", **kw) -> dict:
    return {"status": status, "message": message, **kw}


def _current_incident(db: Session, args: dict, ctx: ToolContext) -> Incident:
    iid = args.get("incident_id") or ctx.incident_id
    if not iid:
        raise ToolError("There is no incident yet — call create_incident first (location may be approximate).")
    try:
        inc = incident_service.get_incident(db, iid)
    except (ValueError, AttributeError):
        inc = None
    if not inc:
        raise ToolError(f"Unknown incident_id '{iid}'. Use the incident_id returned by create_incident "
                        "(or by check_duplicate_incident if it merged).")
    return incident_service.resolve_primary(db, inc)


def _point(args: dict, ctx: ToolContext, inc: Optional[Incident] = None) -> tuple[float, float]:
    loc = args.get("location") or {}
    try:
        return float(loc["lat"]), float(loc["lng"])
    except (KeyError, TypeError, ValueError):
        pass
    if inc is not None and inc.lat is not None:
        return inc.lat, inc.lng
    g = ctx.state.get("last_geocode")
    if g and g.get("found"):
        return g["lat"], g["lng"]
    raise ToolError("No location available yet — call geocode_location first.")


def _context_effect(ctx: ToolContext, inc: Incident, extra: tuple = ()) -> dict:
    """`load_context` effect for the realtime handler: the category + the departments whose instruction files should be
    in the live prompt. The category's own departments come first (lead first), then any escalated departments,
    capped at MAX_ACTIVE_DEPARTMENTS. The supervisor desk is ordered LAST, so it is the first to be dropped by the cap: a
    department that actually responds outranks the desk (whose file matters mainly for transfers). The handler feeds this to PromptState, which
    only sends a session.update if the loaded set really changes."""
    base = taxonomy.category_departments(inc.category)
    active = ctx.state.setdefault("active_departments", [])
    for d in list(base) + list(extra):
        if d not in active:
            active.append(d)
    ordered = list(dict.fromkeys(base + [d for d in active if d not in base and d != "supervisor"]))
    if "supervisor" in active or "supervisor" in base:
        ordered.append("supervisor")
    return {"category": inc.category, "departments": ordered[: taxonomy.MAX_ACTIVE_DEPARTMENTS]}


def _publish_incident(db: Session, inc: Incident, event: str = "incident_updated") -> None:
    hub.publish(event, incident_service.serialize_incident(db, inc))


# ────────────────────────── tool handlers ──────────────────────────
def _geocode_location(db: Session, a: dict, ctx: ToolContext) -> dict:
    if a.get("caller_confirmed"):
        g = ctx.state.get("last_geocode")
        if not g or not g.get("found"):
            raise ToolError("There is no geocoded location to confirm — call geocode_location with the "
                            "spoken location first.")
        ctx.state["location_confirmed"] = True
        if ctx.incident_id:
            inc = incident_service.get_incident(db, ctx.incident_id)
            if inc:
                inc.location_confirmed = True
                incident_service.log_event(db, inc.id, "location_confirmed", "agent", {"formatted": g["formatted"]})
                _publish_incident(db, inc)
        return _ok(confirmed=True, formatted=g["formatted"],
                   message="Location confirmed by the caller. Continue.")

    text_in = (a.get("raw_location_text") or "").strip()
    if not text_in:
        raise ToolError("raw_location_text is required (what the caller said about where they are).")
    g = geo_service.geocode(text_in, a.get("nearby_landmark"), a.get("city_hint"))
    if not g["found"]:
        return _err(
            "Could not place that location. Ask ONE clarifying question — a nearby landmark, a cross road, "
            "or the area name — then call geocode_location again. Do not guess a location.",
            status="not_found", found=False)
    ctx.state["last_geocode"] = g
    ctx.state["location_confirmed"] = False

    if ctx.incident_id:  # caller corrected the location mid-call
        inc = incident_service.get_incident(db, ctx.incident_id)
        if inc:
            inc.lat, inc.lng, inc.address_text = g["lat"], g["lng"], g["formatted"]
            inc.location_confidence, inc.location_confirmed = g["confidence"], False
            _publish_incident(db, inc)

    strong = g["confidence"] >= 0.75
    return _ok(
        found=True, formatted=g["formatted"], lat=g["lat"], lng=g["lng"], confidence=g["confidence"],
        provider=g["provider"],
        alternatives=[c["formatted"] for c in g["candidates"][1:]],
        message=(
            f"Read the location back to the caller in your own words (\"{g['formatted']}\") and get a clear "
            "yes. Once they confirm, call geocode_location again with caller_confirmed=true. If the situation "
            "is obviously critical, do NOT wait for the confirmation — create the incident and dispatch "
            "on this best guess and keep refining while you talk."
            + ("" if strong else " This match is weak — ask for a landmark or cross road to firm it up.")),
    )


def _create_incident(db: Session, a: dict, ctx: ToolContext) -> dict:
    category = a.get("category")
    cat = taxonomy.get_category(category or "")
    if not cat:
        raise ToolError(f"Unknown category '{category}'. Valid: {', '.join(taxonomy.category_names())}.")

    # Idempotent within a turn: a repeated create for the same category right after the first returns it.
    if ctx.incident_id:
        existing = incident_service.get_incident(db, ctx.incident_id)
        created = ctx.state.get("incident_created_at")
        if (existing and existing.category == category and created
                and datetime.now(timezone.utc) - created < timedelta(seconds=45)):
            return _ok(incident_id=str(existing.id), incident_number=existing.incident_number,
                       category=category, duplicate_call=True,
                       message="This incident was already created a moment ago — do not create it again. "
                               "Continue with check_duplicate_incident / estimate_severity.")

    g = ctx.state.get("last_geocode") if (ctx.state.get("last_geocode") or {}).get("found") else None
    lat = lng = None
    if g:
        lat, lng = g["lat"], g["lng"]  # server truth beats model-typed coordinates
    else:
        try:
            lat, lng = float(a["location"]["lat"]), float(a["location"]["lng"])
        except (KeyError, TypeError, ValueError):
            pass

    description = (a.get("description") or "").strip()
    confidence, reasons = incident_service.compute_confidence(
        a.get("confidence"), description, location_found=lat is not None,
        location_confidence=g["confidence"] if g else None)
    people = int(a.get("reported_people_affected") or 0)

    inc = incident_service.create_incident(
        db, category=category, description=description, lat=lat, lng=lng,
        address_text=g["formatted"] if g else None, location_confidence=g["confidence"] if g else None,
        location_confirmed=bool(ctx.state.get("location_confirmed") or a.get("location_confirmed")),
        people_affected=people, caller_phone=ctx.caller_phone or a.get("caller_phone"),
        call_id=ctx.call_id, sub_type=a.get("sub_type"), confidence=confidence,
        confidence_reasons=reasons, base_severity=taxonomy.base_severity(category))
    ctx.incident_id = str(inc.id)
    ctx.state["incident_created_at"] = datetime.now(timezone.utc)
    incident_service.log_event(db, inc.id, "created", "agent", {"category": category, "confidence": confidence,
                                                              "call_sid": ctx.call_sid})
    _publish_incident(db, inc, "incident_created")

    if inc.status == "needs_review":
        return _ok(
            incident_id=str(inc.id), incident_number=inc.incident_number, category=category,
            confidence=confidence, flagged_for_review=True, reasons=reasons,
            message="This report is flagged LOW CONFIDENCE and parked for a human reviewer. Do NOT dispatch "
                    "resources. Call transfer_to_human_operator with a short context summary.",
            _effects={"load_context": _context_effect(ctx, inc)})
    return _ok(
        incident_id=str(inc.id), incident_number=inc.incident_number, category=category,
        location_known=lat is not None, confidence=confidence,
        message=("Incident registered. NEXT, in this order: check_duplicate_incident, then estimate_severity "
                 "with the facts you have heard. If it is obviously critical, still run them — they are "
                 "instant — then dispatch without waiting for more details."),
        _effects={"load_context": _context_effect(ctx, inc)})


def _check_duplicate_incident(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx)
    match = incident_service.find_duplicate(db, inc, a.get("time_window_minutes"))
    ctx.state["dup_checked"].add(str(inc.id))
    if not match:
        return _ok(duplicate=False, incident_id=str(inc.id),
                   message="No matching active incident nearby — this is a new incident. Continue.")

    primary: Incident = match["incident"]
    before = primary.severity
    incident_service.merge_into(db, inc, primary, match["score"])
    incident_service.log_event(db, primary.id, "duplicate_merged", "agent",
                               {"merged_incident": inc.incident_number, "match_score": match["score"],
                                "distance_m": match["distance_m"], "report_count": primary.report_count})
    ctx.incident_id = str(primary.id)
    ctx.state["dup_checked"].add(str(primary.id))

    # A new report can add information ("water is now waist-deep"): re-score the living incident.
    res = severity.score_severity(primary.category, primary.severity_signals, primary.people_affected)
    if res["score"] > primary.severity:
        primary.severity, primary.severity_level = res["score"], res["level"]
        primary.severity_explanation = "; ".join(res["explanation"])
    auto = None
    if primary.severity >= settings.CRITICAL_SEVERITY and not primary.escalated:
        auto = escalation_service.escalate(
            db, primary, f"Severity {primary.severity} after {primary.report_count} reports",
            taxonomy.escalation_targets(primary.category), source="auto_severity")
    _publish_incident(db, inc, "incident_merged")
    _publish_incident(db, primary)
    return _ok(
        duplicate=True, merged_into={"incident_id": str(primary.id), "incident_number": primary.incident_number,
                                     "category": primary.category, "address": primary.address_text},
        incident_id=str(primary.id), match_score=match["score"], distance_m=match["distance_m"],
        breakdown=match["breakdown"], report_count=primary.report_count,
        severity_before=before, severity_after=primary.severity, auto_escalated=bool(auto),
        message=(f"This is the same event as INCIDENT #{primary.incident_number} ({primary.report_count} "
                 "reports now). Use the incident_id above from now on — do NOT create a new incident. "
                 "If the caller told you anything NEW, call estimate_severity again with those facts."),
        _effects={"load_context": _context_effect(ctx, primary, tuple((auto or {}).get("escalate_to", ())))})


def _clean_signals(raw: Optional[dict]) -> dict:
    out: dict[str, Any] = {}
    raw = raw or {}
    for k in SIGNAL_BOOLS:
        if k in raw and isinstance(raw[k], bool):
            out[k] = raw[k]
    if raw.get("injuries") in severity.INJURY_POINTS:
        out["injuries"] = raw["injuries"]
    if raw.get("water_level") in severity.WATER_POINTS:
        out["water_level"] = raw["water_level"]
    try:
        if raw.get("people_affected") is not None:
            out["people_affected"] = max(0, int(raw["people_affected"]))
    except (TypeError, ValueError):
        pass
    return out


def _estimate_severity(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx)
    signals = _clean_signals(a.get("signals"))
    merged = severity.merge_signals(inc.severity_signals, signals)
    hint = a.get("llm_severity_hint")
    res = severity.score_severity(inc.category, merged, inc.people_affected,
                                  int(hint) if isinstance(hint, (int, float)) else None)
    previous = inc.severity if inc.severity_estimated else None
    # Severity only ratchets up within a call unless the caller explicitly reports improvement.
    inc.severity = res["score"]
    inc.severity_level = res["level"]
    inc.severity_explanation = "; ".join(res["explanation"])
    inc.severity_signals = merged
    inc.severity_estimated = True
    inc.people_affected = max(inc.people_affected or 0, int(merged.get("people_affected", 0) or 0))
    incident_service.log_event(db, inc.id, "severity_estimated", "agent",
                               {"score": inc.severity, "level": res["level"], "previous": previous,
                                "explanation": res["explanation"]})

    auto = None
    if inc.severity >= settings.CRITICAL_SEVERITY and not inc.escalated:
        auto = escalation_service.escalate(
            db, inc, f"Auto-escalation: severity {inc.severity} (>= {settings.CRITICAL_SEVERITY}). "
                     + "; ".join(res["explanation"][:4]),
            taxonomy.escalation_targets(inc.category), source="auto_severity")
    _publish_incident(db, inc)
    msg = "Severity recorded."
    if res["level"] == "critical":
        msg = ("CRITICAL severity. Do not wait for more details: dispatch now on the best-guess location "
               "and keep refining it while you talk to the caller.")
    return _ok(incident_id=str(inc.id), severity=inc.severity, level=res["level"], previous=previous,
               explanation=res["explanation"], disagreement=res["disagreement"],
               auto_escalated=bool(auto), escalated_to=(auto or {}).get("escalate_to"), message=msg,
               _effects={"load_context": _context_effect(ctx, inc, tuple((auto or {}).get("escalate_to", ())))})


def _find_nearest_resource(db: Session, a: dict, ctx: ToolContext) -> dict:
    rtype = a.get("resource_type")
    if rtype not in RESOURCE_TYPES:
        raise ToolError(f"resource_type must be one of: {', '.join(RESOURCE_TYPES)}.")
    inc = None
    if a.get("incident_id") or ctx.incident_id:
        inc = _current_incident(db, a, ctx)
    lat, lng = _point(a, ctx, inc)
    radius = float(a.get("max_radius_km") or 15)
    found = resource_service.find_candidates(db, lat, lng, rtype, a.get("required_equipment"), radius)
    for e in found["ranked"]:
        ctx.state["last_search"][e["resource_id"]] = e
    hub.publish("resource_search", {"incident_id": str(inc.id) if inc else None, **found})

    ranked = found["ranked"]
    rejected = [{"callsign": e["callsign"], "reason": e.get("reason")}
                for e in found["considered"] if e.get("decision") == "rejected"][:5]
    if not ranked:
        return _err(f"No available {rtype} within {radius:g} km. Widen max_radius_km, try another "
                    "resource type, or escalate_incident so the supervisor can source one.",
                    status="none_available", considered=len(found["considered"]), rejected=rejected)
    return _ok(
        recommended=ranked[0]["resource_id"], candidates=[
            {k: e[k] for k in ("resource_id", "callsign", "type", "distance_km", "eta_minutes", "score",
                               "capabilities") if k in e} for e in ranked],
        why=ranked[0].get("reason"), rejected=rejected,
        message="Pick the top candidate and call assign_resource with its resource_id.")


def _get_hospital_capacity(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx) if (a.get("incident_id") or ctx.incident_id) else None
    lat, lng = _point(a, ctx, inc)
    res = hospital_service.hospital_capacity(db, lat, lng, a.get("required_specialty"))
    hub.publish("hospital_query", {"incident_id": str(inc.id) if inc else None, "lat": lat, "lng": lng, **res})
    return _ok(**res, message="Use this only to brief the dispatcher; never promise the caller a specific hospital.")


def _gate(ctx: ToolContext, inc: Incident, name: str, failing: bool, message: str) -> Optional[dict]:
    """One-shot reject: first time the condition fails, bounce with instructions; afterwards let it through."""
    if not failing:
        return None
    gates = ctx.state["gates"].setdefault(str(inc.id), {})
    if gates.get(name):
        return None
    gates[name] = True
    logger.warning("assign_resource rejected by gate '%s' incident=%s call=%s", name, inc.id, ctx.call_sid)
    return _err(message, status="rejected", gate=name)


def _assign_resource(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx)
    rid = a.get("resource_id")
    if not rid:
        raise ToolError("resource_id is required — call find_nearest_resource first.")

    if inc.status == "needs_review":
        return _err("This incident is flagged low-confidence and is in the human review queue. Do NOT dispatch. "
                    "Call transfer_to_human_operator.", status="rejected", gate="needs_review")
    if inc.lat is None:
        return _err("The incident has no location yet. Call geocode_location, then retry.",
                    status="rejected", gate="no_location")

    critical = inc.severity >= settings.CRITICAL_SEVERITY
    loc_ok = bool(ctx.state.get("location_confirmed") or inc.location_confirmed)
    for name, failing, msg in (
        ("severity_not_estimated", not inc.severity_estimated,
         "Cannot assign yet — call estimate_severity for this incident first (instant), then retry."),
        ("duplicate_not_checked", (not critical) and str(inc.id) not in ctx.state["dup_checked"],
         "Cannot assign yet — call check_duplicate_incident first (instant), then retry."),
        ("location_not_confirmed", (not critical) and not loc_ok,
         "Cannot assign yet — read the location back to the caller and get a yes, then call "
         "geocode_location with caller_confirmed=true and retry. (Critical incidents skip this.)"),
    ):
        blocked = _gate(ctx, inc, name, failing, msg)
        if blocked:
            return blocked

    try:
        rid_u = uuid.UUID(str(rid))
    except ValueError:
        raise ToolError("resource_id must be the id returned by find_nearest_resource.")
    existing = (db.query(Assignment).filter(Assignment.incident_id == inc.id, Assignment.resource_id == rid_u,
                                            Assignment.status.in_(("dispatched", "pending_approval", "en_route")))
                .first())
    if existing:  # double-tap: already assigned, do not claim or create anything again
        unit = db.get(Resource, rid_u)
        return _ok(assignment_id=str(existing.id), callsign=unit.callsign if unit else None,
                   eta_minutes=existing.eta_minutes, already_assigned=True,
                   message="Already assigned to this incident — do not assign it again.")

    approval = settings.DISPATCH_MODE == "approval"
    ok, unit, reason = resource_service.claim_resource(db, rid_u, "reserved" if approval else "en_route")
    if not ok:
        return _err(f"{reason}. Call find_nearest_resource again and pick another unit.", status="error")

    dist = geo_service.haversine_km(inc.lat, inc.lng, unit.lat, unit.lng)
    eta = resource_service.eta_minutes(unit.type, dist, unit.speed_kmh)  # authoritative, not the model's guess
    prior = ctx.state["last_search"].get(str(unit.id), {})
    route = None
    if unit.type not in ("helicopter", "flood_rescue_boat"):  # roads only matter for road vehicles
        route = routing_service.road_route(unit.lat, unit.lng, inc.lat, inc.lng)  # external service, None on failure
    if route:
        dist = route["distance_km"]
        eta = round(route["duration_minutes"] + resource_service.DISPATCH_DELAY_MIN, 1)
        prior = {**prior, "score_breakdown": {**(prior.get("score_breakdown") or {}), "route_provider": route["provider"]}}
    asg = Assignment(
        incident_id=inc.id, resource_id=unit.id, distance_km=round(dist, 2), eta_minutes=eta,
        eta_at=datetime.now(timezone.utc) + timedelta(minutes=eta),
        status="pending_approval" if approval else "dispatched",
        score_breakdown=prior.get("score_breakdown"), reason=prior.get("reason"))
    db.add(asg)
    incident_service.log_event(db, inc.id, "resource_assigned", "agent",
                               {"callsign": unit.callsign, "type": unit.type, "eta_minutes": eta,
                                "distance_km": round(dist, 2), "status": asg.status,
                                "route_provider": route["provider"] if route else "haversine"})
    if not approval and inc.status in ("open", "escalated"):
        inc.status = "dispatched" if inc.status == "open" else inc.status
    db.flush()

    label = RESOURCE_LABELS.get(unit.type, unit.type)
    hub.publish("assignment_created", {
        "assignment_id": str(asg.id), "incident_id": str(inc.id), "resource_id": str(unit.id),
        "callsign": unit.callsign, "type": unit.type, "status": asg.status, "eta_minutes": eta,
        "distance_km": asg.distance_km, "reason": asg.reason, "lat": unit.lat, "lng": unit.lng})
    _publish_incident(db, inc)
    if approval:
        say = (f"I've sent a dispatch request for {label} to the duty dispatcher, and it is waiting for their "
               "confirmation. Stay on the line with me.")
        note = "DISPATCH_MODE=approval: do NOT tell the caller a unit is on the way yet."
    else:
        say = f"Help is on the way — {label} is coming to you, arriving in about {round(eta)} minutes."
        note = "Say this sentence (translated to the caller's language) — do not invent a different ETA."
    return _ok(assignment_id=str(asg.id), resource_id=str(unit.id), callsign=unit.callsign, type=unit.type,
               eta_minutes=eta, distance_km=asg.distance_km, pending_approval=approval,
               say_to_caller=say, message=note + " Then call notify_dispatch_team for this unit.")


def _notify_dispatch_team(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx)
    rid = a.get("resource_id")
    try:
        rid_u = uuid.UUID(str(rid))
    except (ValueError, TypeError):
        raise ToolError("resource_id must be the id returned by find_nearest_resource / assign_resource.")
    asg = (db.query(Assignment).filter(Assignment.incident_id == inc.id, Assignment.resource_id == rid_u,
                                       Assignment.status.in_(("dispatched", "pending_approval", "en_route")))
           .order_by(Assignment.created_at.desc()).first())
    if not asg:
        raise ToolError("That unit is not assigned to this incident — call assign_resource first.")
    unit = db.get(Resource, rid_u)
    summary = (a.get("summary") or inc.description or "").strip()[:400]
    note = dispatch_service.notify_crew(db, inc, asg, unit, summary, held=asg.status == "pending_approval")
    hub.publish("notification", {"incident_id": str(inc.id), "callsign": unit.callsign,
                                 "status": note.status, "body": (note.payload or {}).get("body")})
    return _ok(callsign=unit.callsign, delivery=note.status,
               message="Crew alert recorded." + (" It will be released when the dispatcher approves."
                                                  if note.status == "held_for_approval" else ""))


def _escalate_incident(db: Session, a: dict, ctx: ToolContext) -> dict:
    inc = _current_incident(db, a, ctx)
    reason = (a.get("reason") or "").strip()
    if not reason:
        raise ToolError("reason is required.")
    targets = a.get("escalate_to") or []
    bad = [t for t in targets if t not in taxonomy.department_keys()]
    if bad:
        raise ToolError(f"Unknown escalate_to value(s): {bad}. Valid: {', '.join(taxonomy.department_keys())}.")
    out = escalation_service.escalate(db, inc, reason, targets, source="agent")
    _publish_incident(db, inc)
    hub.publish("escalation", {"incident_id": str(inc.id), "incident_number": inc.incident_number, **out,
                               "reason": reason})
    return _ok(**out, message="Escalated. Departments have been alerted — you do not need to say more than "
                              "'I've alerted additional teams' to the caller.",
               _effects={"load_context": _context_effect(ctx, inc, tuple(out["escalate_to"]))})


def _transfer_to_human_operator(db: Session, a: dict, ctx: ToolContext) -> dict:
    """Hand the LIVE call to a department's escalation ladder (ai-callcenter's calendar_schedule_transfer).

    This tool only PREPARES the transfer: it resolves the on-duty ladder from the department_contacts table and
    freezes it on a call_handoffs row. The realtime handler then lets the hold line finish playing and calls
    twilio_escalation.begin_transfer(), which redirects the live Twilio call into the ladder."""
    summary = (a.get("context_summary") or "").strip()
    department = a.get("department") or twilio_escalation.FALLBACK_DEPARTMENT
    if department not in taxonomy.department_keys():
        raise ToolError(f"Unknown department '{department}'. Valid: {', '.join(taxonomy.department_keys())}.")
    inc_id = None
    if ctx.incident_id:
        try:
            inc_id = uuid.UUID(ctx.incident_id)
        except ValueError:
            pass
    h = twilio_escalation.create_handoff(db, call_id=ctx.call_id, call_sid=ctx.call_sid, incident_id=inc_id,
                                         department_key=department, reason=summary)
    if ctx.call_id:
        call = db.get(Call, ctx.call_id)
        if call:
            call.transferred_to_human = True
    if inc_id:
        incident_service.log_event(db, inc_id, "call_transfer_requested", "agent",
                                   {"department": department, "handoff_id": str(h.id), "ladder_steps": h.total_steps})
    ctx.state["handoff_summary"] = summary
    hub.publish("human_handoff", {"call_sid": ctx.call_sid, "incident_id": ctx.incident_id, "handoff_id": str(h.id),
                                  "department": department, "context_summary": summary,
                                  "ladder": [{"name": s["name"], "role": s["role"], "cycle": s["cycle"]}
                                             for s in (h.ladder or [])], "from": ctx.caller_phone})
    return _ok(handoff_id=str(h.id), department=department, contacts_in_ladder=h.total_steps,
               message=("Say a short hold line NOW if you have not already (\"I'm connecting you to the "
                        f"{taxonomy.department_label(department)} now, please stay on the line\"), then stop "
                        "talking — the call is being handed over."),
               _effects={"transfer_call": {"handoff_id": str(h.id), "department": department,
                                           "context_summary": summary}})


def _find_nearest_department_center(db: Session, a: dict, ctx: ToolContext) -> dict:
    """Nearest centre of ANY department (fire station, police station, hospital, municipal office ...) from an
    external place service (Overpass/OSM or Google Places) with a real road ETA from a routing service."""
    inc = _current_incident(db, a, ctx) if (a.get("incident_id") or ctx.incident_id) else None
    lat, lng = _point(a, ctx, inc)
    res = facility_locator.find_nearest_center(db, a.get("department") or "", lat, lng,
                                               float(a.get("radius_km") or 10))
    if res.get("error"):
        raise ToolError(res["error"])
    hub.publish("facility_search", {"incident_id": str(inc.id) if inc else None, **res})
    if not res["centers"]:
        return _err(f"No {res['label']} found within the search radius. Widen radius_km or use a different "
                    "department.", status="none_available", provider=res["provider"])
    keep = ("name", "address", "phone", "distance_km", "road_distance_km", "eta_minutes", "route_provider")
    best, others = res["centers"][0], res["centers"][1:]
    return _ok(department=res["department"], label=res["label"], provider=res["provider"], fallback=res["fallback"],
               nearest={k: best[k] for k in keep if best.get(k) is not None},
               others=[{k: c[k] for k in keep if c.get(k) is not None} for c in others],
               message=("You may tell the caller the name of the nearest centre and roughly how far it is. Read any "
                        "phone number in grouped digits (Rule 11). Never invent an address or number that is not "
                        "in this result."))


def _give_caller_safety_instructions(db: Session, a: dict, ctx: ToolContext) -> dict:
    category = a.get("category") or "other"
    if not taxonomy.get_category(category):
        category = "other"
    proto = taxonomy.safety_protocol(category, a.get("situation_detail", ""))
    return _ok(category=category, protocol=proto["protocol"], steps=proto["steps"],
               message=("Give these to the caller in short spoken sentences in THEIR language — one or two steps "
                        "at a time, then check they are doing it. Add no other medical or safety advice. "
                        "Keep dispatching in parallel; do not wait to finish before calling find_nearest_resource."))


HANDLERS: dict[str, Callable[[Session, dict, ToolContext], dict]] = {
    "geocode_location": _geocode_location,
    "create_incident": _create_incident,
    "check_duplicate_incident": _check_duplicate_incident,
    "estimate_severity": _estimate_severity,
    "find_nearest_resource": _find_nearest_resource,
    "get_hospital_capacity": _get_hospital_capacity,
    "assign_resource": _assign_resource,
    "notify_dispatch_team": _notify_dispatch_team,
    "escalate_incident": _escalate_incident,
    "transfer_to_human_operator": _transfer_to_human_operator,
    "give_caller_safety_instructions": _give_caller_safety_instructions,
    "find_nearest_department_center": _find_nearest_department_center,
}


# ────────────────────────── dispatcher + audit ──────────────────────────
def _audit(name: str, args: dict, result: dict, ctx: ToolContext, duration_ms: int) -> None:
    try:
        clean = {k: v for k, v in result.items() if k != "_effects"}
        with session_scope() as db:
            iid = None
            try:
                iid = uuid.UUID(ctx.incident_id) if ctx.incident_id else None
            except ValueError:
                pass
            db.add(AgentToolCall(call_id=ctx.call_id, incident_id=iid, tool_name=name, arguments=args,
                                 result=clean, status=result.get("status", "success"), duration_ms=duration_ms))
        hub.publish("tool_call", {"call_sid": ctx.call_sid, "incident_id": ctx.incident_id, "tool": name,
                                  "arguments": args, "status": result.get("status"), "duration_ms": duration_ms,
                                  "result": clean})
    except Exception:
        logger.exception("tool-call audit failed (%s) — the call continues", name)


def execute_tool(name: str, args: Optional[dict], ctx: ToolContext) -> dict:
    """Run one tool. Never raises — a failure becomes an {status:'error'} result the model can act on."""
    args = args or {}
    started = time.perf_counter()
    handler = HANDLERS.get(name)
    if handler is None:
        result = _err(f"Unknown tool '{name}'.")
    else:
        try:
            with session_scope() as db:
                result = handler(db, args, ctx)
        except ToolError as e:
            result = _err(str(e))
        except Exception:
            logger.exception("tool %s crashed", name)
            result = _err("Internal error while running that tool. Keep helping the caller; if dispatch is "
                          "at stake and the tool keeps failing, call transfer_to_human_operator.")
    _audit(name, args, result, ctx, int((time.perf_counter() - started) * 1000))
    return result
