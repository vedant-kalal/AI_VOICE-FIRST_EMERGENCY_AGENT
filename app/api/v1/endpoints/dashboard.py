"""Ops dashboard API + live WebSocket (PDF §5.4).

REST gives the snapshot (map state, incident detail with the full tool-call audit trail); the WebSocket
/ws/dashboard pushes every change as it happens. Human-in-the-loop controls live here too: approve or
reject a pending dispatch, release or reject a low-confidence report, resolve an incident.

Access: set DASHBOARD_API_KEY to require `X-API-Key` (REST) / `?key=` (WebSocket). Left empty it is open —
fine for a local demo, NOT for anything exposed. Role-based access is the production story (README).
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.call import AgentToolCall, Call
from app.models.dispatch import Assignment, DispatchNotification
from app.models.department import CallHandoff
from app.models.incident import Escalation, Incident, IncidentEvent, IncidentReport
from app.models.resource import Facility, Resource
from app.services import dispatch_service, incident_service, resource_service
from app.utils import taxonomy
from app.services.dashboard_hub import hub


def _check_key(provided: Optional[str]) -> bool:
    if not settings.DASHBOARD_API_KEY:
        return True
    return bool(provided) and secrets.compare_digest(provided, settings.DASHBOARD_API_KEY)


def require_key(x_api_key: Optional[str] = Header(None), key: Optional[str] = Query(None)) -> None:
    if not _check_key(x_api_key or key):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard API key")


router = APIRouter(prefix="/api", tags=["Dashboard"], dependencies=[Depends(require_key)])
ws_router = APIRouter(tags=["Dashboard"])


def _uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")


def _resource_dict(r: Resource) -> dict:
    return {"id": str(r.id), "callsign": r.callsign, "type": r.type, "status": r.status, "lat": r.lat,
            "lng": r.lng, "capabilities": r.capabilities or [], "is_synthetic": r.is_synthetic}


def _assignment_dict(a: Assignment, db: Session) -> dict:
    r = db.get(Resource, a.resource_id)
    return {"id": str(a.id), "incident_id": str(a.incident_id), "resource_id": str(a.resource_id),
            "callsign": r.callsign if r else None, "type": r.type if r else None, "status": a.status,
            "eta_minutes": a.eta_minutes, "distance_km": a.distance_km, "reason": a.reason,
            "score_breakdown": a.score_breakdown, "approved_by": a.approved_by,
            "created_at": a.created_at.isoformat() if a.created_at else None}


@router.get("/state")
def get_state(db: Session = Depends(get_db)):
    incidents = (db.query(Incident).filter(Incident.status != "merged")
                 .order_by(Incident.created_at.desc()).limit(200).all())
    active_assignments = db.query(Assignment).filter(
        Assignment.status.in_(("pending_approval", "dispatched", "en_route", "arrived"))).all()
    # Escalation targets for the whole page in one query — the console scopes a department's view by them.
    esc: dict = {}
    for iid, targets in db.query(Escalation.incident_id, Escalation.escalate_to).filter(
            Escalation.incident_id.in_([i.id for i in incidents]) if incidents else False).all():
        bucket = esc.setdefault(iid, [])
        for key in (targets or []):
            if key not in bucket:
                bucket.append(key)
    return {
        "dispatch_mode": settings.DISPATCH_MODE,
        "critical_severity": settings.CRITICAL_SEVERITY,
        "center": {"lat": settings.CITY_CENTER_LAT, "lng": settings.CITY_CENTER_LNG},
        "incidents": [incident_service.serialize_incident(db, i, esc.get(i.id, [])) for i in incidents],
        "resources": [_resource_dict(r) for r in db.query(Resource).filter(Resource.is_active.is_(True)).all()],
        "facilities": [{"id": str(f.id), "type": f.type, "name": f.name, "lat": f.lat, "lng": f.lng,
                        "capacity": f.capacity or {}} for f in db.query(Facility).all()],
        "assignments": [_assignment_dict(a, db) for a in active_assignments],
        "live_calls": db.query(Call).filter(Call.is_live.is_(True)).count(),
    }


@router.get("/incidents/{incident_id}")
def get_incident_detail(incident_id: str, db: Session = Depends(get_db)):
    inc = incident_service.get_incident(db, _uid(incident_id))
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    reports = db.query(IncidentReport).filter(IncidentReport.incident_id == inc.id).order_by(
        IncidentReport.created_at).all()
    assignments = db.query(Assignment).filter(Assignment.incident_id == inc.id).all()
    escalations = db.query(Escalation).filter(Escalation.incident_id == inc.id).all()
    notes = db.query(DispatchNotification).filter(DispatchNotification.incident_id == inc.id).all()
    # Every tool call across every call that touched this incident (the explainability trail, PDF §11).
    calls = db.query(Call).filter(Call.incident_id == inc.id).all()
    call_ids = [c.id for c in calls]
    audit = (db.query(AgentToolCall).filter((AgentToolCall.incident_id == inc.id) |
                                            (AgentToolCall.call_id.in_(call_ids) if call_ids else False))
             .order_by(AgentToolCall.created_at).all())
    events = db.query(IncidentEvent).filter(IncidentEvent.incident_id == inc.id).order_by(IncidentEvent.created_at).all()
    handoffs = db.query(CallHandoff).filter(CallHandoff.incident_id == inc.id).order_by(CallHandoff.created_at).all()
    return {
        "incident": incident_service.serialize_incident(db, inc),
        "severity_signals": inc.severity_signals or {},
        "timeline": [{"type": e.event_type, "actor": e.actor, "payload": e.payload,
                      "at": e.created_at.isoformat() if e.created_at else None} for e in events],
        "handoffs": [{"id": str(h.id), "status": h.status, "department": h.department.name if h.department else None,
                      "total_steps": h.total_steps, "reason": h.reason,
                      "attempts": [{"step": a.step_index, "cycle": a.cycle, "name": a.contact_name, "role": a.role,
                                    "sms": a.sms_status, "dial_status": a.dial_status, "answered": a.answered,
                                    "duration_seconds": a.duration_seconds} for a in h.attempts]} for h in handoffs],
        "reports": [{"description": r.description, "people_affected": r.people_affected,
                     "match_score": r.match_score, "note": r.note,
                     "created_at": r.created_at.isoformat() if r.created_at else None} for r in reports],
        "assignments": [_assignment_dict(a, db) for a in assignments],
        "escalations": [{"reason": e.reason, "escalate_to": e.escalate_to, "source": e.source,
                         "created_at": e.created_at.isoformat() if e.created_at else None} for e in escalations],
        "notifications": [{"channel": n.channel, "recipient": n.recipient, "status": n.status,
                           "body": (n.payload or {}).get("body")} for n in notes],
        "calls": [{"id": str(c.id), "call_sid": c.call_sid, "status": c.status, "is_live": c.is_live,
                   "duration_seconds": c.duration_seconds, "summary": c.summary,
                   "caller_language": c.caller_language,
                   "transcript": [{"role": t.role, "content": t.content} for t in c.transcripts]} for c in calls],
        "tool_calls": [{"tool": t.tool_name, "status": t.status, "duration_ms": t.duration_ms,
                        "arguments": t.arguments, "result": t.result,
                        "at": t.created_at.isoformat() if t.created_at else None} for t in audit],
    }


@router.get("/review-queue")
def review_queue(db: Session = Depends(get_db)):
    """Low-confidence / prank reports parked for a human (PDF Scenario D)."""
    rows = db.query(Incident).filter(Incident.status == "needs_review").order_by(Incident.created_at.desc()).all()
    return [incident_service.serialize_incident(db, i) for i in rows]


class ReviewBody(BaseModel):
    action: str  # release | reject


@router.post("/incidents/{incident_id}/review")
def review_incident(incident_id: str, body: ReviewBody, db: Session = Depends(get_db)):
    inc = incident_service.get_incident(db, _uid(incident_id))
    if not inc or inc.status != "needs_review":
        raise HTTPException(status_code=404, detail="No such incident awaiting review")
    if body.action == "release":      # a human judged it genuine -> back into the live queue
        inc.status, inc.confidence = "open", "medium"
    elif body.action == "reject":     # false alarm
        inc.status = "closed"
    else:
        raise HTTPException(status_code=400, detail="action must be 'release' or 'reject'")
    db.commit()
    hub.publish("incident_updated", incident_service.serialize_incident(db, inc))
    return incident_service.serialize_incident(db, inc)


class StatusBody(BaseModel):
    status: str  # resolved | closed


@router.post("/incidents/{incident_id}/status")
def set_incident_status(incident_id: str, body: StatusBody, db: Session = Depends(get_db)):
    if body.status not in ("resolved", "closed"):
        raise HTTPException(status_code=400, detail="status must be 'resolved' or 'closed'")
    inc = incident_service.get_incident(db, _uid(incident_id))
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    inc.status = body.status
    for a in db.query(Assignment).filter(Assignment.incident_id == inc.id,
                                         Assignment.status.in_(("pending_approval", "dispatched", "en_route",
                                                                 "arrived"))).all():
        a.status = "completed"
        resource_service.release_resource(db, a.resource_id)
        r = db.get(Resource, a.resource_id)
        if r:
            hub.publish("resource_updated", _resource_dict(r))
    db.commit()
    hub.publish("incident_updated", incident_service.serialize_incident(db, inc))
    return incident_service.serialize_incident(db, inc)


class ApproveBody(BaseModel):
    approver: Optional[str] = "dashboard-operator"


@router.post("/assignments/{assignment_id}/approve")
def approve_assignment(assignment_id: str, body: ApproveBody = ApproveBody(), db: Session = Depends(get_db)):
    a = db.get(Assignment, _uid(assignment_id))
    if not a or a.status != "pending_approval":
        raise HTTPException(status_code=404, detail="No such assignment awaiting approval")
    r = db.get(Resource, a.resource_id)
    inc = db.get(Incident, a.incident_id)
    a.status, a.approved_by = "dispatched", (body.approver or "dashboard-operator")[:100]
    a.eta_at = datetime.now(timezone.utc)  # ETA clock starts at approval
    if a.eta_minutes:
        from datetime import timedelta
        a.eta_at = a.eta_at + timedelta(minutes=a.eta_minutes)
    if r:
        r.status = "en_route"
    if inc and inc.status == "open":
        inc.status = "dispatched"
    released = dispatch_service.release_held_notifications(db, a)
    db.commit()
    payload = _assignment_dict(a, db)
    hub.publish("assignment_created", {**payload, "lat": r.lat if r else None, "lng": r.lng if r else None})
    if inc:
        hub.publish("incident_updated", incident_service.serialize_incident(db, inc))
    return {**payload, "released_notifications": released}


@router.post("/assignments/{assignment_id}/reject")
def reject_assignment(assignment_id: str, db: Session = Depends(get_db)):
    a = db.get(Assignment, _uid(assignment_id))
    if not a or a.status != "pending_approval":
        raise HTTPException(status_code=404, detail="No such assignment awaiting approval")
    a.status = "rejected"
    resource_service.release_resource(db, a.resource_id)
    db.commit()
    hub.publish("assignment_updated", _assignment_dict(a, db))
    return _assignment_dict(a, db)


@router.get("/taxonomy")
def get_taxonomy():
    """The department / category map the console scopes its views by — served from
    emergency_instructions/question_sets/incident_taxonomy.json so the UI never hard-codes a second copy."""
    data = taxonomy.load_taxonomy()
    return {
        "departments": [{"key": k, "label": v.get("name", k), "contact": v.get("contact")}
                        for k, v in data["departments"].items()],
        "categories": [{"key": k, "label": v.get("label", k), "departments": v.get("departments", []),
                        "escalate_to": v.get("escalate_to", []),
                        "resources": [r.get("type") for r in v.get("resources", []) if r.get("type")],
                        "base_severity": v.get("base_severity")}
                       for k, v in data["categories"].items()],
        "critical_severity": settings.CRITICAL_SEVERITY,
    }


def _call_row(c: Call, inc: Optional[Incident]) -> dict:
    return {"id": str(c.id), "call_sid": c.call_sid, "from": c.from_number, "status": c.status,
            "is_live": c.is_live, "duration_seconds": c.duration_seconds, "summary": c.summary,
            "caller_language": c.caller_language, "transferred_to_human": c.transferred_to_human,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "incident_id": str(c.incident_id) if c.incident_id else None,
            "incident_number": inc.incident_number if inc else None,
            "category": inc.category if inc else None,
            "sub_type": inc.sub_type if inc else None,
            "address_text": inc.address_text if inc else None,
            "severity": inc.severity if inc else None,
            "severity_level": inc.severity_level if inc else None,
            "incident_status": inc.status if inc else None,
            "report_count": inc.report_count if inc else None}


@router.get("/calls")
def list_calls(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0), since_hours: Optional[int] = Query(None, ge=1, le=24 * 365),
               department: Optional[str] = Query(None)):
    """Past calls for the console's call log. `department` keeps only calls whose incident is owned by, or
    was escalated to, that department (the same rule the console's department lens uses)."""
    q = db.query(Call)
    if since_hours:
        q = q.filter(Call.created_at >= datetime.now(timezone.utc) - timedelta(hours=since_hours))
    rows = q.order_by(Call.created_at.desc()).offset(offset).limit(limit).all()
    incidents = {}
    ids = [c.incident_id for c in rows if c.incident_id]
    if ids:
        incidents = {i.id: i for i in db.query(Incident).filter(Incident.id.in_(ids)).all()}
    out = [_call_row(c, incidents.get(c.incident_id) if c.incident_id else None) for c in rows]
    if department:
        if department not in taxonomy.department_keys():
            raise HTTPException(status_code=400, detail=f"Unknown department '{department}'")
        # Owned by the department (via its category), or escalated into it — the console's lens rule.
        keep = {str(i.id) for i in incidents.values()
                if department in taxonomy.category_departments(i.category)
                or department in incident_service.escalated_departments(db, i.id)}
        out = [c for c in out if c["incident_id"] in keep]
    return out


@router.get("/calls/{call_id}")
def get_call_detail(call_id: str, db: Session = Depends(get_db)):
    """One call with its transcript and the tool calls the agent made on it — the call-log drawer."""
    c = db.get(Call, _uid(call_id))
    if not c:
        raise HTTPException(status_code=404, detail="Call not found")
    inc = db.get(Incident, c.incident_id) if c.incident_id else None
    tools = (db.query(AgentToolCall).filter(AgentToolCall.call_id == c.id)
             .order_by(AgentToolCall.created_at).all())
    return {
        **_call_row(c, inc),
        "transcript": [{"role": t.role, "content": t.content,
                        "at": t.created_at.isoformat() if t.created_at else None} for t in c.transcripts],
        "tool_calls": [{"tool": t.tool_name, "status": t.status, "duration_ms": t.duration_ms,
                        "arguments": t.arguments, "result": t.result,
                        "at": t.created_at.isoformat() if t.created_at else None} for t in tools],
        "departments": (taxonomy.category_departments(inc.category) if inc else []),
        "escalated_to": (incident_service.escalated_departments(db, inc.id) if inc else []),
    }


@ws_router.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket, key: Optional[str] = Query(None)):
    if not _check_key(key):
        await websocket.close(code=1008)
        return
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive pings from the page; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)
