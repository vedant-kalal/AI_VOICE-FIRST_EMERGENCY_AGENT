"""Escalation (PDF §8): raise priority, notify the supervisor, trigger multi-department response.

Triggered three ways: the agent (escalate_incident tool), automatically when severity crosses
CRITICAL_SEVERITY (estimate_severity), and by the ETA watchdog (response_delay).
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.incident import Escalation, Incident
from app.services.dispatch_service import notify_departments
from app.services.incident_service import log_event
from app.utils.taxonomy import department_keys, escalation_targets


def escalate(db: Session, incident: Incident, reason: str, escalate_to: Optional[list[str]] = None,
             source: str = "agent") -> dict:
    valid = set(department_keys())
    targets = [d for d in (escalate_to or []) if d in valid]
    if not targets:
        targets = escalation_targets(incident.category)
    # de-dupe, keep order
    targets = list(dict.fromkeys(targets))

    row = Escalation(incident_id=incident.id, reason=reason, escalate_to=targets, source=source)
    db.add(row)
    incident.escalated = True
    if incident.status in ("open", "dispatched"):
        incident.status = "escalated"
    notified = notify_departments(db, incident, targets, reason)
    log_event(db, incident.id, "escalated", "agent" if source == "agent" else "system",
              {"reason": reason, "escalate_to": targets, "source": source})
    db.flush()
    return {"escalation_id": str(row.id), "escalate_to": targets, "notified": notified, "source": source}
