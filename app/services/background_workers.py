"""Background workers started with the app.

1. FleetSimulator  (SIMULATE_FLEET=true) — stands in for real vehicle GPS pings (PDF §5.4: "vehicle icons
   animating toward the incident once dispatched"). Moves en_route units towards their incident at
   SIM_SPEED_MULTIPLIER x real speed, pushes `resource_moved`, and marks arrival. Synthetic, demo only.
2. ResponseWatchdog — PDF §8: "No status update within ETA + buffer -> escalate_incident('response_delay')".
   The cron-style safety net behind the conversational escalation.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.database import session_scope
from app.models.dispatch import Assignment
from app.models.incident import Incident
from app.models.resource import Resource, ResourceLocationPing
from app.services import escalation_service, geo_service, incident_service
from app.services.dashboard_hub import hub

logger = logging.getLogger(__name__)
TICK_SECONDS = 2.0
ARRIVE_KM = 0.05


def _fleet_tick() -> None:
    with session_scope() as db:
        rows = (db.query(Assignment).filter(Assignment.status.in_(("dispatched", "en_route"))).all())
        for a in rows:
            inc = db.get(Incident, a.incident_id)
            unit = db.get(Resource, a.resource_id)
            if not inc or inc.lat is None or not unit:
                continue
            step_km = (unit.speed_kmh or 40) * settings.SIM_SPEED_MULTIPLIER * (TICK_SECONDS / 3600.0)
            lat, lng = geo_service.move_towards(unit.lat, unit.lng, inc.lat, inc.lng, step_km)
            unit.lat, unit.lng = lat, lng
            unit.last_updated = datetime.now(timezone.utc)
            # GPS history (what a real AVL/GPS feed would append): powers vehicle animation + after-action review.
            db.add(ResourceLocationPing(resource_id=unit.id, assignment_id=a.id, lat=lat, lng=lng,
                                        speed_kmh=(unit.speed_kmh or 40) * settings.SIM_SPEED_MULTIPLIER,
                                        source="simulator", recorded_at=unit.last_updated))
            if a.status == "dispatched":
                a.status = "en_route"
            arrived = geo_service.haversine_km(lat, lng, inc.lat, inc.lng) <= ARRIVE_KM
            if arrived:
                a.status, unit.status = "arrived", "on_scene"
            hub.publish("resource_moved", {"resource_id": str(unit.id), "callsign": unit.callsign,
                                           "lat": lat, "lng": lng, "status": unit.status,
                                           "assignment_id": str(a.id), "assignment_status": a.status,
                                           "incident_id": str(inc.id)})


def _watchdog_tick() -> None:
    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=settings.RESPONSE_DELAY_BUFFER_MIN)
    with session_scope() as db:
        late = (db.query(Assignment)
                .filter(Assignment.status.in_(("dispatched", "en_route")),
                        Assignment.delay_escalated_at.is_(None), Assignment.eta_at.isnot(None)).all())
        for a in late:
            eta_at = a.eta_at if a.eta_at.tzinfo else a.eta_at.replace(tzinfo=timezone.utc)
            if now <= eta_at + buffer:
                continue
            inc = incident_service.get_incident(db, a.incident_id)
            if not inc or inc.status in ("resolved", "closed", "merged"):
                continue
            a.delay_escalated_at = now
            out = escalation_service.escalate(
                db, inc, f"response_delay: unit has not arrived {settings.RESPONSE_DELAY_BUFFER_MIN} min after its ETA",
                None, source="watchdog")
            hub.publish("escalation", {"incident_id": str(inc.id), "incident_number": inc.incident_number,
                                       "reason": "response_delay", **out})
            hub.publish("incident_updated", incident_service.serialize_incident(db, inc))


async def _loop(name: str, fn, interval: float) -> None:
    while True:
        try:
            await asyncio.to_thread(fn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s tick failed", name)
        await asyncio.sleep(interval)


def start_workers() -> list[asyncio.Task]:
    tasks = [asyncio.create_task(_loop("watchdog", _watchdog_tick, 30.0))]
    if settings.SIMULATE_FLEET:
        tasks.append(asyncio.create_task(_loop("fleet-simulator", _fleet_tick, TICK_SECONDS)))
        logger.info("Fleet simulator ON (x%s speed) — synthetic vehicle movement", settings.SIM_SPEED_MULTIPLIER)
    return tasks
