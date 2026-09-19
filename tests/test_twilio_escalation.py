"""Live-call transfer through a department escalation ladder (Twilio) — ladder logic, TwiML, attempt audit log."""
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest
import pytz
from fastapi.testclient import TestClient

import main
from app.core.config import settings
from app.models.department import CallHandoff, Department, DepartmentContact, EscalationAttempt
from app.models.incident import Escalation
from app.services import twilio_escalation as te


def test_build_ladder_cycles_and_director_last():
    contacts = [
        {"id": "d", "name": "Director", "role": "director", "phone": "+919000000009", "priority": 9},
        {"id": "b", "name": "B", "role": "on_call", "phone": "+919000000002", "priority": 2},
        {"id": "a", "name": "A", "role": "on_call", "phone": "+919000000001", "priority": 1},
    ]
    ladder = te.build_ladder(contacts, cycles=2)
    assert [(s["name"], s["cycle"]) for s in ladder] == [("A", 1), ("B", 1), ("A", 2), ("B", 2), ("Director", 1)]
    assert [s["step"] for s in ladder] == [0, 1, 2, 3, 4]
    assert te.build_ladder([], 2) == []


def test_on_duty_windows_including_overnight():
    ist = pytz.timezone("Asia/Kolkata")
    mon_2am = ist.localize(datetime(2026, 9, 21, 2, 0))       # a Monday
    mon_noon = ist.localize(datetime(2026, 9, 21, 12, 0))
    night_shift = {"days": [6], "start": "18:00", "end": "07:00"}   # Sunday night shift runs into Monday morning
    assert te.is_on_duty(None, mon_noon) is True                      # no window = 24x7
    assert te.is_on_duty(night_shift, mon_2am) is True                # after midnight belongs to Sunday's shift
    assert te.is_on_duty(night_shift, mon_noon) is False
    assert te.is_on_duty({"days": [0], "start": "09:00", "end": "17:00"}, mon_noon) is True
    assert te.is_on_duty({"days": [1], "start": "09:00", "end": "17:00"}, mon_noon) is False


def test_resolve_ladder_skips_off_duty_invalid_and_falls_back_to_supervisor(db):
    fire = db.query(Department).filter_by(key="fire_dept").one()
    fire.contacts[0].phone = "not-a-number"                        # invalid E.164 never reaches TwiML
    fire.contacts[1].is_active = False
    db.commit()
    dept, ladder = te.resolve_ladder(db, "fire_dept")
    assert dept.key == "fire_dept" and [s["role"] for s in ladder] == ["director"]

    for c in fire.contacts:
        c.is_active = False
    db.commit()
    dept, ladder = te.resolve_ladder(db, "fire_dept")
    assert dept.key == "supervisor" and len(ladder) == 5            # a transfer must always reach someone


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _handoff_via_tool(new_call, department="fire_dept"):
    ctx, run = new_call("+919812345678")
    run("geocode_location", raw_location_text="Thaltej")
    run("create_incident", category="fire", description="factory fire with people inside near Thaltej")
    out = run("transfer_to_human_operator", department=department, context_summary="factory fire, 3 trapped, "
                                                                                    "2 engines dispatched")
    return ctx, out["_effects"]["transfer_call"]["handoff_id"]


def _dial(el):
    return el.find("Dial"), (el.find("Dial").find("Number") if el.find("Dial") is not None else None)


def test_full_ladder_no_answer_until_exhausted(client, db, new_call):
    ctx, hid = _handoff_via_tool(new_call)
    q = lambda step: f"?handoff_id={hid}&step={step}"

    # rung 0: SMS is recorded, TwiML dials the first on-call contact with a whisper + status callback
    xml = client.post("/twilio/escalation/dial" + q(0)).text
    root = ET.fromstring(xml)
    dial, number = _dial(root)
    assert root.find("Say") is not None                              # "Connecting you to ..." (step 0 only)
    assert dial.get("timeout") == "30" and dial.get("answerOnBridge") == "true"
    assert dial.get("action").endswith(f"/twilio/escalation/result?handoff_id={hid}&step=0")
    assert re.fullmatch(r"\+\d{8,15}", number.text)
    assert "/twilio/escalation/whisper" in number.get("url") and "/leg-status" in number.get("statusCallback")
    assert number.get("statusCallbackEvent") == "initiated ringing answered completed"

    # leg status callbacks land in the attempt's audit trail
    client.post("/twilio/escalation/leg-status" + q(0), data={"CallStatus": "ringing", "CallSid": "CAleg0"})
    a0 = db.query(EscalationAttempt).filter_by(step_index=0).one()
    db.refresh(a0)
    assert a0.dial_status == "ringing" and a0.dial_call_sid == "CAleg0" and a0.sms_status == "simulated"

    # nobody answers rungs 0..3 -> each result redirects to the next rung on the SAME live call
    for step in range(4):
        r = client.post("/twilio/escalation/result" + q(step), data={"DialCallStatus": "no-answer"}).text
        target = ET.fromstring(r).find("Redirect").text
        assert target.endswith(f"/twilio/escalation/dial?handoff_id={hid}&step={step + 1}")
        d = ET.fromstring(client.post("/twilio/escalation/dial" + q(step + 1)).text).find("Dial")
        assert d is not None
    # step 4 is the director; no answer there -> apology + hangup, handoff exhausted, supervisor alerted
    end = ET.fromstring(client.post("/twilio/escalation/result" + q(4), data={"DialCallStatus": "busy"}).text)
    assert end.find("Hangup") is not None and "could not reach" in end.find("Say").text
    db.expire_all()   # the app wrote through other sessions; drop this session's cached rows
    h = db.get(CallHandoff, uuid.UUID(hid))
    assert h.status == "exhausted" and h.ended_at is not None
    attempts = db.query(EscalationAttempt).filter_by(handoff_id=h.id).order_by(EscalationAttempt.step_index).all()
    assert [(a.step_index, a.cycle, a.dial_status) for a in attempts] == [
        (0, 1, "no-answer"), (1, 1, "no-answer"), (2, 2, "no-answer"), (3, 2, "no-answer"), (4, 1, "busy")]
    assert attempts[0].phone == attempts[2].phone and attempts[0].id != attempts[2].id   # repeat cycle = NEW row
    assert any(e.source == "system" for e in db.query(Escalation).all())            # supervisor was alerted


def test_answered_ends_the_ladder(client, db, new_call):
    ctx, hid = _handoff_via_tool(new_call, "police")
    q = lambda step: f"?handoff_id={hid}&step={step}"
    client.post("/twilio/escalation/dial" + q(0))
    client.post("/twilio/escalation/dial" + q(1))
    r = client.post("/twilio/escalation/result" + q(1),
                    data={"DialCallStatus": "completed", "DialCallDuration": "184", "DialCallSid": "CAans"}).text
    assert ET.fromstring(r).find("Redirect") is None and ET.fromstring(r).find("Say") is None
    h = db.get(CallHandoff, uuid.UUID(hid))
    db.refresh(h)
    assert h.status == "answered" and h.answered_contact_id is not None
    a = db.query(EscalationAttempt).filter_by(handoff_id=h.id, step_index=1).one()
    assert a.answered is True and a.duration_seconds == 184 and a.dial_call_sid == "CAans"
    # a late duplicate callback for a finished handoff does nothing
    again = client.post("/twilio/escalation/result" + q(1), data={"DialCallStatus": "no-answer"}).text
    assert ET.fromstring(again).find("Hangup") is not None


def test_whisper_carries_incident_context_and_is_sanitised(client, db, new_call):
    ctx, hid = _handoff_via_tool(new_call)
    xml = client.post(f"/twilio/escalation/whisper?handoff_id={hid}&step=0").text
    text = ET.fromstring(xml).find("Say").text
    assert "Incident" in text and "fire" in text and "factory fire" in text
    h = db.get(CallHandoff, uuid.UUID(hid))
    h.reason = "<Dial>+1900</Dial> evil"                              # markup in the context must not survive
    db.commit()
    assert "<" not in ET.fromstring(client.post(f"/twilio/escalation/whisper?handoff_id={hid}&step=0").text).find("Say").text


def test_unknown_or_malformed_handoff_is_404_and_signature_enforced(client, monkeypatch):
    assert client.post("/twilio/escalation/dial?handoff_id=nope&step=0").status_code == 404
    assert client.post(f"/twilio/escalation/dial?handoff_id={uuid.uuid4()}&step=0").status_code == 404
    monkeypatch.setattr(settings, "TWILIO_VALIDATE_SIGNATURE", True)
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "secret")
    assert client.post(f"/twilio/escalation/dial?handoff_id={uuid.uuid4()}&step=0").status_code == 403


def test_begin_transfer_redirects_live_call_via_twilio_rest(db, new_call, monkeypatch):
    ctx, hid = _handoff_via_tool(new_call)
    seen = {}

    class FakeCalls:
        def __call__(self, sid):
            seen["sid"] = sid
            return self

        def update(self, **kw):
            seen.update(kw)

    class FakeClient:
        calls = FakeCalls()

    monkeypatch.setattr(te, "_twilio_client", lambda: FakeClient())
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "PUBLIC_HOST", "abc.ngrok.app")
    h = db.get(CallHandoff, uuid.UUID(hid))
    h.call_sid = "CA1234567890"                                       # not a TEST/DEMO sid
    db.commit()
    out = te.begin_transfer(db, h.id)
    assert out["status"] == "dialing" and seen["sid"] == "CA1234567890"
    assert seen["url"] == f"https://abc.ngrok.app/twilio/escalation/dial?handoff_id={hid}&step=0"
    assert seen["method"] == "POST" and h.status == "dialing"


def test_begin_transfer_is_simulated_without_twilio(db, new_call, monkeypatch):
    ctx, hid = _handoff_via_tool(new_call)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", None)
    assert te.begin_transfer(db, uuid.UUID(hid))["status"] == "simulated"
