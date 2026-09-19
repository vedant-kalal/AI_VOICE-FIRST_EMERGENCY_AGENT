"""External map services — Overpass / Google Places (nearest department centre) and OSRM / Google Routes (road ETA) —
against mocked HTTP. Verifies request shape, parsing, caching, and graceful fallback. No network."""
import json

import httpx
import pytest

from app.core.config import settings
from app.models.resource import Facility, FacilityLookup
from app.services import facility_locator, routing_service

# Real Overpass response shape (captured from overpass-api.de for fire stations near SG Highway, Ahmedabad).
OVERPASS = {"elements": [
    {"type": "node", "id": 429410556, "lat": 23.0398116, "lon": 72.5163171,
     "tags": {"amenity": "fire_station", "name": "Bodakdev Fire Station"}},
    {"type": "way", "id": 229021982, "center": {"lat": 23.0537528, "lon": 72.499106},
     "tags": {"amenity": "fire_station", "name": "Thaltej Fire Dpt.", "phone": "+91 79 2685 0101",
              "addr:street": "SG Highway"}},
    {"type": "node", "id": 1, "tags": {"amenity": "fire_station"}},  # no coordinates -> ignored
]}
OSRM = {"code": "Ok", "routes": [{"distance": 3945.5, "duration": 384.6}]}


def _client(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def overpass_calls(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if "overpass" in str(request.url):
            return httpx.Response(200, json=OVERPASS)
        if "osrm" in str(request.url) or "/route/v1/" in str(request.url):
            return httpx.Response(200, json=OSRM)
        return httpx.Response(404)

    monkeypatch.setattr(facility_locator, "_http_client", _client(handler))
    monkeypatch.setattr(routing_service, "_http_client", _client(handler))
    monkeypatch.setattr(settings, "FACILITY_PROVIDER", "overpass")
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "osrm")
    return calls


def test_overpass_nearest_fire_station_with_road_eta_and_cache(db, overpass_calls):
    res = facility_locator.find_nearest_center(db, "fire_dept", 23.0787, 72.5064, radius_km=8)
    assert res["provider"] == "overpass" and not res["cache_hit"] and not res["fallback"]
    names = [c["name"] for c in res["centers"]]
    assert names == ["Thaltej Fire Dpt.", "Bodakdev Fire Station"]          # sorted by distance, coord-less dropped
    best = res["centers"][0]
    assert best["phone"] == "+91 79 2685 0101" and best["address"] == "SG Highway"
    assert best["eta_minutes"] == 6.4 and best["route_provider"] == "osrm"  # 384.6 s from the routing service

    # request shape: an Overpass QL query around the point, for the right OSM tag
    q = overpass_calls[0].content.decode()
    assert "amenity" in q and "fire_station" in q and "around%3A8000%2C23.0787%2C72.5064" in q.replace("(", "%28")

    # upserted into `facilities` (source=overpass, external id kept) and cached
    row = db.query(Facility).filter_by(source="overpass", external_place_id="way/229021982").one()
    assert row.department_key == "fire_dept" and row.is_synthetic is False
    assert db.query(FacilityLookup).count() == 1

    n_calls = len(overpass_calls)
    again = facility_locator.find_nearest_center(db, "fire_dept", 23.0787, 72.5064, radius_km=8)
    assert again["cache_hit"] is True
    assert not [c for c in overpass_calls[n_calls:] if "overpass" in str(c.url)]  # no second Overpass call


def test_google_places_request_and_parse(db, monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        if "places.googleapis.com" in str(request.url):
            seen["headers"], seen["body"] = dict(request.headers), json.loads(request.content)
            return httpx.Response(200, json={"places": [{
                "id": "ChIJabc", "displayName": {"text": "Sola Civil Hospital"},
                "formattedAddress": "Sola, Ahmedabad", "nationalPhoneNumber": "079 2763 0000",
                "location": {"latitude": 23.0771, "longitude": 72.518}}]})
        return httpx.Response(404)

    monkeypatch.setattr(facility_locator, "_http_client", _client(handler))
    monkeypatch.setattr(settings, "FACILITY_PROVIDER", "google_places")
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "test-key")
    res = facility_locator.find_nearest_center(db, "ems", 23.0787, 72.5064)
    assert res["centers"][0]["name"] == "Sola Civil Hospital" and res["centers"][0]["source"] == "google_places"
    assert seen["body"]["includedTypes"] == ["hospital"] and seen["body"]["rankPreference"] == "DISTANCE"
    assert seen["headers"]["x-goog-api-key"] == "test-key" and "places.location" in seen["headers"]["x-goog-fieldmask"]


def test_provider_failure_falls_back_to_seeded_centers(db, monkeypatch):
    def down(request):
        raise httpx.ConnectError("network unreachable")

    monkeypatch.setattr(facility_locator, "_http_client", _client(down))
    monkeypatch.setattr(settings, "FACILITY_PROVIDER", "overpass")
    res = facility_locator.find_nearest_center(db, "police", 23.0787, 72.5064)
    assert res["fallback"] is True and res["centers"] and "Police Station" in res["centers"][0]["name"]
    lookup = db.query(FacilityLookup).one()
    assert "ConnectError" in lookup.error and lookup.result_count == 0   # failure recorded, retried after 5 min


def test_unknown_department_and_supported_list():
    assert "error" in facility_locator.find_nearest_center(None, "nope", 0, 0)
    assert {"fire_dept", "police", "ems", "municipal_corp"} <= set(facility_locator.supported_departments())


def test_routing_osrm_google_and_failure_fallback(monkeypatch):
    def ok(request):
        if "routes.googleapis.com" in str(request.url):
            assert json.loads(request.content)["travelMode"] == "DRIVE"
            return httpx.Response(200, json={"routes": [{"duration": "384s", "distanceMeters": 3945}]})
        assert "/route/v1/driving/72.5064,23.0787;72.51,23.056" in str(request.url)   # lng,lat order!
        return httpx.Response(200, json=OSRM)

    monkeypatch.setattr(routing_service, "_http_client", _client(ok))
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "osrm")
    assert routing_service.road_route(23.0787, 72.5064, 23.056, 72.51) == {
        "distance_km": 3.95, "duration_minutes": 6.4, "provider": "osrm"}
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "google")
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "k")
    assert routing_service.road_route(23.0787, 72.5064, 23.056, 72.51)["provider"] == "google"

    monkeypatch.setattr(routing_service, "_http_client", _client(lambda r: httpx.Response(500)))
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "osrm")
    assert routing_service.road_route(23.0787, 72.5064, 23.056, 72.51) is None      # never raises
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "haversine")
    assert routing_service.road_route(1, 2, 3, 4) is None


def test_agent_tools_use_the_external_services(db, new_call, overpass_calls):
    ctx, run = new_call()
    run("geocode_location", raw_location_text="Sola")
    found = run("find_nearest_department_center", department="fire_dept", radius_km=8)
    assert found["status"] == "success" and found["provider"] == "overpass"
    assert found["nearest"]["name"] == "Thaltej Fire Dpt." and found["nearest"]["eta_minutes"] == 6.4
    assert run("find_nearest_department_center", department="nope")["status"] == "error"

    # dispatch ETA also comes from the routing service (road ETA, not a straight line)
    run("create_incident", category="medical", description="man collapsed and is not breathing near Sola")
    run("estimate_severity", signals={"unconscious_or_not_breathing": True})
    amb = run("find_nearest_resource", resource_type="ambulance")
    a = run("assign_resource", resource_id=amb["recommended"])
    assert a["eta_minutes"] == 7.4 and a["distance_km"] == 3.95                     # 6.4 min route + 1 min turn-out
