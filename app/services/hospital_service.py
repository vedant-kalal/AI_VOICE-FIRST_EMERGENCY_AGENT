"""Hospital routing: nearest hospitals that can actually take the patient (PDF `get_hospital_capacity`)."""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.resource import Facility
from app.services.geo_service import haversine_km
from app.services.resource_service import eta_minutes

# specialty -> (capacity flag, is_bed_count)
SPECIALTIES = {
    "icu": ("available_icu", True),
    "trauma": ("trauma", False),
    "burn_unit": ("burn_unit", False),
    "toxicology": ("toxicology", False),
    "cardiac": ("cardiac", False),
}


def _status(cap: dict) -> str:
    beds = int(cap.get("available_beds", 0) or 0)
    if beds <= 0:
        return "red"      # at capacity
    return "amber" if beds <= 3 else "green"


def _can_treat(cap: dict, specialty: Optional[str]) -> bool:
    if int(cap.get("available_beds", 0) or 0) <= 0:
        return False
    if not specialty or specialty == "general":
        return True
    flag = SPECIALTIES.get(specialty)
    if not flag:
        return True
    key, is_count = flag
    value = cap.get(key)
    return bool(value and int(value) > 0) if is_count else bool(value)


def hospital_capacity(db: Session, lat: float, lng: float, required_specialty: Optional[str] = None,
                      limit: int = 3) -> dict:
    hospitals = db.query(Facility).filter(Facility.type == "hospital", Facility.is_active.is_(True)).all()
    rows = []
    for h in hospitals:
        cap = h.capacity or {}
        dist = haversine_km(lat, lng, h.lat, h.lng)
        rows.append({
            "facility_id": str(h.id), "name": h.name, "lat": h.lat, "lng": h.lng,
            "distance_km": round(dist, 2), "ambulance_eta_minutes": eta_minutes("ambulance", dist),
            "available_beds": cap.get("available_beds", 0), "available_icu": cap.get("available_icu", 0),
            "trauma": bool(cap.get("trauma")), "burn_unit": bool(cap.get("burn_unit")),
            "toxicology": bool(cap.get("toxicology")), "cardiac": bool(cap.get("cardiac")),
            "capacity_status": _status(cap), "can_treat": _can_treat(cap, required_specialty),
        })
    rows.sort(key=lambda r: (not r["can_treat"], r["distance_km"]))
    best = next((r for r in rows if r["can_treat"]), None)
    return {
        "required_specialty": required_specialty or "general",
        "recommended": best,
        "hospitals": rows[:limit],
        "note": None if best else "No nearby hospital reports capacity for this specialty — "
                                  "tell the dispatcher; do not promise a specific hospital to the caller.",
    }
