"""Resource engine: nearest *capable* resource (PDF §5) + atomic claiming (PDF §9 concurrency Q&A).

Search
  * Postgres  -> PostGIS ST_DWithin / ST_Distance on a geography point built from lat/lng.
  * otherwise -> pure-Python haversine over the (small) set of units of that type.
  The PostGIS branch fails over to the Python branch on any error, so a missing extension never
  takes dispatch down.

Ranking is never "closest wins" (PDF §5.3):
    score = w1*proximity + w2*capability_match + w3*availability + w4*travel_time - w5*queue_load
Every unit inside the search radius is reported back — chosen or rejected, with the reason — so the
dashboard can draw the search circle and explain the decision.

Claiming is one atomic transaction with `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres, so two agents
racing for the same ambulance cannot both win.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import IS_POSTGRES
from app.models.resource import Resource
from app.services.geo_service import haversine_km

logger = logging.getLogger(__name__)

# PDF §5.3 weights.
W_PROXIMITY, W_CAPABILITY, W_AVAILABILITY, W_TRAVEL, W_QUEUE = 0.30, 0.30, 0.10, 0.20, 0.10
ROAD_FACTOR = 1.35          # straight-line -> road distance
DISPATCH_DELAY_MIN = 1.0    # crew turn-out time
DEFAULT_SPEEDS_KMH = {
    "ambulance": 45, "fire_truck": 40, "police_unit": 50, "rescue_team": 40,
    "flood_rescue_boat": 25, "hazmat_team": 40, "tow_truck": 35, "helicopter": 180,
}


def eta_minutes(resource_type: str, distance_km: float, speed_kmh: Optional[float] = None) -> float:
    speed = speed_kmh or DEFAULT_SPEEDS_KMH.get(resource_type, 40)
    road_km = distance_km * (1.0 if resource_type == "helicopter" else ROAD_FACTOR)
    return round(road_km / speed * 60 + DISPATCH_DELAY_MIN, 1)


def _capability_match(capabilities: list, required: list[str]) -> float:
    if not required:
        return 1.0
    have = set(capabilities or [])
    return sum(1 for r in required if r in have) / len(required)


def score_unit(distance_km: float, capability_match: float, is_available: bool,
               eta_min: float, queue_load: int) -> tuple[float, dict]:
    """Pure function (unit-tested): the PDF §5.3 weighted score plus its breakdown."""
    proximity = 1.0 / (1.0 + distance_km)
    availability = 1.0 if is_available else 0.0
    travel = 1.0 / (1.0 + eta_min / 10.0)
    queue = min(1.0, queue_load / 5.0)
    score = (W_PROXIMITY * proximity + W_CAPABILITY * capability_match
             + W_AVAILABILITY * availability + W_TRAVEL * travel - W_QUEUE * queue)
    return round(score, 4), {
        "proximity": round(proximity, 3), "capability_match": round(capability_match, 3),
        "availability": availability, "travel_time": round(travel, 3), "queue_load": round(queue, 3),
    }


def _units_within_radius(db: Session, lat: float, lng: float, resource_type: str,
                         radius_km: float) -> list[tuple[Resource, float]]:
    """(resource, straight-line distance km) for every active unit of this type inside the radius."""
    if IS_POSTGRES:
        try:
            rows = db.execute(
                text(
                    """
                    SELECT id,
                           ST_Distance(ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                                       ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) / 1000.0 AS distance_km
                    FROM resources
                    WHERE type = :rtype AND is_active
                      AND ST_DWithin(ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography,
                                     ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius_m)
                    ORDER BY distance_km
                    """
                ),
                {"lat": lat, "lng": lng, "rtype": resource_type, "radius_m": radius_km * 1000.0},
            ).fetchall()
            ids = [uuid.UUID(str(r[0])) for r in rows]
            by_id = {r.id: r for r in db.query(Resource).filter(Resource.id.in_(ids)).all()} if ids else {}
            return [(by_id[uuid.UUID(str(r[0]))], float(r[1])) for r in rows if uuid.UUID(str(r[0])) in by_id]
        except Exception:
            logger.exception("PostGIS search failed — falling back to Python haversine")
            db.rollback()

    units = db.query(Resource).filter(Resource.type == resource_type, Resource.is_active.is_(True)).all()
    out = []
    for u in units:
        d = haversine_km(lat, lng, u.lat, u.lng)
        if d <= radius_km:
            out.append((u, d))
    out.sort(key=lambda x: x[1])
    return out


def find_candidates(db: Session, lat: float, lng: float, resource_type: str,
                    required_equipment: Optional[list[str]] = None, max_radius_km: float = 15.0,
                    limit: int = 3) -> dict:
    """Rank available units near a point. Returns ranked (best first) + every unit considered."""
    required = [e for e in (required_equipment or []) if e]
    within = _units_within_radius(db, lat, lng, resource_type, max_radius_km)

    qualified, partial, considered = [], [], []
    for unit, dist in within:
        caps = unit.capabilities or []
        cap_match = _capability_match(caps, required)
        eta = eta_minutes(unit.type, dist, unit.speed_kmh)
        is_available = unit.status == "available"
        entry = {
            "resource_id": str(unit.id), "callsign": unit.callsign, "type": unit.type,
            "status": unit.status, "capabilities": caps, "lat": unit.lat, "lng": unit.lng,
            "distance_km": round(dist, 2), "eta_minutes": eta,
        }
        if not is_available:
            entry.update(decision="rejected", reason=f"not available (status: {unit.status})")
            considered.append(entry)
            continue
        score, breakdown = score_unit(dist, cap_match, True, eta, unit.queue_load or 0)
        entry.update(score=score, score_breakdown=breakdown, capability_match=round(cap_match, 2))
        if cap_match >= 1.0:
            qualified.append(entry)
        else:
            missing = [r for r in required if r not in set(caps)]
            entry["missing_equipment"] = missing
            partial.append(entry)
        considered.append(entry)

    pool = qualified or partial
    pool.sort(key=lambda e: e["score"], reverse=True)
    ranked = pool[:limit]

    chosen_id = ranked[0]["resource_id"] if ranked else None
    for e in considered:
        if "decision" in e:
            continue
        if e["resource_id"] == chosen_id:
            e["decision"] = "selected"
        elif e in ranked:
            e["decision"] = "candidate"
        elif e in partial and qualified:
            e["decision"] = "rejected"
            e["reason"] = f"missing required equipment: {', '.join(e['missing_equipment'])}"
        else:
            e["decision"] = "rejected"
            e["reason"] = "outranked by a better-scoring unit"

    if ranked:
        best = ranked[0]
        avail = [e for e in considered if e.get("status") == "available"]
        nearest = min(avail, key=lambda e: e["distance_km"]) if avail else best
        if nearest["resource_id"] != best["resource_id"]:
            why = (f"{best['callsign']} chosen over closer {nearest['callsign']} "
                   f"({nearest['distance_km']} km) — better fit on capability/travel time/queue")
        elif not qualified and required:
            why = (f"no unit has all required equipment ({', '.join(required)}); "
                   f"{best['callsign']} is the best partial match")
        else:
            why = f"{best['callsign']} is the closest capable available unit ({best['distance_km']} km)"
        best["reason"] = why

    return {
        "ranked": ranked,
        "considered": considered,
        "search": {"lat": lat, "lng": lng, "radius_km": max_radius_km, "resource_type": resource_type,
                   "required_equipment": required},
    }


def claim_resource(db: Session, resource_id, new_status: str = "en_route") -> tuple[bool, Optional[Resource], str]:
    """Atomically claim an available unit. Returns (ok, resource, reason).

    Postgres: FOR UPDATE SKIP LOCKED — a row another transaction is claiming is skipped, not waited on.
    Caller owns the transaction (commit after creating the Assignment).
    """
    rid = resource_id if isinstance(resource_id, uuid.UUID) else uuid.UUID(str(resource_id))
    q = db.query(Resource).filter(Resource.id == rid)
    if IS_POSTGRES:
        q = q.with_for_update(skip_locked=True)
    unit = q.first()
    if unit is None:
        exists = db.query(Resource.id).filter(Resource.id == rid).first()
        return False, None, ("resource is being claimed by another incident" if exists
                             else "resource not found")
    if unit.status != "available":
        return False, unit, f"resource {unit.callsign} is no longer available (status: {unit.status})"
    unit.status = new_status
    unit.last_updated = datetime.now(timezone.utc)
    return True, unit, "claimed"


def release_resource(db: Session, resource_id) -> None:
    rid = resource_id if isinstance(resource_id, uuid.UUID) else uuid.UUID(str(resource_id))
    unit = db.query(Resource).filter(Resource.id == rid).first()
    if unit:
        unit.status = "available"
        unit.last_updated = datetime.now(timezone.utc)
