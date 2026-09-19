"""Post-call summary (counterpart of ai-callcenter's call_summary_handler.generate_call_summary).

One chat-completion pass over the finished transcript -> short summary + caller language. Fail-open:
with no OpenAI key (or on any error) a deterministic template summary is used, so the call record and
incident summary are never empty.
"""
import json
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def fallback_summary(messages: list[dict], incident: Optional[dict]) -> dict:
    caller_lines = [m["content"] for m in messages if m.get("role") == "user"]
    gist = caller_lines[0][:160] if caller_lines else "no caller speech captured"
    if incident:
        text = (f"{incident['category'].replace('_', ' ')} reported"
                f"{' at ' + incident['address_text'] if incident.get('address_text') else ''}; "
                f"severity {incident['severity']} ({incident['severity_level']}); "
                f"status {incident['status']}. Caller said: \"{gist}\".")
    else:
        text = f"No incident was registered. Caller said: \"{gist}\"."
    return {"summary": text, "caller_language": None}


async def generate_call_summary(messages: list[dict], incident: Optional[dict], tool_names: list[str]) -> dict:
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not convo:
        return fallback_summary(messages, incident)
    if not settings.OPENAI_API_KEY:
        return fallback_summary(messages, incident)
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in convo)[-12000:]
        resp = await client.chat.completions.create(
            model=settings.SUMMARY_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "You summarise emergency-line calls for a dispatch supervisor. Reply as JSON: "
                    '{"summary": "<=3 factual sentences: what happened, where, who is affected, what was '
                    'dispatched>", "caller_language": "<language the caller spoke>"}. State facts only; '
                    "never infer from accent or tone.")},
                {"role": "user", "content": (
                    f"Incident record: {json.dumps(incident, default=str) if incident else 'none'}\n"
                    f"Tools used: {', '.join(tool_names) or 'none'}\n\nTranscript:\n{transcript}")},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        return {"summary": data.get("summary") or fallback_summary(messages, incident)["summary"],
                "caller_language": data.get("caller_language")}
    except Exception:
        logger.exception("summary generation failed — using template summary")
        return fallback_summary(messages, incident)
