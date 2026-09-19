"""DEV-ONLY scenario replayer — rehearse the PDF §7 demo with NO phone call and NO paid services.

Enabled only with ENABLE_DEV_ENDPOINTS=true (and still behind DASHBOARD_API_KEY if one is set). It drives the
REAL tool executor (same code the live agent uses) with realistic pauses and scripted transcript lines, so the
dashboard shows exactly what a live call would: live-call indicator, incident pin, duplicate merge, radius
search, dispatch, escalation, audit trail. The "caller"/"agent" lines are scripted stand-ins for the model.

    POST /api/dev/demo/a   flagship road accident + duplicate merge + parallel dispatch
    POST /api/dev/demo/b   industrial gas leak -> automatic multi-department escalation
    POST /api/dev/demo/c   flood: several callers become one escalating incident
    POST /api/dev/demo/d   prank / low-confidence caller -> human review, nothing dispatched
    POST /api/dev/demo/e   factory fire -> nearest fire station lookup -> live-call transfer to the fire dept ladder
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.endpoints.dashboard import require_key
from app.api.v1.endpoints.openai_realtime_emergency import _persist_call
from app.core.config import settings
from app.core.database import session_scope
from app.models.call import Call
from app.services.dashboard_hub import hub
from app.services.tool_executor import ToolContext, execute_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dev", tags=["Dev demo"], dependencies=[Depends(require_key)])

# step kinds: ("caller", text) | ("agent", text) | ("tool", name, args) | ("pause", seconds)
SCENARIOS: dict[str, list] = {
    "a": [
        ("caller", "Help! There's been a huge crash on the highway, someone's stuck in the car!"),
        ("agent", "I'm here with you. Where exactly are you right now - any landmark or the highway name?"),
        ("caller", "Near SG Mall, on SG Highway."),
        ("tool", "geocode_location", {"raw_location_text": "SG Highway", "nearby_landmark": "SG Mall"}),
        ("agent", "I have you near SG Mall on SG Highway - is that right?"),
        ("caller", "Yes, yes, hurry!"),
        ("tool", "geocode_location", {"caller_confirmed": True}),
        ("tool", "create_incident", {"category": "road_accident", "reported_people_affected": 2, "confidence": "high",
                                     "description": "huge crash on the highway near the mall, someone is stuck in a car"}),
        ("tool", "check_duplicate_incident", {}),
        ("tool", "estimate_severity", {"signals": {"injuries": "serious", "people_trapped": True, "people_affected": 2}}),
        ("agent", "Stay calm. Please don't move the trapped person unless there's fire."),
        ("tool", "give_caller_safety_instructions", {"category": "road_accident", "situation_detail": "person trapped in car"}),
        ("tool", "find_nearest_resource", {"resource_type": "ambulance"}),
        ("tool", "find_nearest_resource", {"resource_type": "rescue_team", "required_equipment": ["jaws_of_life"]}),
        ("tool", "@assign_all", {}),
        ("agent", "An ambulance and a rescue team are on the way, arriving in about six minutes. Can you tell me how many people are in the car?"),
        ("caller", "Two, the driver is awake but can't get out."),
        ("tool", "get_hospital_capacity", {"required_specialty": "trauma"}),
        ("caller", "Where is the nearest hospital?"),
        ("tool", "find_nearest_department_center", {"department": "ems", "radius_km": 8}),
        ("agent", "The nearest hospital is on your route; the ambulance will take the patient there."),
        ("pause", 2),
    ],
    "e": [
        ("caller", "There's a factory fire and three people are trapped inside!"),
        ("agent", "Get out and away from the building now. Where exactly are you?"),
        ("caller", "Thaltej Cross Road."),
        ("tool", "geocode_location", {"raw_location_text": "Thaltej Cross Road"}),
        ("tool", "create_incident", {"category": "fire", "confidence": "high", "reported_people_affected": 3,
                                     "description": "factory fire near Thaltej Cross Road, three people trapped inside"}),
        ("tool", "estimate_severity", {"signals": {"fire_present": True, "people_trapped": True, "people_affected": 3}}),
        ("tool", "find_nearest_department_center", {"department": "fire_dept", "radius_km": 8}),
        ("agent", "I'm connecting you to the fire department now. Please stay on the line."),
        ("tool", "transfer_to_human_operator", {"department": "fire_dept",
                                                "context_summary": "Factory fire at Thaltej Cross Road, three trapped, engines being dispatched."}),
        ("pause", 1),
    ],
    "b": [
        ("caller", "There's a strong smell of gas near a factory and we can't breathe!"),
        ("agent", "Move away from the factory now, upwind if you can. Where are you?"),
        ("caller", "Naroda GIDC, near the chemical units."),
        ("tool", "geocode_location", {"raw_location_text": "Naroda GIDC factory"}),
        ("tool", "create_incident", {"category": "industrial_chemical", "reported_people_affected": 6, "confidence": "high",
                                     "description": "strong gas smell near a factory, people cannot breathe"}),
        ("tool", "estimate_severity", {"signals": {"hazardous_material": True, "difficulty_breathing": True, "people_affected": 6}}),
        ("agent", "I'm alerting the fire department, hazmat and police right now. Keep moving away and cover your nose and mouth."),
        ("tool", "give_caller_safety_instructions", {"category": "industrial_chemical", "situation_detail": "gas leak"}),
        ("tool", "find_nearest_resource", {"resource_type": "hazmat_team", "max_radius_km": 40}),
        ("tool", "find_nearest_resource", {"resource_type": "fire_truck", "required_equipment": ["industrial_fire_equipment"], "max_radius_km": 40}),
        ("tool", "@assign_all", {}),
        ("tool", "get_hospital_capacity", {"required_specialty": "toxicology"}),
        ("pause", 2),
    ],
    "c": [],  # built dynamically below (several callers)
    "d": [
        ("caller", "Hello... test test. There is a... dragon on my roof."),
        ("agent", "I want to help. Can you tell me where you are and what is happening?"),
        ("caller", "Hahaha nothing, just testing."),
        ("tool", "create_incident", {"category": "other", "confidence": "low", "description": "joking caller, contradictory answers"}),
        ("agent", "I'm connecting you to a colleague who can help."),
        ("tool", "transfer_to_human_operator", {"context_summary": "Caller gave contradictory, joking answers; no location; nothing dispatched."}),
        ("pause", 1),
    ],
}

FLOOD_REPORTS = [
    ("The street near Vastrapur Lake is waterlogged.", "street near Vastrapur Lake is waterlogged, water rising", {"water_level": "ankle"}),
    ("Cars are stuck in the water near Vastrapur Lake!", "Vastrapur lake road flooded, cars stuck in the water", {"water_level": "knee"}),
    ("It's getting worse, shops are flooding.", "flood water on the street near Vastrapur Lake, shops flooding", {"water_level": "knee", "situation_worsening": True}),
    ("The water is now waist deep!", "water is now waist deep near Vastrapur Lake", {"water_level": "waist"}),
    ("There are children and elderly people stranded!", "Vastrapur Lake area flooded, children and elderly stranded on the road",
     {"water_level": "waist", "children_or_elderly_involved": True}),
]


def _new_call(phone: str) -> ToolContext:
    sid = f"DEMO-{uuid.uuid4().hex[:10]}"
    with session_scope() as db:
        row = Call(call_sid=sid, from_number=phone, status="in_progress", is_live=True,
                   started_at=datetime.now(timezone.utc))
        db.add(row)
        db.flush()
        cid = row.id
    ctx = ToolContext(call_id=cid, call_sid=sid, caller_phone=phone)
    hub.publish("call_started", {"call_id": str(cid), "call_sid": sid, "from": phone})
    return ctx


def _end_call(ctx: ToolContext, messages: list[dict], started: datetime) -> None:
    dur = int((datetime.now(timezone.utc) - started).total_seconds())
    _persist_call(ctx.call_id, status="completed", duration=dur, summary="Scripted demo call (synthetic).",
                  language="English", user_spoken=True, messages=messages, incident_id=ctx.incident_id,
                  transferred="handoff_summary" in ctx.state)
    hub.publish("call_ended", {"call_sid": ctx.call_sid, "incident_id": ctx.incident_id, "status": "completed",
                               "duration_seconds": dur, "summary": "Scripted demo call (synthetic)."})


async def _run_steps(ctx: ToolContext, steps: list, pace: float) -> list[dict]:
    messages: list[dict] = []
    assigned: list[str] = []
    for step in steps:
        kind = step[0]
        if kind in ("caller", "agent"):
            messages.append({"role": "user" if kind == "caller" else "assistant", "content": step[1],
                             "ts": datetime.now(timezone.utc)})
            hub.publish("transcript", {"call_sid": ctx.call_sid, "role": kind, "content": step[1],
                                       "incident_id": ctx.incident_id})
            await asyncio.sleep(pace * 1.4)
        elif kind == "pause":
            await asyncio.sleep(step[1])
        else:
            name, args = step[1], step[2]
            if name == "@assign_all":  # assign the recommended unit of every search done so far
                for rid in list(ctx.state.get("_recommended", [])):
                    r = await asyncio.to_thread(execute_tool, "assign_resource", {"resource_id": rid}, ctx)
                    if r.get("status") == "rejected" and r.get("gate"):   # obey a gate exactly like the model would
                        r = await asyncio.to_thread(execute_tool, "assign_resource", {"resource_id": rid}, ctx)
                    if r.get("status") == "success":
                        assigned.append(r["resource_id"])
                        await asyncio.to_thread(execute_tool, "notify_dispatch_team",
                                                {"resource_id": r["resource_id"], "summary": "see incident"}, ctx)
                    await asyncio.sleep(pace)
                continue
            result = await asyncio.to_thread(execute_tool, name, args, ctx)
            if name == "find_nearest_resource" and result.get("recommended"):
                ctx.state.setdefault("_recommended", []).append(result["recommended"])
            await asyncio.sleep(pace)
    return messages


async def _run_scenario(key: str, pace: float) -> None:
    try:
        if key == "c":
            for i, (say, desc, sig) in enumerate(FLOOD_REPORTS):
                ctx = _new_call(f"+91900000005{i}")
                started = datetime.now(timezone.utc)
                steps = [("caller", say),
                         ("tool", "geocode_location", {"raw_location_text": "Vastrapur Lake"}),
                         ("tool", "create_incident", {"category": "flood", "description": desc, "confidence": "high"}),
                         ("tool", "check_duplicate_incident", {}),
                         ("tool", "estimate_severity", {"signals": sig})]
                msgs = await _run_steps(ctx, steps, pace)
                await asyncio.to_thread(_end_call, ctx, msgs, started)
                await asyncio.sleep(pace)
            return

        if key == "a":  # an earlier caller already reported this crash -> the live call becomes a duplicate
            prev = _new_call("+919000000090")
            pstart = datetime.now(timezone.utc)
            pm = await _run_steps(prev, [
                ("caller", "There was a big crash on SG Highway near the mall, a person is trapped in a car."),
                ("tool", "geocode_location", {"raw_location_text": "SG Highway near SG Mall"}),
                ("tool", "create_incident", {"category": "road_accident", "confidence": "high",
                                             "description": "big crash on the highway near the mall, person trapped in a car"})],
                pace)
            await asyncio.to_thread(_end_call, prev, pm, pstart)
            await asyncio.sleep(pace)

        ctx = _new_call("+919000000001" if key != "d" else "+919000000099")
        started = datetime.now(timezone.utc)
        msgs = await _run_steps(ctx, SCENARIOS[key], pace)
        await asyncio.to_thread(_end_call, ctx, msgs, started)
    except Exception:
        logger.exception("demo scenario %s failed", key)


@router.post("/demo/{scenario}", status_code=202)
async def run_demo(scenario: str, pace: float = 1.2):
    if not settings.ENABLE_DEV_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")  # indistinguishable from a missing route
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=404, detail="Unknown scenario; use a, b, c, d or e")
    task = asyncio.create_task(_run_scenario(scenario, max(0.2, min(pace, 5.0))))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return {"started": scenario, "note": "watch /dashboard/ — scripted stand-in for a live call"}


_RUNNING: set = set()
