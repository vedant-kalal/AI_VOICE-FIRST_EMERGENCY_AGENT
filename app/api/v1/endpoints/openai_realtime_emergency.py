"""
app/api/v1/endpoints/openai_realtime_emergency.py

Emergency-line realtime handler — OpenAI Realtime API (gpt-realtime) bridged to a Twilio Media Stream.

Adapted from ai-callcenter's openai_realtime_311.py and keeps its proven architecture:
  * ONE long-lived Realtime session per call; tools are sent ONCE at session start (Option A) and
    never resent — only `instructions` change afterwards (session.update is a partial merge).
  * Twilio <-> OpenAI audio pumps, semantic_vad turn detection, barge-in (`clear` + response.cancel),
    Twilio marks to know when audio finished playing, silence monitor, cached greeting/goodbye clips,
    the shared end_call() hang-up helper.
  * The live prompt swap: the moment `create_incident` registers a category, the handler pushes
    dispatcher + ACTIVE INCIDENT block (the analogue of 311's switch_department).

Deliberate differences from ai-callcenter:
  * Tool LOGIC lives in app/services/tool_executor.py (shared with the text harness + tests) instead of
    a 3,000-line inline if/elif chain. This file only does the call plumbing.
  * Tools run in a worker thread (asyncio.to_thread) so DB / geocoder latency never stalls audio.
  * PARALLEL tool calls: a single response may contain several function calls (find_nearest_resource +
    give_caller_safety_instructions ...). Each call's output is sent as it finishes, and ONE
    `response.create` is sent when the response is done — not one per call.
  * Barge-in is locked only around terminal tools (end_call / transfer_to_human_operator), not around
    every tool call, so a panicked caller can always interrupt the agent.
  * Every utterance and tool call is streamed live to the ops dashboard (app/services/dashboard_hub.py).
"""

import asyncio
import audioop
import base64
import io
import json
import logging
import os
import re
import ssl
import time
import traceback
import uuid
import wave
from datetime import datetime, timezone

import websockets
from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect
from openai import AsyncOpenAI
from starlette.websockets import WebSocketState
from twilio.rest import Client

from app.core.config import settings
from app.core.database import session_scope
from app.models.call import Call, CallTranscript
from app.services import incident_service, twilio_escalation
from app.services.call_summary import generate_call_summary
from app.services.dashboard_hub import hub
from app.services.tool_executor import TERMINAL_TOOLS, ToolContext, execute_tool
from app.utils.prompts_realtime_emergency import (
    GLOBAL_TOOLS, GOODBYE_TEXT, GREETING_TEXT, HANDOFF_TEXT, end_call_messages,
)
from app.utils.realtime_session import (
    PromptState, build_initial_session, instructions_update_event, session_update_event,
)

logger = logging.getLogger(__name__)
router = APIRouter()

VOICE_NAME = settings.VOICE_NAME
# Voice for the standalone TTS endpoint (cached clips). It does NOT accept realtime-only voices like
# "marin", so a fixed TTS-valid voice is used here (same note as ai-callcenter).
TTS_VOICE = "shimmer"

twilio_client = (
    Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN else None
)
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None

OPENAI_REALTIME_MODEL = settings.OPENAI_REALTIME_MODEL
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_REALTIME_MODEL}"

# Cached audio (built once at startup as .mulaw files in app/static/). None until generated.
CACHED_GREETING = None
CACHED_GOODBYE = None
CACHED_HANDOFF = None
_CLIPS = {
    "greeting": ("greeting_emergency.mulaw", GREETING_TEXT),
    "goodbye": ("goodbye_emergency.mulaw", GOODBYE_TEXT),
    "handoff": ("handoff_human_operator.mulaw", HANDOFF_TEXT),
}

# Strong references to in-flight post-call tasks (asyncio only keeps weak ones).
_BACKGROUND_TASKS: set = set()

_LATIN = re.compile(r"[a-zA-Z]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_GUJARATI = re.compile(r"[઀-૿]")
_ARABIC = re.compile(r"[؀-ۿ]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def correct_transcript(transcript: str, session_language: str, caller_number: str = "") -> tuple[str, str]:
    """Track the caller's script/language from the input transcription (for logging + dashboard only).

    The model itself handles language switching (global Rule 10); this never rewrites the text. It locks
    a coarse language hint on the first substantive (>=5 word) utterance so a later ASR misfire in another
    script can be logged, as ai-callcenter's version does."""
    if not transcript or not transcript.strip():
        return transcript, session_language
    words = transcript.split()
    if len(words) < 5 and session_language != "unknown":
        return transcript, session_language

    def frac(rx):
        return sum(1 for w in words if rx.search(w)) / len(words)

    if session_language == "unknown" and len(words) >= 3:
        for lang, rx in (("hi", _DEVANAGARI), ("gu", _GUJARATI), ("ur", _ARABIC)):
            if frac(rx) > 0.5:
                return transcript, lang
        if frac(_LATIN) >= 0.5:
            return transcript, "en"
        return transcript, session_language
    if session_language == "en" and (_DEVANAGARI.search(transcript) or _GUJARATI.search(transcript)
                                     or _ARABIC.search(transcript)) and len(words) >= 5:
        logger.warning("EMRT: [MISFIRE?] non-Latin script in an English-locked session: %s", transcript[:100])
    return transcript, session_language


def number_normalise(number: str) -> str:
    """Normalize a phone number to its last 10 significant digits (same helper as ai-callcenter; works for
    +91 Indian mobiles as-is). Returns the input unchanged if it has fewer than 10 digits."""
    if not number:
        return ""
    digits = "".join(c for c in number if c.isdigit())
    return digits[-10:] if len(digits) >= 10 else number


async def convert_to_mulaw(audio_data: bytes) -> bytes:
    """Convert WAV bytes to 8 kHz mono mu-law bytes (Twilio-compatible).

    Pure standard library (wave + audioop) — ai-callcenter uses pydub here, but pydub's mu-law export needs
    an ffmpeg binary on the host; this needs nothing installed."""
    with wave.open(io.BytesIO(audio_data)) as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(w.getnframes())
    if channels == 2:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
    elif channels != 1:
        raise ValueError(f"unsupported channel count: {channels}")
    if width != 2:
        frames = audioop.lin2lin(frames, width, 2)
    if rate != 8000:
        frames, _ = audioop.ratecv(frames, 2, 1, rate, 8000, None)
    return audioop.lin2ulaw(frames, 2)


# RMS threshold (16-bit linear PCM after mu-law decode) below which a chunk counts as silence.
_MULAW_SILENCE_RMS_THRESHOLD = 150


def _twilio_chunk_has_energy(b64_payload: str) -> bool:
    """Cheap local VAD on one ~20ms Twilio chunk. semantic_vad only reports turn start/end; this lets the
    stuck-speech backstop timer refresh for as long as the caller is genuinely still making sound."""
    try:
        raw = base64.b64decode(b64_payload)
        if not raw:
            return False
        return audioop.rms(audioop.ulaw2lin(raw, 2), 2) > _MULAW_SILENCE_RMS_THRESHOLD
    except Exception:
        return False


async def initialize_cached_audio():
    """Load (or generate via OpenAI TTS once, then save) the greeting / goodbye / handoff clips so they play
    instantly. Called from main.py's lifespan. Without an OpenAI key nothing is generated and the handler
    falls back to having the model speak the line — the app still runs."""
    global CACHED_GREETING, CACHED_GOODBYE, CACHED_HANDOFF
    os.makedirs("app/static", exist_ok=True)
    loaded = {}
    for key, (filename, text) in _CLIPS.items():
        path = f"app/static/{filename}"
        try:
            with open(path, "rb") as f:
                loaded[key] = base64.b64encode(f.read()).decode("utf-8")
            logger.info("EMRT: cached %s loaded from file", key)
            continue
        except FileNotFoundError:
            pass
        if openai_client is None:
            logger.info("EMRT: no OPENAI_API_KEY — %s clip not generated (model will speak it)", key)
            continue
        try:
            response = await openai_client.audio.speech.create(
                model="gpt-4o-mini-tts", voice=TTS_VOICE, input=text, response_format="wav")
            mulaw = await convert_to_mulaw(response.read())
            with open(path, "wb") as f:
                f.write(mulaw)
            loaded[key] = base64.b64encode(mulaw).decode("utf-8")
            logger.info("EMRT: cached %s generated and saved", key)
        except Exception as e:
            logger.error("EMRT: failed to initialise cached %s: %s", key, e)
    CACHED_GREETING = loaded.get("greeting")
    CACHED_GOODBYE = loaded.get("goodbye")
    CACHED_HANDOFF = loaded.get("handoff")


async def send_audio(websocket: WebSocket, stream_sid: str, audio_base64: str):
    """Non-blocking audio send with connection guard."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(
                {"event": "media", "streamSid": stream_sid, "media": {"payload": audio_base64}})
        else:
            logger.warning("EMRT: attempted to send audio on a disconnected WebSocket")
    except Exception as e:
        logger.error("EMRT: error sending audio: %s %s", e, traceback.format_exc())


async def send_clear(websocket: WebSocket, stream_sid: str):
    """Twilio `clear` event — flush queued outbound audio (barge-in)."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({"event": "clear", "streamSid": stream_sid})
    except Exception as e:
        logger.error("EMRT: error sending clear event: %s", e)


def _twilio_update(call_sid: str, **kwargs) -> bool:
    """Best-effort Twilio REST call update. No-op (returns False) when Twilio isn't configured, so the
    app runs end-to-end locally without credentials."""
    if twilio_client is None or not call_sid or call_sid.startswith("TEST"):
        return False
    try:
        twilio_client.calls(call_sid).update(**kwargs)
        return True
    except Exception as e:
        logger.error("EMRT: Twilio call update failed: %s", e)
        return False


async def end_call(closing_message: str, websocket: WebSocket, stream_sid: str,
                   pending_marks: dict, call_sid: str) -> bool:
    """Hang up the Twilio call and close the WebSocket — the ONE real hang-up primitive (reused by
    end_call and by the human-transfer fallback), as in ai-callcenter."""
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            logger.info("EMRT: hanging up Twilio call %s", call_sid)
            _twilio_update(call_sid, status="completed")
            await websocket.send_json({"event": "stop", "streamSid": stream_sid})
            await asyncio.sleep(0.5)
            await websocket.close()
        return True
    except Exception as e:
        logger.error("EMRT: error during end_call sequence: %s", e)
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
        return False


def _load_incident_snapshot(incident_id) -> dict | None:
    if not incident_id:
        return None
    with session_scope() as db:
        inc = incident_service.get_incident(db, incident_id)
        if not inc:
            return None
        return incident_service.serialize_incident(db, incident_service.resolve_primary(db, inc))


def _persist_call(call_id, *, status: str, duration: int, summary: str, language: str | None,
                  user_spoken: bool, messages: list[dict], incident_id, transferred: bool) -> None:
    """Write the final Call record + ordered transcript, and finish the incident bookkeeping."""
    with session_scope() as db:
        call = db.get(Call, call_id) if call_id else None
        if call is None:
            return
        call.status, call.is_live = status, False
        call.ended_at = datetime.now(timezone.utc)
        call.duration_seconds = duration
        call.summary, call.caller_language, call.user_spoken = summary, language, user_spoken
        call.transferred_to_human = call.transferred_to_human or transferred
        db.flush()  # autoflush is off: make is_live=False visible to serialize_incident below
        seq = 0
        for m in messages:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                seq += 1
                db.add(CallTranscript(call_id=call.id, seq=seq,
                                      role="caller" if m["role"] == "user" else "agent",
                                      content=m["content"], spoken_at=m.get("ts")))
        inc = incident_service.get_incident(db, incident_id) if incident_id else None
        if inc is not None:
            inc = incident_service.resolve_primary(db, inc)
            if not inc.summary:
                inc.summary = summary
            # PDF Scenario D: an abrupt hang-up after a vague claim, with nothing dispatched, is parked
            # for human review instead of being left looking like a live emergency.
            no_dispatch = not inc.assignments and inc.status == "open"
            words = sum(len(m["content"].split()) for m in messages
                        if m.get("role") == "user" and m.get("content"))
            if no_dispatch and inc.confidence != "low" and (not user_spoken or (duration < 15 and words < 8)):
                inc.confidence, inc.status = "low", "needs_review"
                inc.confidence_reasons = list(inc.confidence_reasons or []) + [
                    "caller hung up abruptly after a vague report; nothing was dispatched"]
            hub.publish("incident_updated", incident_service.serialize_incident(db, inc))


# ── Main WebSocket handler ────────────────────────────────────────────────────

@router.websocket("/media-stream-realtime")
async def handle_client_media_stream_realtime_emergency(websocket: WebSocket):
    logger.info("EMRT: starting WebSocket handler")

    state = {
        "call_active": True,
        "websocket_closed": False,
        "handle_disconnect_called": False,
        "handoff_triggered": False,      # blocks barge-in + new AI audio (terminal tools only)
        "waiting_to_disconnect": False,
        "transfer_redirect_pending": False,
        "user_speaking": False,
        "session_language": "unknown",
        # Set when a tool output was sent for the CURRENT response; the response.done handler then sends
        # exactly one response.create (correct for both single and parallel tool calls).
        "tool_outputs_pending": False,
        "greeting_playing": False,
        "greeting_mark_name": None,
        # Barge-in bookkeeping: which assistant audio item is playing, when its first audio was sent, and how
        # many ms of it we have sent. On interruption we tell OpenAI how much the caller ACTUALLY heard
        # (conversation.item.truncate) so the model's context matches reality — not what it *meant* to say.
        "assistant_item_id": None,
        "audio_start_mono": None,
        "audio_ms_sent": 0,
    }

    call_status = None
    is_speaking = {"value": False}
    response_active = {"value": False, "audio_sent": False}
    pending_marks: dict = {}
    call_sid = None
    stream_sid = None
    call_start_time = datetime.now(timezone.utc)
    last_activity_time = datetime.now(timezone.utc)
    trigger_called = False
    tool_call_in_progress = False
    openai_ws = None
    input_transcript = ""
    silence_lock = asyncio.Lock()
    user_speaking_started_at = None
    silence_prompt_count = 0
    from_number = ""
    call_id = None
    prompt = None
    messages: list[dict] = []
    tool_names_used: list[str] = []
    call_item_names: dict = {}           # output item id -> function name (for the terminal-tool lock)
    tool_ctx = ToolContext()

    try:
        await websocket.accept()
        if not settings.OPENAI_API_KEY:
            logger.error("EMRT: OPENAI_API_KEY is not set — cannot run a realtime call")
            await websocket.close(code=1011, reason="Server not configured")
            return

        # ── Wait for Twilio's 'start' event (callSid, streamSid, custom parameters) ──
        while True:
            data = json.loads(await websocket.receive_text())
            event_type = data.get("event")
            if event_type == "connected":
                continue
            if event_type == "start":
                stream_sid = data["start"]["streamSid"]
                call_sid = data["start"]["callSid"]
                call_start_time = datetime.now(timezone.utc)
                last_activity_time = call_start_time
                logger.info("EMRT: call started — SID %s, stream %s", call_sid, stream_sid)

                if CACHED_GREETING:
                    mark_name = str(uuid.uuid4())
                    await send_audio(websocket, stream_sid, CACHED_GREETING)
                    await websocket.send_json(
                        {"event": "mark", "streamSid": stream_sid, "mark": {"name": mark_name}})
                    pending_marks[mark_name] = datetime.now(timezone.utc)
                    state["greeting_playing"] = True
                    state["greeting_mark_name"] = mark_name

                from_number = data["start"].get("customParameters", {}).get("from_number", "")
                if not from_number and twilio_client is not None:
                    try:
                        info = twilio_client.calls(call_sid).fetch()
                        from_number = info._properties.get("from") or ""
                    except Exception as e:
                        logger.error("EMRT: could not fetch caller number: %s", e)
                break

        raw_from = from_number
        from_number = number_normalise(from_number)

        # ── Register the call + push it to the dashboard (live-call indicator) ──
        def _create_call_row():
            with session_scope() as db:
                row = Call(call_sid=call_sid, from_number=raw_from or from_number, status="in_progress",
                           is_live=True, started_at=call_start_time)
                db.add(row)
                db.flush()
                return row.id

        call_id = await asyncio.to_thread(_create_call_row)
        tool_ctx = ToolContext(call_id=call_id, call_sid=call_sid, caller_phone=raw_from or from_number)
        hub.publish("call_started", {"call_id": str(call_id), "call_sid": call_sid,
                                     "from": raw_from or from_number})

        prompt_state = PromptState(from_number)   # owns what is loaded in the live prompt + when to change it
        prompt = prompt_state.prompt
        messages = [{"role": "assistant", "content": GREETING_TEXT, "ts": datetime.now(timezone.utc)}]
        hub.publish("transcript", {"call_sid": call_sid, "role": "agent", "content": GREETING_TEXT})

        # ── Connect to the OpenAI Realtime API ──
        headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
        logger.info("EMRT: connecting to OpenAI Realtime (model=%s)", OPENAI_REALTIME_MODEL)
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL, extra_headers=headers, ping_interval=20, ping_timeout=None,
            close_timeout=10, max_size=None)

        async def update_session():
            """The FIRST session.update — the ONLY place tools are ever sent (see app/utils/realtime_session.py).
            Later updates carry `instructions` alone and rely on session.update's partial-merge behaviour."""
            await openai_ws.send(session_update_event(build_initial_session(prompt, GLOBAL_TOOLS, VOICE_NAME)))
            logger.info("EMRT: session configured (%d tools)", len(GLOBAL_TOOLS))

        await update_session()

        if CACHED_GREETING:
            # The greeting was already played from cache — tell the model so it does not repeat it.
            await openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": GREETING_TEXT}]}}))
        else:
            # No cached clip (no TTS key at startup) — have the model say it.
            await openai_ws.send(json.dumps({
                "type": "response.create",
                "response": {"instructions": f'Say exactly this and nothing else: "{GREETING_TEXT}"'}}))

        # ── Silence monitor ───────────────────────────────────────────────────
        async def silence_monitor():
            nonlocal last_activity_time, user_speaking_started_at, silence_prompt_count
            SILENCE_THRESHOLD_SECONDS = 5
            STALE_MARK_TIMEOUT_SECONDS = 25
            # Last-resort backstop for a dropped speech_stopped event; refreshed while the caller is
            # genuinely still making sound (see _twilio_chunk_has_energy), so it never cuts real speech.
            VAD_STUCK_TIMEOUT_SECONDS = 10
            MAX_SILENCE_PROMPTS = 3

            while state["call_active"] and not state["websocket_closed"]:
                try:
                    await asyncio.sleep(1)
                    if not state["call_active"] or state["websocket_closed"]:
                        break
                    if websocket.client_state != WebSocketState.CONNECTED or openai_ws.closed:
                        break
                    if state["handoff_triggered"] or state["waiting_to_disconnect"]:
                        continue
                    if tool_call_in_progress:
                        last_activity_time = datetime.now(timezone.utc)
                        continue

                    now = datetime.now(timezone.utc)
                    stale = [k for k, v in list(pending_marks.items())
                             if isinstance(v, datetime) and (now - v).total_seconds() > STALE_MARK_TIMEOUT_SECONDS]
                    for k in stale:
                        del pending_marks[k]
                    if stale and not pending_marks:
                        is_speaking["value"] = False
                        last_activity_time = now
                    if pending_marks or is_speaking["value"]:
                        continue

                    if state["user_speaking"]:
                        if user_speaking_started_at is not None and \
                                (now - user_speaking_started_at).total_seconds() > VAD_STUCK_TIMEOUT_SECONDS:
                            logger.warning("EMRT: [STUCK VAD] forcing buffer commit")
                            state["user_speaking"] = False
                            user_speaking_started_at = None
                            last_activity_time = now
                            try:
                                await openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                                await openai_ws.send(json.dumps({"type": "response.create"}))
                                silence_prompt_count = 0
                            except Exception:
                                logger.exception("EMRT: failed to force buffer commit")
                        else:
                            last_activity_time = now
                        continue

                    if response_active["value"]:
                        last_activity_time = now
                        continue

                    silence_duration = (now - last_activity_time).total_seconds()
                    if silence_duration <= SILENCE_THRESHOLD_SECONDS:
                        continue

                    async with silence_lock:
                        if (response_active["value"] or is_speaking["value"] or state["user_speaking"]
                                or not state["call_active"] or state["handoff_triggered"]
                                or state["waiting_to_disconnect"]):
                            continue
                        try:
                            is_speaking["value"] = True
                            response_active["value"] = True
                            silence_prompt_count += 1
                            if silence_prompt_count == 1:
                                silence_text = (
                                    "The caller has been silent. Say \"I'm here — can you hear me?\" then re-ask "
                                    "your last question in the same context. Stay in the caller's language.")
                            elif silence_prompt_count == 2:
                                silence_text = (
                                    "The caller is still silent. Say \"I'm still here with you.\" If they may be in "
                                    "danger and unable to speak, tell them help is being arranged and to stay on "
                                    "the line, then re-ask the same pending question. Do not end the call.")
                            else:
                                silence_text = (
                                    "No response after 3 attempts. Silence alone is NOT intent: do NOT dispatch, "
                                    "create an incident, or end the call because of silence. If an incident with a "
                                    "location already exists, say \"I'm staying on the line — help is being "
                                    "arranged\" and keep the line open. If there is no incident and no location, "
                                    "call transfer_to_human_operator with context_summary \"caller silent, nothing "
                                    "learned\". Stay in the caller's language.")
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.create",
                                "item": {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": silence_text}]}}))
                            await openai_ws.send(json.dumps({"type": "response.create"}))
                            last_activity_time = datetime.now(timezone.utc)
                            if silence_prompt_count >= MAX_SILENCE_PROMPTS:
                                silence_prompt_count = 0
                        except (websockets.exceptions.ConnectionClosed, ssl.SSLError) as e:
                            logger.warning("EMRT: OpenAI socket closed during silence prompt: %s", e)
                            is_speaking["value"] = response_active["value"] = False
                            break
                        except Exception:
                            logger.exception("EMRT: error sending silence prompt")
                            is_speaking["value"] = response_active["value"] = False
                            silence_prompt_count = max(0, silence_prompt_count - 1)
                            break
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("EMRT: error in silence_monitor loop")
                    break
            logger.info("EMRT: silence_monitor exited")

        # ── Twilio -> OpenAI ──────────────────────────────────────────────────
        async def forward_twilio_to_openai():
            nonlocal call_status, trigger_called, user_speaking_started_at, last_activity_time
            try:
                async for message in websocket.iter_text():
                    if not state["call_active"]:
                        break
                    data = json.loads(message)
                    event_type = data.get("event")

                    if event_type in ("start", "connected"):
                        continue

                    elif event_type == "media":
                        if state["handoff_triggered"]:
                            continue
                        if state["user_speaking"] and _twilio_chunk_has_energy(data["media"]["payload"]):
                            user_speaking_started_at = datetime.now(timezone.utc)
                        try:
                            await openai_ws.send(json.dumps(
                                {"type": "input_audio_buffer.append", "audio": data["media"]["payload"]}))
                        except (websockets.exceptions.ConnectionClosed, ssl.SSLError, RuntimeError) as e:
                            logger.warning("EMRT: OpenAI socket closed while forwarding audio: %s", e)
                            state["call_active"] = False
                            break

                    elif event_type == "mark":
                        mark_name = data.get("mark", {}).get("name")
                        if mark_name and mark_name == state["greeting_mark_name"]:
                            state["greeting_playing"] = False
                        if mark_name and mark_name in pending_marks:
                            del pending_marks[mark_name]
                            if not pending_marks:
                                last_activity_time = datetime.now(timezone.utc)
                                is_speaking["value"] = False
                                if state["waiting_to_disconnect"] and not state["transfer_redirect_pending"]:
                                    logger.info("EMRT: [MARK] all audio played — disconnecting")
                                    state["waiting_to_disconnect"] = False
                                    await asyncio.sleep(2)
                                    call_status = call_status or "System Disconnect"
                                    await end_call("Goodbye", websocket, stream_sid, pending_marks, call_sid)
                                    state["call_active"] = False
                                    state["websocket_closed"] = True
                                    await handle_disconnect()
                                    trigger_called = False
                                    return

                    elif event_type == "stop":
                        logger.info("EMRT: call ended by Twilio")
                        call_status = call_status or "Caller Disconnect"
                        state["call_active"] = False
                        await handle_disconnect()
                        break

            except WebSocketDisconnect:
                call_status = call_status or "Caller Disconnect"
                state["call_active"] = False
                await handle_disconnect()
            except RuntimeError as e:
                logger.info("EMRT: WebSocket runtime error: %s", e)
                call_status = call_status or "System Disconnect"
                state["call_active"] = False
                await handle_disconnect()
            except Exception:
                logger.exception("EMRT: error in Twilio->OpenAI")
                call_status = call_status or "System Disconnect"
                state["call_active"] = False
                await handle_disconnect()

        # ── helpers used by the OpenAI -> Twilio pump ─────────────────────────
        async def _send_tool_output(call_item_id: str, payload: dict):
            await openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_item_id,
                         "output": json.dumps(payload, default=str)}}))

        def _push_message(role: str, content: str):
            messages.append({"role": role, "content": content, "ts": datetime.now(timezone.utc)})
            hub.publish("transcript", {"call_sid": call_sid, "role": "caller" if role == "user" else "agent",
                                       "content": content, "incident_id": tool_ctx.incident_id})

        async def _transfer_to_department(handoff_id: str, department: str):
            """transfer_to_human_operator: let the hold line finish playing, then hand the LIVE call to the
            department's escalation ladder (app/services/twilio_escalation.py): Twilio REST redirects the call to our
            /twilio/escalation/dial TwiML, which SMSes and rings each on-duty contact in turn, repeats the cycle, then
            rings the director — every attempt is logged in `escalation_attempts`. This is the counterpart of
            ai-callcenter's calendar_schedule_transfer -> Twilio Studio Flow.

            Falls back to a clean simulated hang-up when Twilio isn't configured (or the ladder is empty), so a
            caller is never left in dead air."""
            nonlocal call_status
            await asyncio.sleep(3)  # same pacing as ai-callcenter: let "please stay on the line" finish
            waited = 0
            while pending_marks and waited < 8:
                await asyncio.sleep(1)
                waited += 1
            if not (state["waiting_to_disconnect"] and state["call_active"]
                    and not state["handle_disconnect_called"]):
                return

            def _begin():
                with session_scope() as db:
                    return twilio_escalation.begin_transfer(db, uuid.UUID(handoff_id))

            outcome = await asyncio.to_thread(_begin)
            logger.info("EMRT: escalation transfer to '%s' -> %s", department, outcome)
            if outcome["status"] == "dialing":
                state["transfer_redirect_pending"] = True  # Twilio now owns the call leg; do NOT hang up
                call_status = "Transferred to Human"
                return  # Twilio ends our media stream -> 'stop' -> handle_disconnect
            call_status = call_status or f"Transferred to Human ({outcome['status']})"
            state["transfer_redirect_pending"] = False  # transfer could not start -> hang up cleanly instead
            state["waiting_to_disconnect"] = False
            await end_call("Goodbye", websocket, stream_sid, pending_marks, call_sid)
            state["call_active"] = False
            state["websocket_closed"] = True
            await handle_disconnect()

        # ── OpenAI -> Twilio ──────────────────────────────────────────────────
        async def forward_openai_to_twilio():
            nonlocal call_status, trigger_called, last_activity_time
            nonlocal input_transcript, user_speaking_started_at, silence_prompt_count
            nonlocal tool_call_in_progress, prompt

            try:
                async for message in openai_ws:
                    if not state["call_active"]:
                        break
                    data = json.loads(message)
                    event_type = data.get("type")

                    if event_type == "response.output_audio.delta":
                        if state["handoff_triggered"]:
                            continue
                        last_activity_time = datetime.now(timezone.utc)
                        is_speaking["value"] = True
                        response_active["value"] = True
                        response_active["audio_sent"] = True
                        payload = data.get("delta", "")
                        if payload:
                            item_id = data.get("item_id")
                            if item_id and item_id != state["assistant_item_id"]:
                                state["assistant_item_id"] = item_id
                                state["audio_start_mono"] = time.monotonic()
                                state["audio_ms_sent"] = 0
                            state["audio_ms_sent"] += (len(payload) * 3 // 4) // 8  # 8 kHz mu-law = 8 bytes/ms
                        if payload and stream_sid:
                            if state["websocket_closed"]:
                                break
                            try:
                                await websocket.send_json(
                                    {"event": "media", "streamSid": stream_sid, "media": {"payload": payload}})
                            except RuntimeError:
                                state["websocket_closed"] = True
                                break

                    elif event_type == "response.output_item.added":
                        item = data.get("item", {})
                        if item.get("type") == "function_call":
                            call_item_names[item.get("id")] = item.get("name")

                    elif event_type == "response.function_call_arguments.delta":
                        # Lock barge-in only around terminal tools, and only if the agent already spoke in this
                        # response (its goodbye / hold line must not be cut off). Every other tool leaves the
                        # caller free to interrupt — a panicked caller must always be able to.
                        name = call_item_names.get(data.get("item_id"))
                        if name in TERMINAL_TOOLS and response_active["audio_sent"] and not state["handoff_triggered"]:
                            state["handoff_triggered"] = True
                            logger.info("EMRT: [HANDOFF LOCK] terminal tool '%s' streaming — barge-in disabled", name)

                    elif event_type == "response.output_audio_transcript.done":
                        transcript = (data.get("transcript") or "").strip()
                        logger.info("EMRT: AI said: %s", transcript)
                        if state["handoff_triggered"]:
                            last_activity_time = datetime.now(timezone.utc)
                            is_speaking["value"] = False
                            if transcript:
                                _push_message("assistant", transcript)
                            continue
                        if transcript.startswith("{") or transcript.startswith("["):
                            logger.warning("EMRT: rejected AI JSON hallucination: %s...", transcript[:80])
                            last_activity_time = datetime.now(timezone.utc)
                            is_speaking["value"] = False
                            continue
                        if input_transcript.strip():
                            _push_message("user", input_transcript.strip())
                            input_transcript = ""
                        if transcript:
                            _push_message("assistant", transcript)
                        last_activity_time = datetime.now(timezone.utc)
                        is_speaking["value"] = False

                        # Fallback end-call detection: only at the END of the transcript (substring matching
                        # fired false positives in ai-callcenter, e.g. "take care of that").
                        tail = transcript.lower().rstrip(" .,!?")
                        if any(tail.endswith(p.rstrip(" .,!?")) for p in end_call_messages):
                            async def _end_call_fallback():
                                nonlocal trigger_called, call_status
                                await asyncio.sleep(2.0)
                                if (not trigger_called and not state["handoff_triggered"]
                                        and not state["waiting_to_disconnect"]):
                                    logger.warning("EMRT: goodbye phrase but no end_call tool — fallback disconnect")
                                    trigger_called = True
                                    _twilio_update(call_sid, status="completed")
                                    state["waiting_to_disconnect"] = True
                                    state["call_active"] = False
                                    state["websocket_closed"] = True
                                    call_status = "Goodbye (Fallback)"
                                    await handle_disconnect()
                            asyncio.create_task(_end_call_fallback())

                    elif event_type == "response.created":
                        response_active["value"] = True
                        response_active["audio_sent"] = False

                    elif event_type == "conversation.item.input_audio_transcription.completed":
                        raw = data.get("transcript", "")
                        transcript, state["session_language"] = correct_transcript(
                            raw, state["session_language"], from_number)
                        logger.info("EMRT: caller said [lang=%s]: %s", state["session_language"], transcript)
                        if transcript and transcript.strip():
                            input_transcript += f"{transcript} "
                        last_activity_time = datetime.now(timezone.utc)

                    elif event_type == "input_audio_buffer.speech_started":
                        if state["handoff_triggered"] or state["waiting_to_disconnect"]:
                            continue
                        # The greeting is never interrupted; everything else can be (barge-in).
                        if not state["greeting_playing"] and stream_sid \
                                and websocket.client_state == WebSocketState.CONNECTED:
                            await send_clear(websocket, stream_sid)
                        async with silence_lock:
                            silence_prompt_count = 0
                            last_activity_time = datetime.now(timezone.utc)
                            if response_active["value"] or is_speaking["value"]:
                                response_active["value"] = False
                                is_speaking["value"] = False
                                try:
                                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                                except Exception as e:
                                    logger.debug("EMRT: could not send response.cancel: %s", e)
                            # Proper barge-in: cut the assistant's turn at what the caller actually heard.
                            # Playback started at the first audio delta, so heard = wall-clock since then, capped
                            # by what we have sent (audio is streamed faster than real time).
                            if state["assistant_item_id"] and state["audio_start_mono"] is not None:
                                elapsed_ms = int((time.monotonic() - state["audio_start_mono"]) * 1000)
                                heard_ms = max(0, min(elapsed_ms, state["audio_ms_sent"]))
                                try:
                                    await openai_ws.send(json.dumps({
                                        "type": "conversation.item.truncate",
                                        "item_id": state["assistant_item_id"], "content_index": 0,
                                        "audio_end_ms": heard_ms}))
                                    logger.info("EMRT: barge-in — truncated %s at %d ms (sent %d ms)",
                                                state["assistant_item_id"], heard_ms, state["audio_ms_sent"])
                                except Exception as e:
                                    logger.debug("EMRT: could not truncate item: %s", e)
                                state["assistant_item_id"], state["audio_start_mono"], state["audio_ms_sent"] = None, None, 0
                        if not state["greeting_playing"]:
                            pending_marks.clear()
                        state["user_speaking"] = True
                        user_speaking_started_at = datetime.now(timezone.utc)

                    elif event_type == "input_audio_buffer.speech_stopped":
                        state["user_speaking"] = False
                        user_speaking_started_at = None
                        last_activity_time = datetime.now(timezone.utc)

                    elif event_type == "response.done":
                        is_speaking["value"] = False
                        response_active["value"] = False
                        tool_call_in_progress = False

                        if stream_sid and response_active["audio_sent"]:
                            mark_name = f"response_done_{uuid.uuid4()}"
                            if state["websocket_closed"]:
                                continue
                            try:
                                await websocket.send_json(
                                    {"event": "mark", "streamSid": stream_sid, "mark": {"name": mark_name}})
                                pending_marks[mark_name] = datetime.now(timezone.utc)
                            except RuntimeError:
                                state["websocket_closed"] = True
                                break
                        else:
                            last_activity_time = datetime.now(timezone.utc)

                        # ONE follow-up response for however many tool calls this response contained
                        # (find_nearest_resource + give_caller_safety_instructions run in parallel).
                        if state["tool_outputs_pending"]:
                            state["tool_outputs_pending"] = False
                            await openai_ws.send(json.dumps({"type": "response.create"}))
                            last_activity_time = datetime.now(timezone.utc)

                    elif event_type == "error":
                        logger.error("EMRT: OpenAI error event: %s", data.get("error"))

                    elif event_type == "response.function_call_arguments.done":
                        trigger_called = True
                        tool_call_in_progress = True
                        call_item_id = data.get("call_id")
                        function_name = data.get("name") or call_item_names.get(data.get("item_id"))
                        try:
                            arguments = json.loads(data.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            logger.error("EMRT: failed to parse function arguments for %s", function_name)
                            await _send_tool_output(call_item_id, {
                                "status": "error", "message": "Arguments were not valid JSON — call the tool again."})
                            state["tool_outputs_pending"] = True
                            continue
                        logger.info("EMRT: function call: %s %s", function_name, arguments)
                        tool_names_used.append(function_name)

                        if input_transcript.strip():
                            _push_message("user", input_transcript.strip())
                            input_transcript = ""

                        # ── end_call ──────────────────────────────────────────
                        if function_name == "end_call":
                            try:
                                state["handoff_triggered"] = True
                                if not response_active["audio_sent"] and CACHED_GOODBYE and stream_sid:
                                    logger.warning("EMRT: end_call without goodbye audio — playing fallback goodbye")
                                    await send_audio(websocket, stream_sid, CACHED_GOODBYE)
                                    gb_mark = f"end_call_goodbye_{str(uuid.uuid4())[:8]}"
                                    if websocket.client_state == WebSocketState.CONNECTED:
                                        await websocket.send_json(
                                            {"event": "mark", "streamSid": stream_sid, "mark": {"name": gb_mark}})
                                        pending_marks[gb_mark] = datetime.now(timezone.utc)
                                    _push_message("assistant", GOODBYE_TEXT)
                                await _send_tool_output(call_item_id, {"status": "success", "message": "Call will end"})
                                state["waiting_to_disconnect"] = True

                                async def _end_call_timeout():
                                    await asyncio.sleep(3)
                                    if not state["waiting_to_disconnect"]:
                                        return
                                    waited = 0
                                    while pending_marks and waited < 8:
                                        await asyncio.sleep(1)
                                        waited += 1
                                        if not state["waiting_to_disconnect"]:
                                            return
                                    if state["waiting_to_disconnect"] and state["call_active"]:
                                        await end_call("Goodbye", websocket, stream_sid, pending_marks, call_sid)
                                        state["call_active"] = False
                                        state["websocket_closed"] = True
                                        await handle_disconnect()

                                asyncio.create_task(_end_call_timeout())
                                continue
                            except Exception:
                                logger.exception("EMRT: error handling end_call")
                                state["call_active"] = False
                                state["websocket_closed"] = True
                                call_status = call_status or "System Disconnect"
                                await handle_disconnect()
                                return

                        # ── every other tool: shared executor, in a worker thread ──
                        result = await asyncio.to_thread(execute_tool, function_name, arguments, tool_ctx)
                        effects = result.pop("_effects", None) or {}
                        await _send_tool_output(call_item_id, result)

                        # LIVE PROMPT SWAP. create_incident / a duplicate merge / an escalation return a `load_context`
                        # effect (category + responsible departments). PromptState decides whether that is really a change;
                        # if so ONLY the instructions are re-sent (tools/audio untouched, history preserved) — before the
                        # follow-up response.create, so the model's very next words already use the new context.
                        ctx_effect = effects.get("load_context")
                        if ctx_effect:
                            new_prompt = prompt_state.apply(ctx_effect["category"], ctx_effect["departments"])
                            if new_prompt:
                                prompt = new_prompt
                                await openai_ws.send(instructions_update_event(prompt))
                                logger.info("EMRT: session.update #%d — category '%s', departments %s",
                                            prompt_state.updates_sent, prompt_state.category, prompt_state.departments)

                        transfer = effects.get("transfer_call")
                        if transfer:
                            # The hold line was spoken in the same turn (tool description). If it was not,
                            # play the cached fallback so the caller never hears dead air.
                            if not response_active["audio_sent"] and CACHED_HANDOFF and stream_sid:
                                await send_audio(websocket, stream_sid, CACHED_HANDOFF)
                                hmark = f"handoff_{str(uuid.uuid4())[:8]}"
                                if websocket.client_state == WebSocketState.CONNECTED:
                                    await websocket.send_json(
                                        {"event": "mark", "streamSid": stream_sid, "mark": {"name": hmark}})
                                    pending_marks[hmark] = datetime.now(timezone.utc)
                                _push_message("assistant", HANDOFF_TEXT)
                            state["handoff_triggered"] = True
                            state["waiting_to_disconnect"] = True
                            # Set NOW, not after the wait: when the hold line finishes playing, the Twilio `mark`
                            # handler would otherwise see waiting_to_disconnect and hang up on the caller before the
                            # transfer task gets to redirect the call. Cleared only if the transfer can't start.
                            state["transfer_redirect_pending"] = True
                            asyncio.create_task(_transfer_to_department(transfer["handoff_id"], transfer["department"]))
                            continue  # no follow-up response: the call is being handed over

                        state["tool_outputs_pending"] = True
                        last_activity_time = datetime.now(timezone.utc)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("EMRT: OpenAI WebSocket closed: %s", e)
                call_status = call_status or "System Disconnect"
                if state["call_active"]:
                    state["call_active"] = False
                    await handle_disconnect()
            except Exception:
                logger.exception("EMRT: error in OpenAI->Twilio")
                call_status = call_status or "System Disconnect"
                state["call_active"] = False
                await handle_disconnect()

        # ── Disconnect: summary + persistence ─────────────────────────────────
        async def _finish_call():
            """The actual post-call work. Runs as its OWN task (see handle_disconnect) so that cancelling the
            call handler — the framework does this when the peer hangs up, and every server shutdown /
            redeploy does — can never abort it half-way and lose the call record."""
            nonlocal input_transcript
            try:
                if input_transcript.strip():
                    _push_message("user", input_transcript.strip())
                    input_transcript = ""
                duration = int((datetime.now(timezone.utc) - call_start_time).total_seconds())
                user_spoken = any(m["role"] == "user" and m["content"].strip() for m in messages)
                incident = await asyncio.to_thread(_load_incident_snapshot, tool_ctx.incident_id)
                summary = await generate_call_summary(messages, incident, sorted(set(tool_names_used)))
                status = {"Transferred to Human": "transferred_to_human"}.get(call_status or "", "completed")
                await asyncio.to_thread(
                    _persist_call, call_id, status=status, duration=duration, summary=summary["summary"],
                    language=summary.get("caller_language") or state["session_language"],
                    user_spoken=user_spoken, messages=messages, incident_id=tool_ctx.incident_id,
                    transferred=bool(call_status and call_status.startswith("Transferred")))
                hub.publish("call_ended", {"call_sid": call_sid, "call_id": str(call_id),
                                           "incident_id": tool_ctx.incident_id, "status": call_status,
                                           "duration_seconds": duration, "summary": summary["summary"]})
                logger.info("EMRT: call %s finished (%ss, status=%s)", call_sid, duration, call_status)
            except Exception:
                logger.exception("EMRT: error handling disconnect")

        async def handle_disconnect():
            """Runs once at real call end: flush the last utterance, summarise, persist the call, transcript and
            incident bookkeeping, tell the dashboard the caller is off the line."""
            if state["handle_disconnect_called"]:
                return
            state["handle_disconnect_called"] = True
            state["call_active"] = False
            logger.info("EMRT: handle_disconnect start (call_sid=%s, status=%s)", call_sid, call_status)
            task = asyncio.create_task(_finish_call())
            _BACKGROUND_TASKS.add(task)  # hold a reference so it cannot be garbage-collected mid-flight
            task.add_done_callback(_BACKGROUND_TASKS.discard)
            await asyncio.shield(task)   # cancellation of THIS coroutine no longer cancels the task

        # ── Run all three loops concurrently ──────────────────────────────────
        twilio_task = asyncio.create_task(forward_twilio_to_openai())
        openai_task = asyncio.create_task(forward_openai_to_twilio())
        silence_task = asyncio.create_task(silence_monitor())

        async def _close_openai_ws():
            try:
                if openai_ws is not None and not openai_ws.closed:
                    await openai_ws.close()
            except Exception as e:
                logger.debug("EMRT: error closing OpenAI WebSocket: %s", e)

        # Once the caller's leg is gone (hang-up, `stop`, or Twilio taking the leg for a transfer) nothing else will
        # ever arrive from OpenAI, so `async for message in openai_ws` would wait on an idle socket until OpenAI's
        # session limit. Close it: that ends the OpenAI->Twilio task and lets the handler finish.
        twilio_task.add_done_callback(lambda _t: asyncio.create_task(_close_openai_ws()))
        try:
            await asyncio.gather(twilio_task, openai_task, silence_task, return_exceptions=True)
        finally:
            state["call_active"] = False
            state["websocket_closed"] = True
            for task in (twilio_task, openai_task, silence_task):
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            if not state["handle_disconnect_called"] and call_sid:
                await handle_disconnect()

    except Exception:
        logger.exception("EMRT: WebSocket handler error")
    finally:
        state["call_active"] = False
        try:
            if openai_ws is not None and not openai_ws.closed:
                await openai_ws.close()
        except Exception as e:
            logger.debug("EMRT: error closing OpenAI WebSocket: %s", e)
        try:
            # websocket_closed only means "stop sending"; the socket itself may still be open (e.g. after Twilio's
            # `stop`), so decide from the real connection state and always close our end when the handler finishes.
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception as e:
            logger.debug("EMRT: error closing Twilio WebSocket: %s", e)
        logger.info("EMRT: WebSocket handler completed")
