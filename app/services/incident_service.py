"""Incident engine: create, confidence, duplicate detection + merge, serialisation.

Duplicate detection (PDF §4 `check_duplicate_incident`): distance + time window + category (+ text
similarity — embeddings when USE_EMBEDDINGS=true, token-overlap otherwise). A match MERGES the new
report into the existing incident instead of creating a second one, so five callers in one flooded
street become one living incident whose severity keeps rising (PDF Scenario C).
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.call import Call
from app.models.incident import ACTIVE_STATUSES, Incident, IncidentEvent, IncidentReport
from app.services.geo_service import haversine_km
from app.utils.taxonomy import duplicate_radius_m

logger = logging.getLogger(__name__)

DUP_THRESHOLD = 0.70
W_DISTANCE, W_CATEGORY, W_TEXT = 0.50, 0.25, 0.25
# Categories that plausibly describe the same real-world event when reported differently.
RELATED_CATEGORIES = [
    {"road_accident", "road_blockage", "medical"},
    {"fire", "industrial_chemical", "utility_failure", "building_collapse"},
    {"flood", "natural_disaster", "road_blockage"},
    {"crime", "public_disturbance", "medical"},
]
_STOP = set("the a an and or of in on at to is are was were it this that there with for from my our "
            "please help someone people near by have has been being just very really".split())


# ── text similarity ─────────────────────────────────────────────────────────
def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 2 and t not in _STOP}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def embed_text(text: str) -> Optional[list[float]]:
    """Optional OpenAI embedding. None when disabled / unavailable — callers fall back to token overlap."""
    if not (settings.USE_EMBEDDINGS and settings.OPENAI_API_KEY and text):
        return None
    try:
        from openai import OpenAI

        resp = OpenAI(api_key=settings.OPENAI_API_KEY).embeddings.create(
            model=settings.EMBEDDING_MODEL, input=text[:2000]
        )
        return list(resp.data[0].embedding)
    except Exception:
        logger.exception("embedding failed — using token overlap for duplicate detection")
        return None


def text_similarity(a: str, b: str, emb_a: Optional[list] = None, emb_b: Optional[list] = None) -> float:
    if emb_a and emb_b:
        return max(0.0, _cosine(emb_a, emb_b))
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _categories_related(a: str, b: str) -> bool:
    return any(a in grp and b in grp for grp in RELATED_CATEGORIES)


# ── creation ────────────────────────────────────────────────────────────────
def compute_confidence(model_confidence: Optional[str], description: str,
                       location_found: bool, location_confidence: Optional[float]) -> tuple[str, list[str]]:
    """Rule-based cross-check of the model's own confidence read (prank / unclear-call handling).
    Never raises the model's confidence; only lowers it when facts are missing."""
    order = ["low", "medium", "high"]
    level = model_confidence if model_confidence in order else "high"
    reasons: list[str] = []
    if model_confidence == "low":
        reasons.append("agent flagged the call as low confidence (contradictory / nonsensical / abusive)")
    if not location_found:
        reasons.append("no usable location resolved")
        level = min(level, "medium", key=order.index)
    elif location_confidence is not None and location_confidence < 0.5:
        reasons.append(f"location match is weak ({location_confidence:.2f})")
        level = min(level, "medium", key=order.index)
    if len((description or "").strip()) < 12:
        reasons.append("description too short to act on")
        level = "low"
    return level, reasons


def create_incident(db: Session, *, category: str, description: str, lat: Optional[float],
                    lng: Optional[float], address_text: Optional[str], location_confidence: Optional[float],
                    location_confirmed: bool, people_affected: int, caller_phone: Optional[str],
                    call_id, sub_type: Optional[str], confidence: str, confidence_reasons: list[str],
                    base_severity: int) -> Incident:
    for attempt in range(4):
        number = (db.query(func.max(Incident.incident_number)).scalar() or 1000) + 1
        inc = Incident(
            incident_number=number, category=category, sub_type=sub_type, description=description,
            address_text=address_text, lat=lat, lng=lng, location_confidence=location_confidence,
            location_confirmed=location_confirmed, people_affected=people_affected or 0,
            caller_phone=caller_phone, severity=base_severity, severity_level="unrated",
            status="needs_review" if confidence == "low" else "open",
            confidence=confidence, confidence_reasons=confidence_reasons,
            source_call_id=call_id, severity_signals={}, embedding=embed_text(description),
        )
        db.add(inc)
        try:
            db.flush()
            break
        except IntegrityError:  # two calls grabbed the same number — retry with the next one
            db.rollback()
            if attempt == 3:
                raise
    db.add(IncidentReport(incident_id=inc.id, call_id=call_id, description=description,
                          people_affected=people_affected or 0, note="original report"))
    if call_id:
        call = db.get(Call, call_id)
        if call:
            call.incident_id = inc.id
    db.flush()
    return inc


# ── duplicates ──────────────────────────────────────────────────────────────
def find_duplicate(db: Session, inc: Incident, window_minutes: Optional[int] = None,
                   radius_m: Optional[int] = None, embedding: Optional[list] = None) -> Optional[dict]:
    """Best matching active incident for `inc`, or None. Does not modify anything."""
    if inc.lat is None or inc.lng is None:
        return None
    window = window_minutes or settings.DUPLICATE_WINDOW_MIN
    radius = radius_m or duplicate_radius_m(inc.category, settings.DUPLICATE_RADIUS_M)
    since = datetime.now(timezone.utc) - timedelta(minutes=window)

    candidates = (
        db.query(Incident)
        .filter(Incident.id != inc.id, Incident.status.in_(ACTIVE_STATUSES),
                Incident.merged_into_id.is_(None), Incident.lat.isnot(None))
        .all()
    )
    best = None
    for c in candidates:
        created = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=timezone.utc)
        if created < since:
            continue
        same = c.category == inc.category
        if not same and not _categories_related(c.category, inc.category):
            continue
        dist_m = haversine_km(inc.lat, inc.lng, c.lat, c.lng) * 1000
        # An older incident of a wider-radius category should still catch a nearby report.
        eff_radius = max(radius, duplicate_radius_m(c.category, settings.DUPLICATE_RADIUS_M))
        if dist_m > eff_radius:
            continue
        d_score = max(0.0, 1.0 - dist_m / eff_radius)
        c_score = 1.0 if same else 0.4
        t_score = text_similarity(inc.description, c.description, embedding or inc.embedding, c.embedding)
        score = W_DISTANCE * d_score + W_CATEGORY * c_score + W_TEXT * t_score
        if best is None or score > best["score"]:
            best = {"incident": c, "score": round(score, 3), "distance_m": round(dist_m),
                    "breakdown": {"distance": round(d_score, 2), "category": c_score, "text": round(t_score, 2)}}
    if best and best["score"] >= DUP_THRESHOLD:
        return best
    return None


def merge_into(db: Session, dup: Incident, primary: Incident, match_score: float) -> None:
    """Fold `dup` into `primary`: keep one living incident, remember every report."""
    db.execute(update(IncidentReport).where(IncidentReport.incident_id == dup.id)
               .values(incident_id=primary.id, match_score=match_score, note="merged duplicate report"))
    primary.report_count = (primary.report_count or 1) + 1
    primary.people_affected = max(primary.people_affected or 0, dup.people_affected or 0)
    dup.status = "merged"
    dup.merged_into_id = primary.id
    if dup.source_call_id:
        call = db.get(Call, dup.source_call_id)
        if call:
            call.incident_id = primary.id
    db.flush()


def log_event(db: Session, incident_id, event_type: str, actor: str = "system", payload: Optional[dict] = None) -> None:
    """Append to the incident timeline (incident_events). Never raises into a tool call."""
    try:
        db.add(IncidentEvent(incident_id=incident_id, event_type=event_type, actor=actor, payload=payload or {}))
    except Exception:
        logger.exception("could not write incident event %s", event_type)


# ── serialisation ───────────────────────────────────────────────────────────
def serialize_incident(db: Session, inc: Incident) -> dict:
    live = db.query(Call.id).filter(Call.incident_id == inc.id, Call.is_live.is_(True)).first() is not None
    return {
        "id": str(inc.id), "incident_number": inc.incident_number, "category": inc.category,
        "sub_type": inc.sub_type, "description": inc.description, "address_text": inc.address_text,
        "lat": inc.lat, "lng": inc.lng, "location_confirmed": inc.location_confirmed,
        "severity": inc.severity, "severity_level": inc.severity_level,
        "severity_explanation": inc.severity_explanation, "status": inc.status,
        "confidence": inc.confidence, "confidence_reasons": inc.confidence_reasons or [],
        "people_affected": inc.people_affected, "report_count": inc.report_count,
        "escalated": inc.escalated, "live_call": live, "summary": inc.summary,
        "merged_into_id": str(inc.merged_into_id) if inc.merged_into_id else None,
        "created_at": inc.created_at.isoformat() if inc.created_at else None,
    }


def get_incident(db: Session, incident_id) -> Optional[Incident]:
    if not incident_id:
        return None
    iid = incident_id if isinstance(incident_id, uuid.UUID) else uuid.UUID(str(incident_id))
    return db.get(Incident, iid)


def resolve_primary(db: Session, inc: Incident) -> Incident:
    """Follow merged_into links to the living incident."""
    seen = 0
    while inc.merged_into_id and seen < 10:
        nxt = db.get(Incident, inc.merged_into_id)
        if not nxt:
            break
        inc, seen = nxt, seen + 1
    return inc
