"""Nearest centre of ANY department, from an external place-search service.

"Where is the nearest fire station / police station / hospital / municipal office to this incident?" is
answered by a real map service instead of a hard-coded list:

  overpass       OpenStreetMap via the Overpass API — free, no key (default)
  google_places  Google Places API (New) `places:searchNearby`, needs GOOGLE_MAPS_API_KEY
  seed           only the seeded `facilities` table (offline / tests)

Pipeline: cache (facility_lookups) -> provider -> normalise -> rank by distance -> enrich the best few with a
real ROAD route/ETA (routing_service) -> upsert into `facilities` -> cache. If the provider is down or returns
nothing, the seeded facilities are used, so the agent always has an answer. Never raises.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.resource import Facility, FacilityLookup
from app.services import routing_service
from app.services.geo_service import haversine_km

logger = logging.getLogger(__name__)

# department key -> how to find its centre on each provider.
#   osm: list of (tag, value) alternatives; google: Places "includedTypes"; ftype: our facilities.type
DEPARTMENT_CENTERS: dict[str, dict] = {
    "fire_dept":           {"label": "fire station",       "ftype": "fire_station",      "osm": [("amenity", "fire_station")], "google": ["fire_station"]},
    "hazmat":              {"label": "fire station",       "ftype": "fire_station",      "osm": [("amenity", "fire_station")], "google": ["fire_station"]},
    "police":              {"label": "police station",     "ftype": "police_station",    "osm": [("amenity", "police")],       "google": ["police"]},
    "traffic_police":      {"label": "police station",     "ftype": "police_station",    "osm": [("amenity", "police")],       "google": ["police"]},
    "ems":                 {"label": "hospital",           "ftype": "hospital",          "osm": [("amenity", "hospital")],     "google": ["hospital"]},
    "hospital":            {"label": "hospital",           "ftype": "hospital",          "osm": [("amenity", "hospital")],     "google": ["hospital"]},
    "municipal_corp":      {"label": "municipal office",   "ftype": "government_office", "osm": [("amenity", "townhall")],     "google": ["city_hall", "local_government_office"]},
    "disaster_management": {"label": "emergency / disaster office", "ftype": "government_office",
                            "osm": [("emergency", "disaster_response"), ("office", "government")], "google": ["local_government_office"]},
    "utility_board":       {"label": "utility office",     "ftype": "government_office", "osm": [("office", "government")],    "google": ["local_government_office"]},
    "pollution_control":   {"label": "government office",  "ftype": "government_office", "osm": [("office", "government")],    "google": ["local_government_office"]},
    "forest_dept":         {"label": "forest office",      "ftype": "government_office", "osm": [("office", "forestry")],      "google": ["local_government_office"]},
    "supervisor":          {"label": "control room",       "ftype": "government_office", "osm": [("office", "government")],    "google": ["local_government_office"]},
}


def supported_departments() -> list[str]:
    return sorted(DEPARTMENT_CENTERS)


def _http_client() -> httpx.Client:
    """Factory so tests can inject an httpx.MockTransport."""
    return httpx.Client(timeout=settings.EXTERNAL_HTTP_TIMEOUT_S,
                        headers={"User-Agent": "ai-voice-emergency-agent/1.0"})


# ── providers ───────────────────────────────────────────────────────────────
def _overpass(dept: dict, lat: float, lng: float, radius_km: float) -> list[dict]:
    around = f"(around:{int(radius_km * 1000)},{lat},{lng})"
    clauses = "".join(f'{kind}["{k}"="{v}"]{around};' for k, v in dept["osm"] for kind in ("node", "way"))
    query = f"[out:json][timeout:10];({clauses});out center tags 40;"
    with _http_client() as c:
        r = c.post(settings.OVERPASS_URL, data={"data": query})
        r.raise_for_status()
        elements = r.json().get("elements", [])
    out = []
    for el in elements:
        tags = el.get("tags", {})
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plng = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is None or plng is None:
            continue
        addr = ", ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street"),
                                     tags.get("addr:suburb") or tags.get("addr:city")) if p)
        out.append({"name": tags.get("name") or tags.get("name:en") or f"Unnamed {dept['label']}",
                    "address": addr or None, "phone": tags.get("phone") or tags.get("contact:phone"),
                    "lat": float(plat), "lng": float(plng), "source": "overpass",
                    "external_place_id": f"{el.get('type')}/{el.get('id')}"})
    return out


def _google_places(dept: dict, lat: float, lng: float, radius_km: float) -> list[dict]:
    if not settings.GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")
    body = {
        "includedTypes": dept["google"], "maxResultCount": 10, "rankPreference": "DISTANCE",
        "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                           "radius": min(radius_km * 1000.0, 50000.0)}},
    }
    headers = {"X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
               "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,"
                                   "places.location,places.nationalPhoneNumber"}
    with _http_client() as c:
        r = c.post("https://places.googleapis.com/v1/places:searchNearby", json=body, headers=headers)
        r.raise_for_status()
        places = r.json().get("places", [])
    return [{"name": (p.get("displayName") or {}).get("text") or f"Unnamed {dept['label']}",
             "address": p.get("formattedAddress"), "phone": p.get("nationalPhoneNumber"),
             "lat": p["location"]["latitude"], "lng": p["location"]["longitude"], "source": "google_places",
             "external_place_id": p.get("id")} for p in places if p.get("location")]


# ── cache + fallback ────────────────────────────────────────────────────────
def _cache_key(provider: str, department: str, lat: float, lng: float, radius_km: float) -> str:
    raw = json.dumps([provider, department, round(lat, 3), round(lng, 3), round(radius_km, 1)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _seed_fallback(db: Session, dept: dict, lat: float, lng: float, radius_km: float) -> list[dict]:
    rows = db.query(Facility).filter(Facility.type == dept["ftype"], Facility.is_active.is_(True)).all()
    out = [{"name": f.name, "address": f.address, "phone": f.phone, "lat": f.lat, "lng": f.lng,
            "source": f.source or "seed", "external_place_id": f.external_place_id} for f in rows]
    return [p for p in out if haversine_km(lat, lng, p["lat"], p["lng"]) <= radius_km * 3]


def _upsert(db: Session, dept_key: str, dept: dict, places: list[dict]) -> None:
    """Persist externally-found places so later lookups / the dashboard can use them."""
    now = datetime.now(timezone.utc)
    for p in places:
        if p["source"] == "seed" or not p.get("external_place_id"):
            continue
        row = (db.query(Facility).filter(Facility.source == p["source"],
                                         Facility.external_place_id == p["external_place_id"]).first())
        if row is None:
            row = Facility(type=dept["ftype"], name=p["name"], lat=p["lat"], lng=p["lng"], source=p["source"],
                           external_place_id=p["external_place_id"], is_synthetic=False, capacity={})
            db.add(row)
        row.name, row.address, row.phone = p["name"], p.get("address"), p.get("phone")
        row.lat, row.lng, row.department_key, row.last_synced_at = p["lat"], p["lng"], dept_key, now
    db.flush()


def find_nearest_center(db: Session, department_key: str, lat: float, lng: float,
                        radius_km: float = 10.0, limit: int = 3) -> dict:
    """Nearest centre(s) of a department. Returns {'department', 'label', 'provider', 'centers': [...],
    'cache_hit', 'fallback'}; each center has name/address/phone/lat/lng/distance_km and, for the best ones, a
    road route (`road_distance_km`, `eta_minutes`, `route_provider`)."""
    dept = DEPARTMENT_CENTERS.get(department_key)
    if dept is None:
        return {"error": f"Unknown department '{department_key}'. Valid: {', '.join(supported_departments())}."}

    provider = (settings.FACILITY_PROVIDER or "overpass").lower()
    key = _cache_key(provider, department_key, lat, lng, radius_km)
    now = datetime.now(timezone.utc)
    places: list[dict] = []
    cache_hit = fallback = False

    cached = db.query(FacilityLookup).filter(FacilityLookup.cache_key == key).first()
    exp = None
    if cached is not None:
        exp = cached.expires_at if cached.expires_at.tzinfo else cached.expires_at.replace(tzinfo=timezone.utc)
    if cached is not None and exp > now and cached.response:
        places, cache_hit = list(cached.response), True
    elif provider != "seed":
        error = None
        try:
            places = (_google_places if provider == "google_places" else _overpass)(dept, lat, lng, radius_km)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning("facility lookup via %s failed: %s — using seeded facilities", provider, error)
        if places:
            _upsert(db, department_key, dept, places)
        row = cached or FacilityLookup(cache_key=key, provider=provider, department_key=department_key,
                                       lat=lat, lng=lng, radius_km=radius_km, expires_at=now)
        row.response, row.result_count, row.error = places, len(places), error
        row.expires_at = now + timedelta(seconds=settings.FACILITY_CACHE_TTL_S if places else 300)
        db.add(row)
        db.flush()

    if not places:
        places, fallback = _seed_fallback(db, dept, lat, lng, radius_km), True

    for p in places:
        p["distance_km"] = round(haversine_km(lat, lng, p["lat"], p["lng"]), 2)
    places.sort(key=lambda p: p["distance_km"])
    centers = places[:limit]

    for c in centers[:2]:  # real road ETA for the closest two only (keeps latency and API usage low)
        route = routing_service.road_route(lat, lng, c["lat"], c["lng"])
        if route:
            c.update(road_distance_km=route["distance_km"], eta_minutes=route["duration_minutes"],
                     route_provider=route["provider"])
    return {"department": department_key, "label": dept["label"], "provider": provider,
            "cache_hit": cache_hit, "fallback": fallback, "centers": centers,
            "search": {"lat": lat, "lng": lng, "radius_km": radius_km}}
