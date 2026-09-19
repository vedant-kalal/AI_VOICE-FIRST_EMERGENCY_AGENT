"""Twilio Voice webhooks.

`POST /incoming-call` is what the Twilio phone number's "A call comes in" webhook points at. It answers
with TwiML that opens a bidirectional Media Stream to /media-stream-realtime (PDF §3.1 steps 1-2) — the
same shape as ai-callcenter's /incoming-call-realtime/{recording_sid}, minus recording/tenant plumbing.

If you'd rather not host this route at all, twilio/incoming_call_twiml_bin.xml is the equivalent static
TwiML Bin (see twilio/README.md).
"""
import logging

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.core.config import settings
from app.core.database import session_scope
from app.models.call import Call
from app.models.department import CallHandoff
from app.services import twilio_escalation
from app.services.dashboard_hub import hub

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Twilio"])


def _public_host(request: Request) -> str:
    return (settings.PUBLIC_HOST or request.headers.get("host") or request.url.hostname or "").strip()


async def _validate_twilio(request: Request, host: str) -> dict:
    """Return the parsed form. When TWILIO_VALIDATE_SIGNATURE is on, reject requests Twilio did not sign."""
    form = dict(await request.form())
    if settings.TWILIO_VALIDATE_SIGNATURE:
        if not settings.TWILIO_AUTH_TOKEN:
            raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN not configured")
        signature = request.headers.get("X-Twilio-Signature", "")
        # Twilio signs the FULL url, query string included (the escalation URLs carry handoff_id/step).
        url = f"https://{host}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")
        if not RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, form, signature):
            logger.warning("Rejected Twilio webhook with an invalid signature (path=%s)", request.url.path)
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return form


@router.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    host = _public_host(request)
    if not host:
        raise HTTPException(status_code=400, detail="Cannot determine public host (set PUBLIC_HOST)")
    form = await _validate_twilio(request, host)
    call_from = form.get("From") or request.query_params.get("From") or ""
    logger.info("Incoming call sid=%s from=%s", form.get("CallSid"), call_from)

    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=f"wss://{host}/media-stream-realtime")
    if call_from:
        stream.parameter(name="from_number", value=call_from)  # read in the handler's 'start' event
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")


@router.post("/call-status")
async def handle_call_status(request: Request):
    """Optional Twilio status callback: if a call ends without our WebSocket seeing 'stop' (network drop),
    make sure the dashboard stops showing the caller as live."""
    host = _public_host(request)
    form = await _validate_twilio(request, host)
    sid, status = form.get("CallSid"), form.get("CallStatus")
    if sid and status in ("completed", "busy", "failed", "no-answer", "canceled"):
        with session_scope() as db:
            call = db.query(Call).filter(Call.call_sid == sid).first()
            if call and call.is_live:
                call.is_live = False
                call.status = call.status if call.status != "in_progress" else "caller_disconnect"
                hub.publish("call_ended", {"call_sid": sid, "status": status})
    return Response(status_code=204)


# ── Escalation ladder (live-call transfer to a department) — see app/services/twilio_escalation.py ──────────
def _twiml(xml: str) -> Response:
    return Response(content=xml, media_type="application/xml")


def _handoff(db, request: Request) -> tuple[CallHandoff, int]:
    try:
        hid = uuid.UUID(request.query_params.get("handoff_id", ""))
        step = int(request.query_params.get("step", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown handoff")
    h = db.get(CallHandoff, hid)
    if h is None:
        raise HTTPException(status_code=404, detail="Unknown handoff")
    return h, step


@router.post("/twilio/escalation/dial")
async def escalation_dial(request: Request):
    """Twilio fetches this after begin_transfer() redirected the live call: SMS + <Dial> one rung."""
    await _validate_twilio(request, _public_host(request))
    with session_scope() as db:
        h, step = _handoff(db, request)
        return _twiml(twilio_escalation.dial_twiml(db, h, step))


@router.post("/twilio/escalation/result")
async def escalation_result(request: Request):
    """<Dial action>: answered -> done; otherwise redirect to the next rung or give up."""
    form = await _validate_twilio(request, _public_host(request))
    with session_scope() as db:
        h, step = _handoff(db, request)
        return _twiml(twilio_escalation.result_twiml(db, h, step, form))


@router.post("/twilio/escalation/whisper")
async def escalation_whisper(request: Request):
    """Read to the person who answers, before the bridge — gives them the incident context."""
    await _validate_twilio(request, _public_host(request))
    with session_scope() as db:
        h, _ = _handoff(db, request)
        return _twiml(twilio_escalation.whisper_twiml(db, h))


@router.post("/twilio/escalation/leg-status")
async def escalation_leg_status(request: Request):
    """Per-leg statusCallback (initiated / ringing / answered / completed) -> escalation_attempts audit row."""
    form = await _validate_twilio(request, _public_host(request))
    with session_scope() as db:
        h, step = _handoff(db, request)
        twilio_escalation.record_leg_status(db, h, step, form)
    return Response(status_code=204)
