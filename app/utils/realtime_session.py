"""Every payload sent to the OpenAI Realtime API as `session.update`, and the state that decides WHEN to send one.

Two kinds of update, exactly as in ai-callcenter's handler (§2 of its memory):

  1. INITIAL (once, at session start): the full session object — audio formats, transcription, semantic VAD with
     barge-in, the voice, and ALL tools. This is the ONLY time `tools` is ever sent.
  2. INSTRUCTIONS-ONLY (many times, mid-call): `{"type": "session.update", "session": {"type": "realtime",
     "instructions": <prompt>}}`. session.update is a partial merge, so tools/audio stay untouched and the
     conversation history is preserved — the caller never notices the swap.

`PromptState` tracks which category + departments are loaded and returns a new prompt ONLY when that set actually
changes, so a repeated create_incident / a re-run duplicate check never re-sends an identical 6k-token prompt.

Used by the phone handler and by scripts/test_realtime_text_chat.py, so both configure the model identically.
"""
import json
from typing import Optional

from app.utils import taxonomy
from app.utils.prompts_realtime_emergency import load_dispatcher_instructions

TRANSCRIPTION_PROMPT = (
    "Transcribe only. Never translate. Emergency call — place names, road names and landmarks are common; keep "
    "them exactly as spoken."
)  # no language forced: callers may speak Hindi, Gujarati, English... (global Rule 10)


def build_initial_session(prompt: str, tools: list, voice: str, modalities: tuple[str, ...] = ("audio",)) -> dict:
    """The full session object for the FIRST session.update. `modalities=("text",)` gives the text-only test harness."""
    session: dict = {
        "type": "realtime",
        "output_modalities": list(modalities),
        "instructions": prompt,
        "tools": tools,
        "tool_choice": "auto",
    }
    if "audio" in modalities:
        session["audio"] = {
            "input": {
                "format": {"type": "audio/pcmu"},  # Twilio Media Streams: 8 kHz mu-law
                "transcription": {"model": "gpt-4o-transcribe", "prompt": TRANSCRIPTION_PROMPT},
                "noise_reduction": {"type": "near_field"},
                # semantic_vad judges turn-end from the WORDS spoken, so a panicked caller talking continuously is
                # never mistaken for finished. interrupt_response enables barge-in (handled in the call handler).
                "turn_detection": {"type": "semantic_vad", "eagerness": "auto",
                                   "create_response": True, "interrupt_response": True},
            },
            "output": {"format": {"type": "audio/pcmu"}, "voice": voice},
        }
    return session


def session_update_event(session: dict) -> str:
    return json.dumps({"type": "session.update", "session": session})


def instructions_update_event(prompt: str) -> str:
    """Instructions ONLY — tools/audio are left as configured at session start (partial merge)."""
    return json.dumps({"type": "session.update", "session": {"type": "realtime", "instructions": prompt}})


class PromptState:
    """What is currently loaded into the live session, and the rule for changing it."""

    def __init__(self, from_number: str = "", loader=load_dispatcher_instructions):
        self.from_number = from_number
        self._loader = loader
        self.category: str = ""
        self.departments: list[str] = []
        self.prompt: str = loader(from_number)
        self.updates_sent = 0

    def apply(self, category: str, departments: Optional[list[str]] = None) -> Optional[str]:
        """Switch to `category` + `departments`. Returns the NEW prompt if it differs from what is loaded (send it
        with instructions_update_event), or None when nothing changed. Ignores unknown categories."""
        if not category or not taxonomy.get_category(category):
            return None
        wanted = list(dict.fromkeys(departments or taxonomy.category_departments(category)))
        wanted = wanted[: taxonomy.MAX_ACTIVE_DEPARTMENTS]
        if category == self.category and wanted == self.departments:
            return None
        self.category, self.departments = category, wanted
        self.prompt = self._loader(self.from_number, category=category, departments=wanted)
        self.updates_sent += 1
        return self.prompt
