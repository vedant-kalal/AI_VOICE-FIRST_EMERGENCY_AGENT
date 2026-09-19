"""Live text-mode test harness (counterpart of ai-callcenter's scripts/test_realtime_text_chat.py).

Opens a REAL websocket to OpenAI's Realtime API in TEXT modality (no audio, no Twilio), drives scripted
caller lines, and runs the model's tool calls through the SAME executor the phone handler uses
(app/services/tool_executor.py) — so what passes here is what the live agent does, minus audio.

    python scripts/test_realtime_text_chat.py a            # flagship road accident
    python scripts/test_realtime_text_chat.py all
    python scripts/test_realtime_text_chat.py --interactive # you type the caller's lines

COSTS MONEY: it calls the real Realtime API (text tokens only — far cheaper than audio, but not free) and
needs OPENAI_API_KEY. The offline test-suite (pytest tests/) needs no keys and covers the tool logic.

NOT verified against the live API by the author (it was written without spending on it) — expect to tweak
event names if OpenAI's GA schema differs. Test hygiene (ai-callcenter's `testing` skill): every row this
creates uses a TEST- call_sid; rows are deleted afterwards unless --keep is passed.
"""
import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import websockets  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models  # noqa: E402,F401
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, session_scope  # noqa: E402
from app.models.call import Call  # noqa: E402
from app.services.tool_executor import ToolContext, execute_tool  # noqa: E402
from app.utils.prompts_realtime_emergency import GLOBAL_TOOLS, GREETING_TEXT  # noqa: E402
from app.utils.realtime_session import (  # noqa: E402
    PromptState, build_initial_session, instructions_update_event, session_update_event,
)

URL = f"wss://api.openai.com/v1/realtime?model={settings.OPENAI_REALTIME_MODEL}"

# scripted caller lines + the tool ORDER we expect to see (subsequence match)
SCENARIOS = {
    "a": {
        "lines": ["Help! There's been a huge crash on the highway, someone's stuck in the car!",
                  "Near SG Mall, on SG Highway.", "Yes that's right, please hurry!",
                  "There are two people, the driver is awake but trapped."],
        "expect": ["geocode_location", "create_incident", "check_duplicate_incident", "estimate_severity",
                   "find_nearest_resource", "assign_resource"],
    },
    "b": {
        "lines": ["There's a strong smell of gas at a factory and we can't breathe!",
                  "Naroda GIDC, near the chemical units.", "Yes, that's it. About six of us feel dizzy."],
        "expect": ["geocode_location", "create_incident", "estimate_severity", "find_nearest_resource",
                   "assign_resource"],
    },
    "d": {
        "lines": ["hello test test... there's a dragon on my roof", "hahaha nothing, just testing"],
        "expect": ["transfer_to_human_operator"],
    },
}


async def run_scenario(name: str, lines: list[str], interactive: bool = False) -> tuple[list[str], list[str]]:
    sid = f"TEST-TXT-{uuid.uuid4().hex[:8]}"
    with session_scope() as db:
        row = Call(call_sid=sid, from_number="+912255550100", status="in_progress", is_live=True,
                   started_at=datetime.now(timezone.utc))
        db.add(row)
        db.flush()
        cid = row.id
    ctx = ToolContext(call_id=cid, call_sid=sid, caller_phone="+912255550100")

    state = PromptState("+912255550100")       # same live-prompt logic as the phone handler
    tools_used: list[str] = []
    outputs_pending = False

    async with websockets.connect(URL, extra_headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                                  max_size=None) as ws:
        await ws.send(session_update_event(
            build_initial_session(state.prompt, GLOBAL_TOOLS, settings.VOICE_NAME, modalities=("text",))))
        # The greeting is 'already played' on a real call — seed it so the model does not repeat it.
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": GREETING_TEXT}]}}))
        print(f"\n===== scenario {name} ({sid}) =====\nAGENT: {GREETING_TEXT}")

        async def say(line: str):
            print(f"CALLER: {line}")
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": line}]}}))
            await ws.send(json.dumps({"type": "response.create"}))

        async def drain_until_idle():
            """Process events until a response finishes WITHOUT pending tool outputs."""
            nonlocal outputs_pending
            while True:
                ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                t = ev.get("type")
                if t == "response.output_text.done":
                    print(f"AGENT: {ev.get('text', '')}")
                elif t == "response.output_audio_transcript.done":
                    print(f"AGENT: {ev.get('transcript', '')}")
                elif t == "response.function_call_arguments.done":
                    name_ = ev["name"]
                    args = json.loads(ev.get("arguments") or "{}")
                    tools_used.append(name_)
                    result = await asyncio.to_thread(execute_tool, name_, args, ctx)
                    effects = result.pop("_effects", None) or {}
                    print(f"  [tool] {name_}({json.dumps(args)[:120]}) -> {result.get('status')} "
                          f"{(result.get('message') or '')[:90]}")
                    await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                        "type": "function_call_output", "call_id": ev["call_id"],
                        "output": json.dumps(result, default=str)}}))
                    ce = effects.get("load_context")   # same live prompt swap as the phone handler
                    if ce:
                        new_prompt = state.apply(ce["category"], ce["departments"])
                        if new_prompt:
                            await ws.send(instructions_update_event(new_prompt))
                            print(f"  [session.update #{state.updates_sent}] {state.category} / {state.departments}")
                    outputs_pending = not effects.get("transfer_call")   # a transfer leaves the agent: no follow-up
                elif t == "error":
                    print("  [openai error]", ev.get("error"))
                elif t == "response.done":
                    if outputs_pending:  # ONE follow-up response for all tool calls in that response
                        outputs_pending = False
                        await ws.send(json.dumps({"type": "response.create"}))
                        continue
                    return

        if interactive:
            while True:
                line = await asyncio.to_thread(input, "you> ")
                if line.strip().lower() in ("quit", "exit", ""):
                    break
                await say(line)
                await drain_until_idle()
        else:
            for line in lines:
                await say(line)
                await drain_until_idle()
    return tools_used, [sid]


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    it = iter(actual)
    return all(any(x == y for y in it) for x in expected)


def cleanup(sids: list[str]) -> None:
    """Delete every row the TEST- calls created (raw SQL: no ORM mapper-order surprises) and free any unit
    the test dispatched."""
    with session_scope() as db:
        for sid in sids:
            p = {"sid": sid}
            call_id = db.execute(text("SELECT id FROM calls WHERE call_sid = :sid"), p).scalar()
            if call_id is None:
                continue
            inc_ids = [r[0] for r in db.execute(
                text("SELECT DISTINCT incident_id FROM agent_tool_calls WHERE call_id = :c AND incident_id IS NOT NULL"),
                {"c": call_id})] + [r[0] for r in db.execute(text("SELECT incident_id FROM calls WHERE id = :c AND incident_id IS NOT NULL"), {"c": call_id})]
            for iid in set(inc_ids):
                q = {"i": iid}
                db.execute(text("UPDATE resources SET status='available' WHERE id IN (SELECT resource_id FROM assignments WHERE incident_id = :i)"), q)
                for tbl in ("dispatch_notifications", "assignments", "escalations", "incident_reports", "agent_tool_calls"):
                    db.execute(text(f"DELETE FROM {tbl} WHERE incident_id = :i"), q)
                db.execute(text("UPDATE calls SET incident_id = NULL WHERE incident_id = :i"), q)
                db.execute(text("DELETE FROM incidents WHERE id = :i"), q)
            for tbl in ("call_transcripts", "agent_tool_calls"):
                db.execute(text(f"DELETE FROM {tbl} WHERE call_id = :c"), {"c": call_id})
            db.execute(text("DELETE FROM calls WHERE id = :c"), {"c": call_id})
        left = db.execute(text("SELECT count(*) FROM calls WHERE call_sid LIKE 'TEST-%'")).scalar()
    print(f"cleanup done — TEST- calls remaining: {left}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="a", help="a | b | d | all")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--keep", action="store_true", help="do not delete the TEST- rows afterwards")
    args = ap.parse_args()
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set — this harness calls the real Realtime API.")
        return 2

    sids, failed = [], []
    try:
        if args.interactive:
            used, s = await run_scenario("interactive", [], interactive=True)
            sids += s
        else:
            for name in (SCENARIOS if args.scenario == "all" else [args.scenario]):
                sc = SCENARIOS[name]
                used, s = await run_scenario(name, sc["lines"])
                sids += s
                ok = _is_subsequence(sc["expect"], used)
                print(f"scenario {name}: tools = {used}\n  expected order {sc['expect']}: {'PASS' if ok else 'FAIL'}")
                if not ok:
                    failed.append(name)
    finally:
        if not args.keep:
            cleanup(sids)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
