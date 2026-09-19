"""Loader for emergency_instructions/question_sets/incident_taxonomy.json — the single source of
truth for categories, resources, departments and safety protocols (ai-callcenter's 311.json role).

Loaded once and cached. The category enum, escalation departments and resource types used in tool
schemas (prompts_realtime_emergency.py) are all derived from this file, so adding a category is a
data change, not a code change.
"""
import json
import os
from functools import lru_cache
from typing import Optional

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTRUCTIONS_DIR = os.path.join(_PROJECT_ROOT, "emergency_instructions")
QUESTION_SETS_DIR = os.path.join(INSTRUCTIONS_DIR, "question_sets")
TAXONOMY_PATH = os.path.join(QUESTION_SETS_DIR, "incident_taxonomy.json")
LANDMARKS_PATH = os.path.join(QUESTION_SETS_DIR, "landmarks.json")


@lru_cache()
def load_taxonomy() -> dict:
    with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# Department sections loaded into the live prompt at once (each is ~300-450 tokens; the instructions budget is
# shared with global rules, the dispatcher persona and the ACTIVE INCIDENT block).
MAX_ACTIVE_DEPARTMENTS = 4


def category_names() -> list[str]:
    return list(load_taxonomy()["categories"].keys())


def department_keys() -> list[str]:
    return list(load_taxonomy()["departments"].keys())


def get_category(category: str) -> Optional[dict]:
    return load_taxonomy()["categories"].get(category)


def department_label(key: str) -> str:
    return load_taxonomy()["departments"].get(key, {}).get("name", key)


def base_severity(category: str) -> int:
    cat = get_category(category) or {}
    return int(cat.get("base_severity", 10))


def duplicate_radius_m(category: str, default: int = 500) -> int:
    cat = get_category(category) or {}
    return int(cat.get("duplicate_radius_m", default))


def category_departments(category: str) -> list[str]:
    """Departments that own an incident of this category, lead department first."""
    cat = get_category(category) or {}
    return list(cat.get("departments", []))


def escalation_targets(category: str) -> list[str]:
    """Departments to notify when an incident of this category is escalated (+ supervisor)."""
    cat = get_category(category) or {}
    targets = list(cat.get("escalate_to", []))
    if "supervisor" not in targets:
        targets.append("supervisor")
    return targets


def safety_protocol(category: str, situation_detail: str = "") -> dict:
    """Pick the protocol steps for a category, switching to a specific sub-protocol (CPR, bleeding,
    choking, childbirth) when the situation text contains its trigger keywords."""
    tax = load_taxonomy()
    cat = get_category(category) or get_category("other")
    protocols = cat.get("safety_protocols", {})
    text = (situation_detail or "").lower()
    chosen = "default"
    for name, keywords in tax.get("protocol_keywords", {}).items():
        if name in protocols and any(k in text for k in keywords):
            chosen = name
            break
    return {"protocol": chosen, "steps": protocols.get(chosen) or protocols.get("default", [])}
