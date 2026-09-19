"""Live-call transfer through a department's escalation ladder, driven by Twilio.

Counterpart of ai-callcenter's after-hours escalation (its Twilio Studio Flow + nurse_handoff_outreach). Same
behaviour, but implemented as plain TwiML endpoints in this app so the whole flow is readable and testable:

  agent calls transfer_to_human_operator(department=...)
    -> tool_executor.create_handoff(): resolves the ladder from `department_contacts`, freezes it on a
       `call_handoffs` row                                                     (status: pending)
    -> handler speaks the hold line, waits for it to finish playing, then begin_transfer():
       Twilio REST  calls(<live call sid>).update(url=/twilio/escalation/dial?handoff_id=..&step=0)
       — Twilio pulls the caller's LEG OUT of our media stream and asks us for TwiML       (status: dialing)
    -> GET/POST /twilio/escalation/dial      SMS the contact, then <Dial timeout=..><Number url=whisper/>
    -> POST     /twilio/escalation/result    answered? bridge ends, done   |   no-answer/busy/failed? <Redirect> to
                                             the next step; after the last one -> apology + hangup  (exhausted)
    -> POST     /twilio/escalation/leg-status  ringing / answered / completed events per leg
    -> POST     /twilio/escalation/whisper   context read to the person who picks up, before they are bridged

Ladder = every on_call contact in priority order, repeated `repeat_cycles` times, then the director(s) once
(ai-callcenter §18). EVERY dial attempt is its own `escalation_attempts` row — a repeat cycle never overwrites
the first attempt.

Nothing here runs without Twilio credentials: begin_transfer() reports 'simulated' and the handler falls back to
a clean hang-up, so the app is fully usable locally.
"""
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import pytz
from sqlalchemy.orm import Session
from twilio.twiml.voice_response import Dial, VoiceResponse

from app.core.config import settings
from app.models.department import CallHandoff, Department, DepartmentContact, EscalationAttempt
from app.models.incident import Incident
from app.services import dispatch_service
from app.services.dashboard_hub import hub

logger = logging.getLogger(__name__)

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
FALLBACK_DEPARTMENT = "supervisor"
FINAL_MESSAGE = ("We could not reach a dispatcher right now. Your report has been recorded and help is being "
                 "arranged. If this is a life-threatening emergency, please also call one one two.")


# ── ladder construction (pure, unit-tested) ─────────────────────────────────
def is_on_duty(window: Optional[dict], now_local: datetime) -> bool:
    """window = {"days": [0-6, Mon=0], "start": "HH:MM", "end": "HH:MM"}; None = 24x7. Overnight windows
    (start > end, e.g. 18:00-07:00) count the after-midnight hours toward the PREVIOUS day's shift."""
    if not window:
        return True
    days = window.get("days", list(range(7)))
    start, end = window.get("start", "00:00"), window.get("end", "23:59")
    hm = now_local.strftime("%H:%M")
    if start <= end:
        return now_local.weekday() in days and start <= hm <= end
    if hm >= start:
        return now_local.weekday() in days
    return ((now_local.weekday() - 1) % 7) in days and hm <= end


def build_ladder(contacts: list[dict], cycles: int) -> list[dict]:
    """contacts: dicts with id/name/role/phone/priority. Returns ordered steps: on_call+supervisor contacts by
    priority, repeated `cycles` times, then director(s) once."""
    ring = sorted((c for c in contacts if c["role"] != "director"), key=lambda c: c["priority"])
    directors = sorted((c for c in contacts if c["role"] == "director"), key=lambda c: c["priority"])
    steps: list[dict] = []
    for cycle in range(1, max(1, cycles) + 1):
        for c in ring:
            steps.append({**c, "cycle": cycle})
    for c in directors:
        steps.append({**c, "cycle": 1})
    for i, s in enumerate(steps):
        s["step"] = i
    return steps


def resolve_ladder(db: Session, department_key: str, now: Optional[datetime] = None) -> tuple[Optional[Department], list[dict]]:
    """Department + its ladder now. Falls back to the supervisor desk when the department (or its on-duty
    contacts) is missing — a transfer must always reach *someone*."""
    for key in (department_key, FALLBACK_DEPARTMENT):
        dept = db.query(Department).filter(Department.key == key, Department.is_active.is_(True)).first()
        if dept is None:
            continue
        now_local = (now or datetime.now(timezone.utc)).astimezone(pytz.timezone(dept.timezone))
        contacts = [
            {"id": str(c.id), "name": c.name, "role": c.role, "phone": c.phone, "priority": c.priority}
            for c in dept.contacts
            if c.is_active and E164.match(c.phone or "") and is_on_duty(c.duty_window, now_local)
        ]
        ladder = build_ladder(contacts, dept.repeat_cycles)
        if ladder:
            return dept, ladder
    return None, []


# ── handoff lifecycle ───────────────────────────────────────────────────────
def create_handoff(db: Session, *, call_id, call_sid: str, incident_id, department_key: str, reason: str) -> CallHandoff:
    dept, ladder = resolve_ladder(db, department_key)
    h = CallHandoff(call_id=call_id, incident_id=incident_id, department_id=dept.id if dept else None,
                    call_sid=call_sid, reason=(reason or "")[:1000], ladder=ladder, total_steps=len(ladder),
                    status="pending" if ladder else "simulated")
    db.add(h)
    db.flush()
    return h


def _base_url() -> str:
    return f"https://{settings.PUBLIC_HOST}/twilio/escalation"


def _twilio_client():
    from twilio.rest import Client
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def begin_transfer(db: Session, handoff_id) -> dict:
    """Redirect the LIVE call to the escalation ladder. Returns {'status': 'dialing'|'simulated'|'failed', ...}."""
    h = db.get(CallHandoff, handoff_id)
    if h is None or h.total_steps == 0:
        return {"status": "simulated", "reason": "no on-duty contacts configured"}
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.PUBLIC_HOST) \
            or h.call_sid.startswith(("TEST", "DEMO")):
        h.status = "simulated"
        return {"status": "simulated", "reason": "Twilio not configured (or demo call)"}
    url = f"{_base_url()}/dial?handoff_id={h.id}&step=0"
    try:
        _twilio_client().calls(h.call_sid).update(url=url, method="POST")
    except Exception as e:
        logger.error("Twilio redirect to escalation ladder failed: %s", e)
        h.status = "failed"
        return {"status": "failed", "reason": str(e)}
    h.status, h.started_at = "dialing", datetime.now(timezone.utc)
    _publish(h)
    return {"status": "dialing", "steps": h.total_steps, "url": url}


def _publish(h: CallHandoff) -> None:
    hub.publish("handoff_update", {
        "handoff_id": str(h.id), "incident_id": str(h.incident_id) if h.incident_id else None,
        "status": h.status, "current_step": h.current_step, "total_steps": h.total_steps,
        "attempts": [{"step": a.step_index, "name": a.contact_name, "role": a.role, "cycle": a.cycle,
                      "dial_status": a.dial_status, "sms": a.sms_status} for a in h.attempts]})


def _step(h: CallHandoff, step: int) -> Optional[dict]:
    return (h.ladder or [])[step] if 0 <= step < len(h.ladder or []) else None


def _attempt(db: Session, h: CallHandoff, step: int, s: dict) -> EscalationAttempt:
    a = (db.query(EscalationAttempt)
         .filter(EscalationAttempt.handoff_id == h.id, EscalationAttempt.step_index == step).first())
    if a is None:
        a = EscalationAttempt(handoff_id=h.id, contact_id=uuid.UUID(s["id"]), step_index=step, cycle=s["cycle"],
                              contact_name=s["name"], role=s["role"], phone=s["phone"], dial_status="pending",
                              raw_status_events=[])
        db.add(a)
        db.flush()
    return a


# ── TwiML producers ─────────────────────────────────────────────────────────
def _hangup(message: Optional[str] = None) -> str:
    r = VoiceResponse()
    if message:
        r.say(message)
    r.hangup()
    return str(r)


def dial_twiml(db: Session, h: CallHandoff, step: int) -> str:
    """TwiML for one rung of the ladder: SMS the contact, then dial them with a whisper of the context."""
    s = _step(h, step)
    if s is None:
        return _exhausted(h, db)
    dept = db.get(Department, h.department_id) if h.department_id else None
    timeout = dept.dial_timeout_seconds if dept else 30
    a = _attempt(db, h, step, s)
    h.current_step, h.status = step, "dialing"

    inc = db.get(Incident, h.incident_id) if h.incident_id else None
    if a.sms_status is None:  # SMS goes out immediately BEFORE each dial (ai-callcenter §18), once per attempt
        where = (inc.address_text if inc else None) or "location on the dashboard"
        body = (f"EMERGENCY TRANSFER: a caller is being connected to you now. "
                f"{('Incident #%s %s at %s. ' % (inc.incident_number, inc.category, where)) if inc else ''}"
                f"Please answer.")
        a.sms_status, _err = dispatch_service.send_sms(s["phone"], body)
    a.dial_status, a.started_at = "initiated", datetime.now(timezone.utc)

    q = f"handoff_id={h.id}&step={step}"
    r = VoiceResponse()
    if step == 0:
        r.say(f"Connecting you to a {dept.name if dept else 'dispatcher'} now. Please stay on the line.")
    dial = Dial(timeout=timeout, answer_on_bridge=True, action=f"{_base_url()}/result?{q}", method="POST",
                caller_id=settings.TWILIO_PHONE_NUMBER or None)
    dial.number(s["phone"], url=f"{_base_url()}/whisper?{q}", method="POST",
                status_callback=f"{_base_url()}/leg-status?{q}", status_callback_method="POST",
                status_callback_event="initiated ringing answered completed")
    r.append(dial)
    _publish(h)
    return str(r)


def result_twiml(db: Session, h: CallHandoff, step: int, form: dict) -> str:
    """Twilio's <Dial action>: decide whether to bridge-end, try the next rung, or give up."""
    if h.status in ("answered", "exhausted", "cancelled"):
        return _hangup()  # a late/duplicate callback for a finished handoff
    s = _step(h, step)
    a = _attempt(db, h, step, s) if s else None
    status = (form.get("DialCallStatus") or "failed").lower()
    if a is not None:
        a.dial_status = status if status in ("completed", "busy", "no-answer", "failed", "canceled") else "failed"
        a.dial_call_sid = form.get("DialCallSid") or a.dial_call_sid
        a.ended_at = datetime.now(timezone.utc)
        try:
            a.duration_seconds = int(form.get("DialCallDuration") or 0)
        except ValueError:
            a.duration_seconds = 0
        a.answered = status == "completed"

    if status == "completed" and a is not None:  # answered and the bridge has now ended normally
        h.status, h.ended_at = "answered", datetime.now(timezone.utc)
        h.answered_contact_id = uuid.UUID(s["id"])  # ladder is stored as JSON -> ids are strings
        _publish(h)
        return _hangup()

    if step + 1 < h.total_steps:  # nobody picked up: next rung, same live call, no gap for the caller
        h.current_step = step + 1
        _publish(h)
        r = VoiceResponse()
        r.redirect(f"{_base_url()}/dial?handoff_id={h.id}&step={step + 1}", method="POST")
        return str(r)
    return _exhausted(h, db)


def _exhausted(h: CallHandoff, db: Session) -> str:
    h.status, h.ended_at = "exhausted", datetime.now(timezone.utc)
    _publish(h)
    if h.incident_id:  # the supervisor must know a caller was left without a human
        from app.services import escalation_service
        inc = db.get(Incident, h.incident_id)
        if inc is not None:
            escalation_service.escalate(db, inc, "Call transfer exhausted: no on-duty contact answered",
                                        [FALLBACK_DEPARTMENT], source="system")
    return _hangup(FINAL_MESSAGE)


def whisper_twiml(db: Session, h: CallHandoff) -> str:
    """Read to the PERSON who answers, before they are bridged to the caller."""
    inc = db.get(Incident, h.incident_id) if h.incident_id else None
    ctx = re.sub(r"[^\w\s.,:#\-/]", " ", (h.reason or "")[:240])
    intro = (f"Emergency line transfer. Incident {inc.incident_number}, {inc.category.replace('_', ' ')}. "
             if inc else "Emergency line transfer. ")
    r = VoiceResponse()
    r.say(intro + ctx)
    return str(r)


def record_leg_status(db: Session, h: CallHandoff, step: int, form: dict) -> None:
    s = _step(h, step)
    if s is None:
        return
    a = _attempt(db, h, step, s)
    events = list(a.raw_status_events or [])
    events.append({"status": form.get("CallStatus"), "sid": form.get("CallSid"),
                   "at": datetime.now(timezone.utc).isoformat()})
    a.raw_status_events = events
    a.dial_call_sid = form.get("CallSid") or a.dial_call_sid
    live = {"ringing": "ringing", "in-progress": "answered"}.get((form.get("CallStatus") or "").lower())
    if live and a.dial_status not in ("completed", "busy", "no-answer", "failed", "canceled"):
        a.dial_status = live
    _publish(h)
