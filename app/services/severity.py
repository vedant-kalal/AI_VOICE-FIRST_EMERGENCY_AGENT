"""Rule-based severity scoring (PDF §9: "don't make the whole project depend on the LLM").

The realtime model only fills a constrained `signals` object with STATED FACTS (injuries, trapped
people, fire spread, ...). This module turns those facts into a 0-100 score with an explanation.
Accent, speech style, emotion and language are never inputs — a deliberate bias-avoidance choice.

The model may also pass `llm_severity_hint`. It can nudge the score UP by at most LLM_NUDGE_MAX; a
larger disagreement is recorded but not trusted (the rules stay in charge).
"""
from typing import Any, Optional

from app.utils.taxonomy import base_severity

LLM_NUDGE_MAX = 15

INJURY_POINTS = {"none": 0, "unknown": 5, "minor": 8, "serious": 20, "critical": 32}
WATER_POINTS = {"none": 0, "ankle": 5, "knee": 12, "waist": 22, "above_waist": 30}

BOOL_POINTS = {
    "people_trapped": (18, "people trapped"),
    "unconscious_or_not_breathing": (30, "person unconscious or not breathing"),
    "severe_bleeding": (22, "severe bleeding"),
    "fire_present": (10, "fire present"),
    "fire_spreading": (15, "fire spreading"),
    "explosion": (25, "explosion"),
    "hazardous_material": (15, "hazardous material involved"),
    "difficulty_breathing": (15, "difficulty breathing"),
    "weapon_involved": (20, "weapon involved"),
    "active_threat": (25, "active threat to life"),
    "structure_collapsed": (25, "structure collapsed"),
    "utility_hazard": (12, "utility hazard (live wire / gas)"),
    "children_or_elderly_involved": (6, "children or elderly involved"),
    "road_blocked_fully": (8, "road fully blocked"),
    "situation_worsening": (10, "situation is worsening"),
}

# Pre-arrival protocol triggers: real dispatch systems escalate these regardless of the sum.
# (label, predicate(category, signals) -> bool, floor)
PROTOCOL_FLOORS = [
    ("not breathing / unconscious", lambda c, s: s.get("unconscious_or_not_breathing"), 90),
    ("gas/chemical + difficulty breathing",
     lambda c, s: s.get("hazardous_material") and s.get("difficulty_breathing"), 90),
    ("trapped in fire", lambda c, s: s.get("people_trapped") and s.get("fire_present"), 90),
    ("explosion", lambda c, s: s.get("explosion"), 90),
    ("active threat to life", lambda c, s: s.get("active_threat"), 90),
    ("collapse with people trapped",
     lambda c, s: s.get("structure_collapsed") and s.get("people_trapped"), 90),
    ("deep floodwater with vulnerable people",
     lambda c, s: c == "flood" and s.get("water_level") in ("waist", "above_waist")
     and s.get("children_or_elderly_involved"), 88),
]


def level_for(score: int) -> str:
    if score >= 85:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def merge_signals(old: Optional[dict], new: Optional[dict]) -> dict:
    """Combine signals from repeated reports/updates: booleans OR, numbers max, ordered enums max."""
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if value in (None, "", "unknown") and key in merged:
            continue
        if isinstance(value, bool):
            merged[key] = bool(merged.get(key)) or value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            merged[key] = max(merged.get(key, 0) or 0, value)
        elif key in ("injuries", "water_level"):
            order = list(INJURY_POINTS if key == "injuries" else WATER_POINTS)
            cur = merged.get(key, "none")
            # An unrecognised value never overwrites a known one.
            if value in order and (cur not in order or order.index(value) >= order.index(cur)):
                merged[key] = value
        else:
            merged[key] = value
    return merged


def score_severity(category: str, signals: Optional[dict[str, Any]], people_affected: int = 0,
                   llm_hint: Optional[int] = None) -> dict:
    s = signals or {}
    parts: list[tuple[str, int]] = [(f"{category} baseline", base_severity(category))]

    injuries = s.get("injuries", "none")
    if injuries in INJURY_POINTS and INJURY_POINTS[injuries]:
        parts.append((f"injuries: {injuries}", INJURY_POINTS[injuries]))

    for key, (points, label) in BOOL_POINTS.items():
        if s.get(key):
            parts.append((label, points))

    water = s.get("water_level", "none")
    if category == "flood" and water in WATER_POINTS and WATER_POINTS[water]:
        parts.append((f"water level: {water}", WATER_POINTS[water]))

    n = max(int(people_affected or 0), int(s.get("people_affected", 0) or 0))
    if n >= 10:
        parts.append((f"{n} people affected", 18))
    elif n >= 5:
        parts.append((f"{n} people affected", 10))
    elif n >= 2:
        parts.append((f"{n} people affected", 5))

    score = min(100, sum(p for _, p in parts))
    explanation = [f"{label} (+{pts})" for label, pts in parts]

    for label, predicate, floor in PROTOCOL_FLOORS:
        if predicate(category, s) and score < floor:
            explanation.append(f"protocol trigger '{label}' raises severity to {floor}")
            score = floor

    disagreement = None
    if llm_hint is not None:
        hint = max(0, min(100, int(llm_hint)))
        if hint > score:
            nudged = min(hint, score + LLM_NUDGE_MAX)
            if hint - score > LLM_NUDGE_MAX:
                disagreement = f"model hint {hint} vs rules {score}; capped at +{LLM_NUDGE_MAX}"
            if nudged > score:
                explanation.append(f"model hint nudges score +{nudged - score}")
                score = nudged

    score = int(min(100, score))
    return {
        "score": score,
        "level": level_for(score),
        "explanation": explanation,
        "disagreement": disagreement,
    }
