"""Boots the real FastAPI app (lifespan, routers, static dashboard) and exercises REST, TwiML, the live
dashboard WebSocket, human approval, and API-key protection — still with no OpenAI / Twilio / network."""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from app.core.config import settings


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_health_state_and_static_dashboard(client):
    assert client.get("/ping").json() == {"status": "ok"}
    st = client.get("/api/state").json()
    assert len(st["resources"]) >= 30 and any(f["type"] == "hospital" for f in st["facilities"])
    assert st["dispatch_mode"] == "autonomous"
    page = client.get("/dashboard/")
    assert page.status_code == 200 and "Emergency Ops Console" in page.text
    assert 'integrity="sha256-' in page.text  # Leaflet loaded with Subresource Integrity


def test_incoming_call_returns_stream_twiml(client):
    r = client.post("/incoming-call", data={"From": "+919000000001", "CallSid": "CAtest"},
                    headers={"host": "demo.example.ngrok.io"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<Connect>" in r.text and 'url="wss://demo.example.ngrok.io/media-stream-realtime"' in r.text
    assert 'name="from_number"' in r.text and "+919000000001" in r.text


def test_live_events_reach_dashboard_and_detail_has_audit_trail(client, new_call):
    ctx, run = new_call()
    with client.websocket_connect("/ws/dashboard") as ws:
        run("geocode_location", raw_location_text="Law Garden")
        run("create_incident", category="medical", description="man collapsed and is not breathing at Law Garden")
        seen = set()
        for _ in range(400):  # events published before we connected are replayed first
            msg = ws.receive_json()
            seen.add(msg["type"])
            if msg["type"] == "incident_created" and msg["data"]["id"] == ctx.incident_id:
                break
        assert "incident_created" in seen

    detail = client.get(f"/api/incidents/{ctx.incident_id}").json()
    assert detail["incident"]["category"] == "medical"
    tools = [t["tool"] for t in detail["tool_calls"]]
    assert tools[:2] == ["geocode_location", "create_incident"]
    assert all(t["duration_ms"] is not None for t in detail["tool_calls"])


def test_human_approval_flow(client, new_call, monkeypatch):
    monkeypatch.setattr(settings, "DISPATCH_MODE", "approval")
    ctx, run = new_call()
    run("geocode_location", raw_location_text="Kankaria Lake")
    run("create_incident", category="medical", description="woman collapsed and is not breathing at Kankaria")
    run("estimate_severity", signals={"unconscious_or_not_breathing": True})
    amb = run("find_nearest_resource", resource_type="ambulance")
    asg = run("assign_resource", resource_id=amb["recommended"])
    assert asg["pending_approval"] is True
    run("notify_dispatch_team", resource_id=asg["resource_id"], summary="cardiac arrest")

    ok = client.post(f"/api/assignments/{asg['assignment_id']}/approve").json()
    assert ok["status"] == "dispatched" and ok["released_notifications"] == 1
    res = {r["id"]: r for r in client.get("/api/state").json()["resources"]}
    assert res[asg["resource_id"]]["status"] in ("en_route", "on_scene")
    assert client.post(f"/api/assignments/{asg['assignment_id']}/approve").status_code == 404  # not pending anymore

    done = client.post(f"/api/incidents/{ctx.incident_id}/status", json={"status": "resolved"}).json()
    assert done["status"] == "resolved"
    res = {r["id"]: r for r in client.get("/api/state").json()["resources"]}
    assert res[asg["resource_id"]]["status"] == "available"  # unit released back to the pool


def test_review_queue_release_and_reject(client, new_call):
    ctx, run = new_call("+919000000077")
    run("create_incident", category="other", confidence="low", description="hello test")
    q = client.get("/api/review-queue").json()
    assert [i["id"] for i in q] == [ctx.incident_id]
    assert client.post(f"/api/incidents/{ctx.incident_id}/review", json={"action": "reject"}).json()["status"] == "closed"
    assert client.post(f"/api/incidents/{ctx.incident_id}/review", json={"action": "reject"}).status_code == 404


def test_dashboard_api_key_protects_rest_and_websocket(client, monkeypatch):
    monkeypatch.setattr(settings, "DASHBOARD_API_KEY", "s3cret")
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/state", headers={"X-API-Key": "s3cret"}).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/dashboard"):
            pass
    with client.websocket_connect("/ws/dashboard?key=s3cret"):
        pass


def test_dev_demo_is_off_by_default_and_runs_when_enabled(client, monkeypatch):
    assert client.post("/api/dev/demo/a").status_code == 404           # disabled -> looks like a missing route
    monkeypatch.setattr(settings, "ENABLE_DEV_ENDPOINTS", True)
    assert client.post("/api/dev/demo/zzz").status_code == 404
    assert client.post("/api/dev/demo/d?pace=0.2").status_code == 202
    import time
    from app.core.database import SessionLocal
    from app.models.incident import Incident
    deadline = time.time() + 15
    while time.time() < deadline:
        s = SessionLocal()
        try:
            inc = s.query(Incident).first()
            if inc is not None and inc.status == "needs_review":
                break
        finally:
            s.close()
        time.sleep(0.3)
    assert inc is not None and inc.status == "needs_review"           # scenario D: parked, never dispatched
