"""session.update construction + the state that decides when to send one (app/utils/realtime_session.py) and the
per-department instruction files (emergency_instructions/<department>.py)."""
import json

import pytest

from app.utils import taxonomy
from app.utils.prompts_realtime_emergency import (
    GLOBAL_TOOLS, department_instruction, format_department_context, load_dispatcher_instructions,
)
from app.utils.realtime_session import (
    PromptState, build_initial_session, instructions_update_event, session_update_event,
)


def test_initial_session_has_everything_and_only_it_has_tools():
    s = build_initial_session("PROMPT", GLOBAL_TOOLS, "marin")
    assert s["type"] == "realtime" and s["output_modalities"] == ["audio"] and s["tool_choice"] == "auto"
    assert len(s["tools"]) == 13 and s["instructions"] == "PROMPT"
    inp = s["audio"]["input"]
    assert inp["format"] == {"type": "audio/pcmu"} and inp["noise_reduction"] == {"type": "near_field"}
    assert inp["turn_detection"] == {"type": "semantic_vad", "eagerness": "auto",
                                     "create_response": True, "interrupt_response": True}   # barge-in enabled
    assert s["audio"]["output"] == {"format": {"type": "audio/pcmu"}, "voice": "marin"}
    text_only = build_initial_session("P", GLOBAL_TOOLS, "marin", modalities=("text",))
    assert "audio" not in text_only and text_only["output_modalities"] == ["text"]
    assert json.loads(session_update_event(s))["type"] == "session.update"


def test_instructions_update_is_a_partial_merge():
    ev = json.loads(instructions_update_event("NEW"))
    assert ev == {"type": "session.update", "session": {"type": "realtime", "instructions": "NEW"}}
    assert "tools" not in ev["session"] and "audio" not in ev["session"]


def test_prompt_state_only_returns_a_prompt_when_the_context_changes():
    st = PromptState("+919812345678")
    assert "ACTIVE DEPARTMENT" not in st.prompt and st.updates_sent == 0      # nothing loaded before an incident
    p1 = st.apply("fire")
    assert p1 and "DEPARTMENT: Fire & Rescue Services (fire_dept)" in p1 and "ACTIVE INCIDENT: Fire" in p1
    assert st.apply("fire") is None and st.updates_sent == 1                     # same context -> no re-send
    p2 = st.apply("fire", ["fire_dept", "police", "supervisor"])                 # escalation adds departments
    assert p2 and "(police)" in p2 and "(supervisor)" in p2 and st.updates_sent == 2
    p3 = st.apply("medical")                                                     # merged into a medical incident
    assert p3 and "(ems)" in p3 and "(fire_dept)" not in p3 and "ACTIVE INCIDENT: Medical Emergency" in p3
    assert st.apply("not_a_category") is None and st.apply("") is None


def test_prompt_state_caps_departments():
    st = PromptState()
    st.apply("industrial_chemical", ["fire_dept", "hazmat", "pollution_control", "police", "supervisor", "ems"])
    assert len(st.departments) == taxonomy.MAX_ACTIVE_DEPARTMENTS == 4


def test_every_taxonomy_department_has_its_own_instruction_file():
    for key in taxonomy.department_keys():
        text = department_instruction(key)
        assert text.startswith("========== DEPARTMENT:") and f"({key})" in text, key
        assert "CAUTIONS" in text, key                                            # every department states its limits
        assert "{" not in text and "}" not in text, key                           # no stray placeholders
    assert department_instruction("nope") == ""


def test_category_prompt_contains_exactly_its_departments():
    p = load_dispatcher_instructions("+919812345678", category="road_accident")
    assert "(traffic_police)" in p and "(ems)" in p
    assert "(fire_dept)" not in p and "(hazmat)" not in p
    assert p.index("SECTION 20 — ACTIVE DEPARTMENT(S)") < p.index("ACTIVE INCIDENT: Road Accident")
    assert "Lead department for this incident: Traffic Police" in p
    assert format_department_context([]) == ""
    assert "(police)" not in load_dispatcher_instructions("+91", category="fire")   # unrelated dept absent
