"""Dispatch notifications: crew alerts + department alerts (PDF `notify_dispatch_team`).

Every alert is recorded in `dispatch_notifications`. Real delivery is opt-in:
  * SMS        settings.SMS_ENABLED + Twilio credentials + the unit's contact_number
  * webhook    settings.DEPARTMENT_WEBHOOK_URL (department APIs are "the integration point")
  * dashboard  always
Anything not configured is recorded as status='simulated' — nothing is silently dropped.
"""
import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.dispatch import Assignment, DispatchNotification
from app.models.incident import Incident
from app.models.resource import Resource
from app.utils.taxonomy import department_label

logger = logging.getLogger(__name__)


def _send_sms(to: str, body: str) -> tuple[str, Optional[str]]:
    if not (settings.SMS_ENABLED and settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
            and settings.TWILIO_PHONE_NUMBER and to):
        return "simulated", None
    try:
        from twilio.rest import Client

        Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN).messages.create(
            to=to, from_=settings.TWILIO_PHONE_NUMBER, body=body[:1500]
        )
        return "sent", None
    except Exception as e:
        logger.exception("SMS send failed")
        return "failed", str(e)


send_sms = _send_sms  # public name (also used by twilio_escalation)


def _post_webhook(payload: dict) -> tuple[str, Optional[str]]:
    if not settings.DEPARTMENT_WEBHOOK_URL:
        return "simulated", None
    try:
        r = httpx.post(settings.DEPARTMENT_WEBHOOK_URL, json=payload, timeout=4.0)
        r.raise_for_status()
        return "sent", None
    except Exception as e:
        logger.warning("department webhook failed: %s", e)
        return "failed", str(e)


def dispatch_body(incident: Incident, resource: Optional[Resource], summary: str, eta: Optional[float]) -> str:
    where = incident.address_text or (f"{incident.lat:.5f},{incident.lng:.5f}" if incident.lat else "unknown")
    maps = f"https://maps.google.com/?q={incident.lat},{incident.lng}" if incident.lat is not None else ""
    unit = f"{resource.callsign}: " if resource else ""
    eta_txt = f" ETA {eta:g} min." if eta else ""
    return (f"{unit}INCIDENT #{incident.incident_number} [{incident.category.upper()}] "
            f"sev {incident.severity}. {summary} @ {where}.{eta_txt} {maps}").strip()


def notify_crew(db: Session, incident: Incident, assignment: Assignment, resource: Resource,
                summary: str, held: bool = False) -> DispatchNotification:
    body = dispatch_body(incident, resource, summary, assignment.eta_minutes)
    payload = {"incident_id": str(incident.id), "incident_number": incident.incident_number,
               "category": incident.category, "severity": incident.severity, "lat": incident.lat,
               "lng": incident.lng, "callsign": resource.callsign, "summary": summary, "body": body}
    if held:
        status, err = "held_for_approval", None
    else:
        status, err = _send_sms(resource.contact_number, body)
    n = DispatchNotification(incident_id=incident.id, assignment_id=assignment.id, resource_id=resource.id,
                             channel="sms" if resource.contact_number else "dashboard",
                             recipient=resource.contact_number or resource.callsign,
                             payload=payload, status=status, error=err)
    db.add(n)
    db.flush()
    return n


def release_held_notifications(db: Session, assignment: Assignment) -> int:
    """Human approved the assignment — actually send what was held back."""
    held = db.query(DispatchNotification).filter(
        DispatchNotification.assignment_id == assignment.id,
        DispatchNotification.status == "held_for_approval").all()
    for n in held:
        status, err = _send_sms(n.recipient, (n.payload or {}).get("body", ""))
        n.status, n.error = status, err
    db.flush()
    return len(held)


def notify_departments(db: Session, incident: Incident, departments: list[str], reason: str) -> list[dict]:
    """Multi-department alert (escalation). One row per department."""
    out = []
    for key in departments:
        payload = {"incident_id": str(incident.id), "incident_number": incident.incident_number,
                   "category": incident.category, "severity": incident.severity, "lat": incident.lat,
                   "lng": incident.lng, "address": incident.address_text, "reason": reason,
                   "department": key, "department_name": department_label(key)}
        status, err = _post_webhook(payload)
        db.add(DispatchNotification(incident_id=incident.id, channel="webhook", recipient=key,
                                    payload=payload, status=status, error=err))
        out.append({"department": key, "name": department_label(key), "status": status})
    db.flush()
    return out
