"""Geocoding + distance helpers.

Providers (settings.GEOCODER_PROVIDER):
  gazetteer  offline, seeded landmarks (emergency_instructions/question_sets/landmarks.json)
  nominatim  OpenStreetMap search (needs internet, 1 req/s policy — fine for a demo)
  hybrid     gazetteer first, Nominatim if nothing matched  (default)

ai-callcenter geocodes against the city's own ArcGIS locator; a panicked caller here gives a spoken
landmark ("near SG Mall on SG Highway"), so a fuzzy landmark gazetteer is the primary source.
"""
import json
import logging
import math
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Optional

import httpx

from app.core.config import settings
from app.utils.taxonomy import LANDMARKS_PATH

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def move_towards(lat: float, lng: float, to_lat: float, to_lng: float, step_km: float) -> tuple[float, float]:
    """Move a point `step_km` along the straight line towards a target (fleet simulator)."""
    dist = haversine_km(lat, lng, to_lat, to_lng)
    if dist <= step_km or dist == 0:
        return to_lat, to_lng
    f = step_km / dist
    return lat + (to_lat - lat) * f, lng + (to_lng - lng) * f


@lru_cache()
def _landmarks() -> list[dict]:
    with open(LANDMARKS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["landmarks"]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def _alias_score(alias: str, text: str) -> float:
    """1.0 for an exact word-boundary hit, else the best fuzzy ratio over same-length word windows."""
    alias_n = _norm(alias)
    if not alias_n:
        return 0.0
    if re.search(rf"\b{re.escape(alias_n)}\b", text):
        return 1.0
    words = text.split()
    n = len(alias_n.split())
    best = 0.0
    for i in range(0, max(1, len(words) - n + 1)):
        window = " ".join(words[i : i + n])
        best = max(best, SequenceMatcher(None, alias_n, window).ratio())
    return best


def _gazetteer(raw_text: str, landmark: Optional[str]) -> list[dict]:
    text = _norm(f"{raw_text} {landmark or ''}")
    hits = []
    for lm in _landmarks():
        score = max((_alias_score(a, text) for a in lm["aliases"] + [lm["name"]]), default=0.0)
        if score >= 0.84:
            # Prefer specific places (poi/junction) over broad roads/areas when both match.
            specificity = {"poi": 0.05, "junction": 0.04, "industrial": 0.03, "area": 0.01, "road": 0.0}
            hits.append(
                {
                    # A landmark may name its own city (the Baton Rouge drill); otherwise it is in the default city.
                    "formatted": f'{lm["name"]}, {lm.get("city") or settings.DEFAULT_CITY}',
                    "lat": lm["lat"],
                    "lng": lm["lng"],
                    "confidence": round(min(1.0, score * 0.9 + specificity.get(lm["kind"], 0)), 2),
                    "provider": "gazetteer",
                    "kind": lm["kind"],
                }
            )
    hits.sort(key=lambda h: h["confidence"], reverse=True)
    return hits


def _nominatim(raw_text: str, landmark: Optional[str], city_hint: Optional[str]) -> list[dict]:
    query = ", ".join(p for p in [raw_text, landmark, city_hint or settings.DEFAULT_CITY] if p)
    d = 0.35  # ~40 km viewbox around the city centre keeps results local
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 3,
        "countrycodes": settings.DEFAULT_COUNTRY_CODE,
        "viewbox": f"{settings.CITY_CENTER_LNG - d},{settings.CITY_CENTER_LAT + d},"
                   f"{settings.CITY_CENTER_LNG + d},{settings.CITY_CENTER_LAT - d}",
        "bounded": 1,
    }
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params=params,
            headers={"User-Agent": "ai-voice-emergency-agent-hackathon/1.0"},
            timeout=6.0,
        )
        r.raise_for_status()
        out = []
        for item in r.json():
            imp = float(item.get("importance", 0.4))
            out.append(
                {
                    "formatted": item.get("display_name", query),
                    "lat": float(item["lat"]),
                    "lng": float(item["lon"]),
                    "confidence": round(min(0.85, 0.45 + imp * 0.5), 2),
                    "provider": "nominatim",
                    "kind": item.get("type", "place"),
                }
            )
        return out
    except Exception as e:
        logger.warning("Nominatim geocode failed (%s) — continuing without it", e)
        return []


def geocode(raw_location_text: str, nearby_landmark: Optional[str] = None,
            city_hint: Optional[str] = None) -> dict:
    """Return {found, lat, lng, formatted, confidence, provider, candidates}. Never raises."""
    provider = (settings.GEOCODER_PROVIDER or "hybrid").lower()
    candidates: list[dict] = []
    if provider in ("gazetteer", "hybrid"):
        candidates = _gazetteer(raw_location_text, nearby_landmark)
    if not candidates and provider in ("nominatim", "hybrid"):
        candidates = _nominatim(raw_location_text, nearby_landmark, city_hint)
    if not candidates:
        return {"found": False, "candidates": [], "provider": provider}
    best = candidates[0]
    return {
        "found": True,
        "lat": best["lat"],
        "lng": best["lng"],
        "formatted": best["formatted"],
        "confidence": best["confidence"],
        "provider": best["provider"],
        "candidates": candidates[:3],
    }
