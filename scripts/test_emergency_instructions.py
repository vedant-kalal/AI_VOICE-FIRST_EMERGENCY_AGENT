"""Regression check for the assembled instructions (counterpart of ai-callcenter's
scripts/test_311_instructions.py). Run after ANY change to emergency_instructions/*.py,
incident_taxonomy.json or prompts_realtime_emergency.py:

    python scripts/test_emergency_instructions.py

For every category (+ the bare dispatcher prompt) it checks required phrases are present, no
unreplaced placeholder leaked, tool schemas are valid, and the token count stays inside budget.
Tool descriptions do NOT count against the instruction budget (same fact ai-callcenter exploits).
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("DATABASE_URL", "sqlite:///./_instr_check.db")

from app.utils import taxonomy  # noqa: E402
from app.utils.prompts_realtime_emergency import (  # noqa: E402
    CATEGORY_NAMES, GLOBAL_TOOLS, department_instruction, load_dispatcher_instructions,
)

TOKEN_LIMIT = 16384       # OpenAI's hard limit for `instructions` (same one ai-callcenter lives under)
TOKEN_TARGET = 10000      # keep real headroom — this project is not at the ceiling

REQUIRED_ALWAYS = [
    "RULE 01", "RULE 03", "RULE 05", "RULE 07", "RULE 08", "RULE 12", "RULE 16",
    "CALL STATE MACHINE", "TOOL MAP", "geocode_location", "estimate_severity",
]


def count_tokens(text: str) -> int:
    try:
        import tiktoken
        return len(tiktoken.get_encoding("o200k_base").encode(text))
    except Exception:
        return len(text) // 4  # rough fallback


def check(name: str, prompt: str, category: str = "") -> list[str]:
    problems = []
    for phrase in REQUIRED_ALWAYS:
        if phrase not in prompt:
            problems.append(f"{name}: missing '{phrase}'")
    left = re.findall(r"\{(?:caller_id_number|current_date|current_time|city|category_table)\}", prompt)
    if left:
        problems.append(f"{name}: unreplaced placeholders {set(left)}")
    if category:
        cat = taxonomy.get_category(category)
        if f'ACTIVE INCIDENT: {cat["label"]}' not in prompt:
            problems.append(f"{name}: no ACTIVE INCIDENT block")
        for q in cat["triage_questions"]:
            if q not in prompt:
                problems.append(f"{name}: triage question missing: {q[:40]}")
        for r in cat["resources"]:
            if r["type"] not in prompt:
                problems.append(f"{name}: resource '{r['type']}' missing")
        for d in taxonomy.category_departments(category):
            if f"({d})" not in prompt:
                problems.append(f"{name}: department section '{d}' missing")
        if cat.get("needs_hospital") and "HOSPITAL BRIEFING" not in prompt:
            problems.append(f"{name}: hospital briefing missing")
    tokens = count_tokens(prompt)
    if tokens > TOKEN_LIMIT:
        problems.append(f"{name}: {tokens} tokens > hard limit {TOKEN_LIMIT}")
    return problems


def check_tools() -> list[str]:
    problems, names = [], set()
    for t in GLOBAL_TOOLS:
        n = t["name"]
        if n in names:
            problems.append(f"duplicate tool {n}")
        names.add(n)
        if t.get("type") != "function" or not t.get("description"):
            problems.append(f"tool {n}: bad shape")
        params = t.get("parameters", {})
        for req in params.get("required", []):
            if req not in params.get("properties", {}):
                problems.append(f"tool {n}: required '{req}' not in properties")
        json.dumps(t)  # must be serialisable
    expected = {"geocode_location", "create_incident", "check_duplicate_incident", "estimate_severity",
                "find_nearest_resource", "get_hospital_capacity", "assign_resource", "notify_dispatch_team",
                "escalate_incident", "transfer_to_human_operator", "give_caller_safety_instructions",
                "find_nearest_department_center", "end_call"}
    if names != expected:
        problems.append(f"tool set mismatch: missing={expected - names} extra={names - expected}")
    return problems


def check_departments() -> list[str]:
    """Every department in the taxonomy has its own <key>.py file, and every category only names known departments."""
    problems = []
    tax = taxonomy.load_taxonomy()
    for key in taxonomy.department_keys():
        text = department_instruction(key)
        if not text:
            problems.append(f"department '{key}' has no emergency_instructions/{key}.py")
        elif "CAUTIONS" not in text:
            problems.append(f"department '{key}': missing CAUTIONS")
    for name, cat in tax["categories"].items():
        for d in cat["departments"] + cat["escalate_to"]:
            if d not in tax["departments"]:
                problems.append(f"category '{name}' references unknown department '{d}'")
    files = {f[:-3] for f in os.listdir(taxonomy.INSTRUCTIONS_DIR) if f.endswith(".py")}
    orphans = files - set(taxonomy.department_keys()) - {"__init__", "global_rules", "dispatcher"}
    if orphans:
        problems.append(f"instruction files with no taxonomy department: {sorted(orphans)}")
    return problems


def main() -> int:
    problems = check_tools() + check_departments()
    base = load_dispatcher_instructions("+919000000000")
    problems += check("dispatcher", base)
    print(f"dispatcher (no incident yet): {count_tokens(base)} tokens")
    worst = 0
    for cat in CATEGORY_NAMES:
        p = load_dispatcher_instructions("+919000000000", category=cat)
        problems += check(cat, p, cat)
        worst = max(worst, count_tokens(p))
    print(f"{len(CATEGORY_NAMES)} categories checked, worst case {worst}/{TOKEN_LIMIT} tokens "
          f"(target < {TOKEN_TARGET})")
    if worst > TOKEN_TARGET:
        problems.append(f"worst case {worst} exceeds target {TOKEN_TARGET}")
    for p in problems:
        print("FAIL:", p)
    print("ALL PASSED" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
