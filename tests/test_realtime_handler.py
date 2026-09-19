"""End-to-end test of the realtime call handler with NO paid services.

A fake OpenAI Realtime server (local websockets server) plays a scripted model; the test plays Twilio
(TestClient WebSocket). Verifies the plumbing that is otherwise only testable on a real phone call:

  * session.update once at start with all 13 tools; later session.update carries instructions ONLY
  * PARALLEL tool calls in one response -> every output sent, exactly ONE follow-up response.create
  * create_incident -> live prompt swap to the ACTIVE INCIDENT block
  * audio forwarded to Twilio, marks sent + echoed, barge-in => Twilio `clear` + response.cancel
  * end_call -> hang-up (`stop`), then the call/transcript/incident/audit rows are persisted
"""
import asyncio
import base64
import json
import threading
import time

import pytest
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import main
from app.api.v1.endpoints import openai_realtime_emergency as handler
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.call import AgentToolCall, Call, CallTranscript
from app.models.incident import Incident


class FakeOpenAI:
    """Scripted stand-in for wss://api.openai.com/v1/realtime."""

    def __init__(self, mode: str = "main"):
        self.mode = mode
        self.received: list[dict] = []
        self.step = 0
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        self.port = None
        threading.Thread(target=self._run, daemon=True).start()
        assert self.ready.wait(5)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def boot():
            server = await websockets.serve(self._handle, "127.0.0.1", 0)
            self.port = server.sockets[0].getsockname()[1]
            self.ready.set()
            await asyncio.Future()

        self.loop.run_until_complete(boot())

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    async def _send(self, ws, **event):
        await ws.send(json.dumps(event))

    def _fc(self, item_id, call_id, name, args):
        return [
            {"type": "response.output_item.added", "item": {"id": item_id, "type": "function_call",
                                                            "name": name, "call_id": call_id}},
            {"type": "response.function_call_arguments.done", "item_id": item_id, "call_id": call_id,
             "name": name, "arguments": json.dumps(args)},
        ]

    async def _handle(self, ws):
        async for raw in ws:
            m = json.loads(raw)
            self.received.append(m)
            t = m["type"]

            # "context" mode: creation, then a critical severity that auto-escalates, then a repeat create_incident
            if self.mode == "context":
                if t == "input_audio_buffer.append" and self.step == 0:
                    self.step = 1
                    await self._send(ws, type="response.created", response={"id": "r1"})
                    calls = [
                        ("geocode_location", {"raw_location_text": "Kankaria Lake"}),
                        ("create_incident", {"category": "medical", "confidence": "high",
                                             "description": "man collapsed at the lake and is not breathing"}),
                        ("estimate_severity", {"signals": {"unconscious_or_not_breathing": True}}),
                        ("create_incident", {"category": "medical", "confidence": "high",
                                             "description": "man collapsed at the lake and is not breathing"}),
                    ]
                    for i, (name, args) in enumerate(calls):
                        for ev in self._fc(f"ic{i}", f"cc{i}", name, args):
                            await ws.send(json.dumps(ev))
                    await self._send(ws, type="response.done", response={"id": "r1"})
                continue

            # "transfer" mode: the agent says the hold line, then calls transfer_to_human_operator.
            if self.mode == "transfer":
                if t == "input_audio_buffer.append" and self.step == 0:
                    self.step = 1
                    await self._send(ws, type="response.created", response={"id": "r1"})
                    await self._send(ws, type="response.output_audio.delta", item_id="msg_hold",
                                     delta=base64.b64encode(b"\xff" * 160).decode())
                    await self._send(ws, type="response.output_audio_transcript.done",
                                     transcript="I'm connecting you to the fire department now. Please stay on the line.")
                    await self._send(ws, type="response.output_item.added",
                                     item={"id": "i9", "type": "function_call", "name": "transfer_to_human_operator",
                                           "call_id": "c9"})
                    await self._send(ws, type="response.function_call_arguments.delta", item_id="i9", delta="{")
                    await self._send(ws, type="response.function_call_arguments.done", item_id="i9", call_id="c9",
                                     name="transfer_to_human_operator",
                                     arguments=json.dumps({"department": "fire_dept",
                                                           "context_summary": "factory fire, three people trapped"}))
                    await self._send(ws, type="response.done", response={"id": "r1"})
                continue

            # Turn 1 — the first caller audio frame: THREE parallel tool calls in ONE response.
            if t == "input_audio_buffer.append" and self.step == 0:
                self.step = 1
                await self._send(ws, type="response.created", response={"id": "r1"})
                for ev in (self._fc("i1", "c1", "geocode_location", {"raw_location_text": "Law Garden"})
                           + self._fc("i2", "c2", "create_incident",
                                      {"category": "medical", "confidence": "high",
                                       "description": "man collapsed and is not breathing at Law Garden"})
                           + self._fc("i3", "c3", "give_caller_safety_instructions",
                                      {"category": "medical", "situation_detail": "man not breathing"})):
                    await ws.send(json.dumps(ev))
                await self._send(ws, type="response.done", response={"id": "r1"})

            # Turn 2 — triggered by the handler's single follow-up response.create.
            elif t == "response.create" and self.step == 1 and any(
                    x["type"] == "conversation.item.create" and x["item"]["type"] == "function_call_output"
                    for x in self.received):
                self.step = 2
                await self._send(ws, type="response.created", response={"id": "r2"})
                await self._send(ws, type="response.output_audio.delta", item_id="msg_r2",
                                 delta=base64.b64encode(b"\xff" * 160).decode())
                # The caller talks over the agent WHILE it is still speaking (barge-in), then finishes.
                await asyncio.sleep(0.2)
                await self._send(ws, type="input_audio_buffer.speech_started")
                await self._send(ws, type="conversation.item.input_audio_transcription.completed",
                                 transcript="okay I am doing it now please hurry up")
                await self._send(ws, type="input_audio_buffer.speech_stopped")
                await self._send(ws, type="response.output_audio_transcript.done",
                                 transcript="Help is coming. Start chest compressions now.")
                await self._send(ws, type="response.done", response={"id": "r2"})
                await asyncio.sleep(0.3)

                # Turn 3 — the agent says goodbye and ends the call.
                await asyncio.sleep(0.5)
                await self._send(ws, type="response.created", response={"id": "r3"})
                await self._send(ws, type="response.output_audio.delta", item_id="msg_r3",
                                 delta=base64.b64encode(b"\xff" * 160).decode())
                await self._send(ws, type="response.output_audio_transcript.done",
                                 transcript="Help is on the way. Stay safe. Goodbye.")
                await self._send(ws, type="response.output_item.added",
                                 item={"id": "i4", "type": "function_call", "name": "end_call", "call_id": "c4"})
                await self._send(ws, type="response.function_call_arguments.delta", item_id="i4", delta="{")
                await self._send(ws, type="response.function_call_arguments.done", item_id="i4", call_id="c4",
                                 name="end_call",
                                 arguments=json.dumps({"closing_message": "Help is on the way. Stay safe. Goodbye."}))
                await self._send(ws, type="response.done", response={"id": "r3"})


@pytest.fixture
def fake_openai(monkeypatch):
    fake = FakeOpenAI()
    monkeypatch.setattr(handler, "OPENAI_REALTIME_URL", fake.url)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key-not-used")

    async def stub_summary(messages, incident, tools):  # never call OpenAI from a test
        return {"summary": "STUB SUMMARY: cardiac arrest at Law Garden, ambulance requested.",
                "caller_language": "English"}

    monkeypatch.setattr(handler, "generate_call_summary", stub_summary)
    return fake


def test_full_call_through_fake_openai(fake_openai):
    twilio_events = []
    with TestClient(main.app) as client:
        with client.websocket_connect("/media-stream-realtime") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "start": {
                "streamSid": "MZ-test", "callSid": "CAhandlertest",
                "customParameters": {"from_number": "+919812345678"}}})
            ws.send_json({"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
            while True:
                try:
                    msg = ws.receive_json()
                except WebSocketDisconnect:
                    break
                twilio_events.append(msg)
                if msg["event"] == "mark":  # play the role of Twilio: acknowledge that audio finished
                    ws.send_json({"event": "mark", "mark": {"name": msg["mark"]["name"]}})
                if msg["event"] == "stop":
                    break

        # The hang-up (`stop`) goes out BEFORE the handler finishes its post-call work (summary + DB
        # writes) — wait for it while the app is still running, exactly as production keeps running.
        deadline = time.time() + 10
        while time.time() < deadline:
            probe = SessionLocal()
            try:
                row = probe.query(Call).filter(Call.call_sid == "CAhandlertest").first()
                if row is not None and row.status != "in_progress":
                    break
            finally:
                probe.close()
            time.sleep(0.2)

    got = fake_openai.received
    types = [m["type"] for m in got]

    # 1) session configured once, with all tools; the model is asked to say the greeting (no cached clip).
    first_update = next(m for m in got if m["type"] == "session.update")
    assert len(first_update["session"]["tools"]) == 13
    assert first_update["session"]["audio"]["input"]["turn_detection"]["type"] == "semantic_vad"
    assert "RULE 01" in first_update["session"]["instructions"]

    # 2) three parallel tool calls -> three outputs, and only ONE follow-up response.create for them.
    outputs = [m["item"] for m in got if m["type"] == "conversation.item.create"
               and m["item"]["type"] == "function_call_output"]
    assert [o["call_id"] for o in outputs][:3] == ["c1", "c2", "c3"]
    assert json.loads(outputs[0]["output"])["found"] is True                      # geocode worked
    assert "incident_id" in json.loads(outputs[1]["output"])                      # incident registered
    assert "_effects" not in outputs[1]["output"]                                # internal key never leaks
    assert json.loads(outputs[2]["output"])["protocol"] == "cpr"                 # 'not breathing' -> CPR steps
    first_output_idx = types.index("conversation.item.create", 1)
    creates_after_tools = [i for i, t in enumerate(types) if t == "response.create" and i > first_output_idx]
    assert len(creates_after_tools) == 1, "parallel tool calls must yield exactly one response.create"

    # 3) live prompt swap: instructions only (tools untouched), category block present.
    swaps = [m for m in got if m["type"] == "session.update" and m is not first_update]
    assert len(swaps) == 1 and "tools" not in swaps[0]["session"]
    swap_prompt = swaps[0]["session"]["instructions"]
    assert "ACTIVE INCIDENT: Medical Emergency" in swap_prompt
    assert "DEPARTMENT: Emergency Medical Services (EMS) (ems)" in swap_prompt      # its own department file
    assert "(fire_dept)" not in swap_prompt and "RULE 01" in swap_prompt            # only relevant departments; globals kept

    # 4) audio reached Twilio, barge-in cleared it and cancelled the model's response.
    kinds = [e["event"] for e in twilio_events]
    assert "media" in kinds and "mark" in kinds and "clear" in kinds
    assert "response.cancel" in types
    # proper barge-in: the interrupted assistant turn is truncated to what the caller actually heard
    trunc = [m for m in got if m["type"] == "conversation.item.truncate"]
    assert len(trunc) == 1 and trunc[0]["item_id"] == "msg_r2" and trunc[0]["content_index"] == 0
    assert 0 < trunc[0]["audio_end_ms"] <= 20            # we had sent 160 mu-law bytes = 20 ms of audio
    assert kinds[-1] == "stop" or "stop" in kinds  # hang-up after end_call

    # 5) everything was persisted.
    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.call_sid == "CAhandlertest").one()
        assert call.status == "completed" and call.is_live is False and call.user_spoken is True
        assert call.summary.startswith("STUB SUMMARY") and call.duration_seconds is not None
        said = [(t.role, t.content) for t in db.query(CallTranscript).filter_by(call_id=call.id)
                .order_by(CallTranscript.seq)]
        assert ("caller", "okay I am doing it now please hurry up") in said
        assert any(r == "agent" and "Help is coming" in c for r, c in said)
        tools = [t.tool_name for t in db.query(AgentToolCall).filter_by(call_id=call.id)
                 .order_by(AgentToolCall.created_at)]
        assert tools == ["geocode_location", "create_incident", "give_caller_safety_instructions"]
        inc = db.query(Incident).one()
        assert inc.category == "medical" and call.incident_id == inc.id
        assert inc.summary.startswith("STUB SUMMARY")
    finally:
        db.close()


def test_handler_refuses_calls_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/media-stream-realtime") as ws:
                ws.receive_json()


def test_correct_transcript_tracks_language_without_rewriting():
    ct = handler.correct_transcript
    text = "there has been a big accident near the mall"
    assert ct(text, "unknown") == (text, "en")
    hi = "मेरे पास एक बहुत बड़ा हादसा हुआ है"
    assert ct(hi, "unknown")[1] == "hi" and ct(hi, "unknown")[0] == hi
    gu = "અહીં એક મોટો અકસ્માત થયો છે"
    assert ct(gu, "unknown")[1] == "gu"
    assert ct("ok", "hi") == ("ok", "hi")  # short utterance never flips a locked language
    assert handler.number_normalise("+91 98123 45678") == "9812345678"


def test_convert_to_mulaw_needs_no_ffmpeg():
    import io
    import math
    import struct
    import wave

    rate, secs = 24000, 0.5  # OpenAI TTS returns 24 kHz 16-bit mono WAV
    samples = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
                       for i in range(int(rate * secs)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples)
    out = asyncio.run(handler.convert_to_mulaw(buf.getvalue()))
    assert abs(len(out) - int(8000 * secs)) <= 8      # 8 kHz, one byte per mu-law sample
    assert handler._twilio_chunk_has_energy(base64.b64encode(out[:160]).decode()) is True
    assert handler._twilio_chunk_has_energy(base64.b64encode(b"\xff" * 160).decode()) is False  # mu-law silence


def test_transfer_to_department_escalation_ladder_via_twilio(monkeypatch):
    """The agent says the hold line, calls transfer_to_human_operator(department=fire_dept); the handler waits for the
    hold line to finish, then asks Twilio to redirect the LIVE call to our escalation-ladder TwiML."""
    import uuid as _uuid

    from app.models.department import CallHandoff
    from app.services import twilio_escalation as te

    fake = FakeOpenAI(mode="transfer")
    monkeypatch.setattr(handler, "OPENAI_REALTIME_URL", fake.url)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key-not-used")

    async def stub_summary(messages, incident, tools):
        return {"summary": "STUB: transferred to fire department", "caller_language": "English"}

    monkeypatch.setattr(handler, "generate_call_summary", stub_summary)

    twilio_rest = {}
    holder = {}

    class FakeCalls:
        def __call__(self, sid):
            twilio_rest["sid"] = sid
            return self

        def update(self, **kw):
            twilio_rest.update(kw)
            # Twilio pulls the caller's leg out of the media stream -> our WebSocket gets a `stop`.
            threading.Timer(0.3, lambda: holder["ws"].send_json({"event": "stop"})).start()

    class FakeClient:
        calls = FakeCalls()

    monkeypatch.setattr(te, "_twilio_client", lambda: FakeClient())
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "PUBLIC_HOST", "abc.ngrok.app")

    with TestClient(main.app) as client:
        with client.websocket_connect("/media-stream-realtime") as ws:
            holder["ws"] = ws
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "start": {"streamSid": "MZ-x", "callSid": "CAtransfertest",
                                                       "customParameters": {"from_number": "+919812345678"}}})
            ws.send_json({"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
            events = []
            while True:
                try:
                    msg = ws.receive_json()
                except WebSocketDisconnect:
                    break
                events.append(msg)
                if msg["event"] == "mark":
                    ws.send_json({"event": "mark", "mark": {"name": msg["mark"]["name"]}})
                if msg["event"] == "stop":
                    break

        deadline = time.time() + 10
        while time.time() < deadline:
            probe = SessionLocal()
            try:
                row = probe.query(Call).filter(Call.call_sid == "CAtransfertest").first()
                if row is not None and row.status != "in_progress":
                    break
            finally:
                probe.close()
            time.sleep(0.2)

    # Twilio was asked to redirect the live call into the ladder, on the right call and URL.
    assert twilio_rest["sid"] == "CAtransfertest" and twilio_rest["method"] == "POST"
    assert twilio_rest["url"].startswith("https://abc.ngrok.app/twilio/escalation/dial?handoff_id=")
    assert twilio_rest["url"].endswith("&step=0")

    # The model got a tool output, but NO follow-up response.create (the call left the agent).
    types = [m["type"] for m in fake.received]
    assert any(m["type"] == "conversation.item.create" and m["item"].get("call_id") == "c9" for m in fake.received)
    assert types.count("response.create") == 1                  # only the initial greeting
    assert not any(e["event"] == "stop" for e in events)        # we did NOT hang up: Twilio owns the leg now
    assert any(e["event"] == "media" for e in events)           # but the hold line WAS played to the caller

    db = SessionLocal()
    try:
        call = db.query(Call).filter(Call.call_sid == "CAtransfertest").one()
        assert call.status == "transferred_to_human" and call.transferred_to_human is True and call.is_live is False
        h = db.query(CallHandoff).filter(CallHandoff.call_sid == "CAtransfertest").one()
        assert h.status == "dialing" and h.total_steps == 5
        assert str(h.id) in twilio_rest["url"] and h.reason.startswith("factory fire")
    finally:
        db.close()


def test_session_updates_are_instruction_only_ordered_and_deduplicated(monkeypatch):
    """create_incident loads the category's departments; the critical-severity escalation adds the supervisor desk; the
    repeated create_incident changes nothing -> exactly TWO instruction-only updates, both sent BEFORE the model's
    follow-up response.create so its very next words already use the new context."""
    fake = FakeOpenAI(mode="context")
    monkeypatch.setattr(handler, "OPENAI_REALTIME_URL", fake.url)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key-not-used")

    async def stub_summary(messages, incident, tools):
        return {"summary": "STUB", "caller_language": "English"}

    monkeypatch.setattr(handler, "generate_call_summary", stub_summary)

    with TestClient(main.app) as client:
        with client.websocket_connect("/media-stream-realtime") as ws:
            ws.send_json({"event": "connected"})
            ws.send_json({"event": "start", "start": {"streamSid": "MZ-c", "callSid": "CAcontexttest",
                                                       "customParameters": {"from_number": "+919812345678"}}})
            ws.send_json({"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}})
            deadline = time.time() + 6
            while time.time() < deadline and sum(1 for m in fake.received if m["type"] == "response.create") < 2:
                time.sleep(0.1)
            ws.send_json({"event": "stop"})
            try:
                while True:
                    ws.receive_json()
            except WebSocketDisconnect:
                pass

    got = fake.received
    updates = [(i, m) for i, m in enumerate(got) if m["type"] == "session.update"]
    assert len(updates) == 3                                           # 1 initial (tools) + exactly 2 instruction swaps
    initial, first, second = updates[0][1], updates[1][1], updates[2][1]
    assert len(initial["session"]["tools"]) == 13
    for _, swap in updates[1:]:
        assert set(swap["session"]) == {"type", "instructions"}       # instructions ONLY — tools/audio never resent
    p1, p2 = first["session"]["instructions"], second["session"]["instructions"]
    assert "(ems)" in p1 and "(supervisor)" not in p1 and "ACTIVE INCIDENT: Medical Emergency" in p1
    assert "(ems)" in p2 and "(supervisor)" in p2                     # escalation added the supervisor desk
    follow_up = max(i for i, m in enumerate(got) if m["type"] == "response.create")
    assert updates[1][0] < updates[2][0] < follow_up                  # both swaps land before the follow-up response
