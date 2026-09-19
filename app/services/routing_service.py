"""Road routing: real driving distance and ETA from an external routing service.

  osrm    GET  {OSRM_URL}/route/v1/driving/{lng},{lat};{lng},{lat}      (public demo server or self-hosted)
  google  POST https://routes.googleapis.com/directions/v2:computeRoutes (Routes API, needs GOOGLE_MAPS_API_KEY)
  haversine  no external call (straight line x road factor) — the automatic fallback

`road_route()` NEVER raises and never blocks a dispatch: on any timeout / HTTP / parse error it returns None and
the caller keeps its haversine estimate. Routing is an enhancement to ETA accuracy, not a dependency.
"""
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def _http_client() -> httpx.Client:
    """Factory so tests can inject an httpx.MockTransport."""
    return httpx.Client(timeout=settings.EXTERNAL_HTTP_TIMEOUT_S,
                        headers={"User-Agent": "ai-voice-emergency-agent/1.0"})


def _osrm(lat1: float, lng1: float, lat2: float, lng2: float) -> Optional[dict]:
    url = f"{settings.OSRM_URL.rstrip('/')}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}"
    with _http_client() as c:
        r = c.get(url, params={"overview": "false"})
        r.raise_for_status()
        data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    route = data["routes"][0]
    return {"distance_km": round(route["distance"] / 1000.0, 2),
            "duration_minutes": round(route["duration"] / 60.0, 1), "provider": "osrm"}


def _google(lat1: float, lng1: float, lat2: float, lng2: float) -> Optional[dict]:
    if not settings.GOOGLE_MAPS_API_KEY:
        return None
    body = {
        "origin": {"location": {"latLng": {"latitude": lat1, "longitude": lng1}}},
        "destination": {"location": {"latLng": {"latitude": lat2, "longitude": lng2}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    headers = {"X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
               "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"}
    with _http_client() as c:
        r = c.post("https://routes.googleapis.com/directions/v2:computeRoutes", json=body, headers=headers)
        r.raise_for_status()
        routes = r.json().get("routes") or []
    if not routes:
        return None
    seconds = float(str(routes[0]["duration"]).rstrip("s"))  # Google returns e.g. "384s"
    return {"distance_km": round(routes[0]["distanceMeters"] / 1000.0, 2),
            "duration_minutes": round(seconds / 60.0, 1), "provider": "google"}


def road_route(lat1: float, lng1: float, lat2: float, lng2: float) -> Optional[dict]:
    """{'distance_km', 'duration_minutes', 'provider'} from the configured routing service, or None."""
    provider = (settings.ROUTING_PROVIDER or "haversine").lower()
    if provider == "haversine":
        return None
    try:
        return _google(lat1, lng1, lat2, lng2) if provider == "google" else _osrm(lat1, lng1, lat2, lng2)
    except Exception as e:
        logger.warning("routing (%s) failed: %s — falling back to straight-line estimate", provider, e)
        return None
