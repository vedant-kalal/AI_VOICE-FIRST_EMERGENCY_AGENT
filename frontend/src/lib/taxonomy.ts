// Project vocabulary — mirrors emergency_instructions/question_sets/incident_taxonomy.json,
// app/models/resource.py (RESOURCE_TYPES) and the 13-tool contract in app/services/tool_executor.py.
import type { SeverityLevel } from "./types";

export const CATEGORY: Record<string, { label: string; short: string }> = {
  fire: { label: "Fire", short: "FIRE" },
  flood: { label: "Flood", short: "FLOOD" },
  road_accident: { label: "Road accident", short: "RTA" },
  road_blockage: { label: "Road blockage", short: "BLOCK" },
  medical: { label: "Medical emergency", short: "MED" },
  industrial_chemical: { label: "Industrial / chemical", short: "HAZMAT" },
  building_collapse: { label: "Building collapse", short: "COLLAPSE" },
  natural_disaster: { label: "Natural disaster", short: "DISASTER" },
  crime: { label: "Crime in progress", short: "CRIME" },
  utility_failure: { label: "Utility failure", short: "UTILITY" },
  missing_person: { label: "Missing person", short: "SAR" },
  animal_rescue: { label: "Animal emergency", short: "ANIMAL" },
  public_disturbance: { label: "Public disturbance", short: "PUBLIC" },
  other: { label: "Other emergency", short: "OTHER" },
};
export const categoryLabel = (k: string) => CATEGORY[k]?.label ?? k.replaceAll("_", " ");

export const DEPARTMENT: Record<string, string> = {
  supervisor: "Duty supervisor",
  fire_dept: "Fire & Emergency",
  hazmat: "Hazmat",
  police: "Police",
  ems: "EMS / 108",
  traffic_police: "Traffic police",
  disaster_management: "Disaster mgmt",
  municipal_corp: "Municipal corp.",
  utility_board: "Utility board",
  pollution_control: "Pollution control",
  forest_dept: "Forest dept.",
};
export const deptLabel = (k: string) => DEPARTMENT[k] ?? k.replaceAll("_", " ");

export const RESOURCE: Record<string, { code: string; label: string }> = {
  ambulance: { code: "AMB", label: "Ambulance" },
  fire_truck: { code: "FIR", label: "Fire engine" },
  police_unit: { code: "POL", label: "Police unit" },
  rescue_team: { code: "RSQ", label: "Rescue team" },
  flood_rescue_boat: { code: "BOT", label: "Flood boat" },
  hazmat_team: { code: "HAZ", label: "Hazmat team" },
  tow_truck: { code: "TOW", label: "Tow truck" },
  helicopter: { code: "HEL", label: "Helicopter" },
};

export const SEVERITY_COLOR: Record<SeverityLevel | "none", string> = {
  critical: "var(--color-flare)",
  high: "var(--color-ember)",
  medium: "var(--color-sodium)",
  low: "var(--color-sage)",
  none: "var(--color-bone-faint)",
};
export const levelOf = (score: number): SeverityLevel =>
  score >= 85 ? "critical" : score >= 65 ? "high" : score >= 40 ? "medium" : "low";

export const UNIT_STATUS_COLOR: Record<string, string> = {
  available: "var(--color-ice)",
  reserved: "var(--color-sodium)",
  en_route: "var(--color-ember)",
  on_scene: "var(--color-sage)",
  busy: "var(--color-bone-faint)",
  offline: "var(--color-bone-faint)",
};

export const STATUS_LABEL: Record<string, string> = {
  open: "Open",
  dispatched: "Dispatched",
  escalated: "Escalated",
  needs_review: "Needs review",
  resolved: "Resolved",
  closed: "Closed",
  merged: "Merged",
};

/** The agent's function-calling contract, in the order a good call walks it. */
export const PIPELINE: { tool: string; verb: string; hint: string }[] = [
  { tool: "geocode_location", verb: "Locate", hint: "Gazetteer / Nominatim, caller read-back" },
  { tool: "create_incident", verb: "Log", hint: "Category enum + confidence" },
  { tool: "check_duplicate_incident", verb: "De-dupe", hint: "500 m · 60 min merge window" },
  { tool: "estimate_severity", verb: "Triage", hint: "Rules on stated facts, not tone" },
  { tool: "give_caller_safety_instructions", verb: "Guide", hint: "Protocol steps for the caller" },
  { tool: "find_nearest_resource", verb: "Search", hint: "Proximity · capability · ETA · load" },
  { tool: "assign_resource", verb: "Dispatch", hint: "Code-gated atomic claim" },
  { tool: "notify_dispatch_team", verb: "Alert", hint: "Crew notification" },
  { tool: "get_hospital_capacity", verb: "Hospital", hint: "Beds, ICU, specialty" },
  { tool: "find_nearest_department_center", verb: "Centre", hint: "Overpass / Places + OSRM" },
  { tool: "escalate_incident", verb: "Escalate", hint: "Add departments" },
  { tool: "transfer_to_human_operator", verb: "Hand-off", hint: "Twilio escalation ladder" },
];
export const PIPELINE_INDEX = Object.fromEntries(PIPELINE.map((p, i) => [p.tool, i]));

export const SCENARIOS = [
  { key: "a", title: "Highway crash", place: "SG Highway · SG Mall", cat: "road_accident",
    beats: ["Earlier caller logs the crash", "Live call merges as duplicate", "Ambulance + rescue in parallel"] },
  { key: "b", title: "Gas leak", place: "Naroda GIDC", cat: "industrial_chemical",
    beats: ["Hazmat + breathing signals", "Auto-escalation ≥ 85", "Fire + hazmat dispatched"] },
  { key: "c", title: "Flood cluster", place: "Vastrapur Lake", cat: "flood",
    beats: ["Five separate callers", "One incident, rising severity", "Escalates as water hits waist"] },
  { key: "d", title: "Prank caller", place: "No location", cat: "other",
    beats: ["Contradictory, joking answers", "Parked in human review", "Nothing dispatched"] },
  { key: "e", title: "Factory fire", place: "Thaltej Cross Road", cat: "fire",
    beats: ["Three trapped inside", "Nearest fire station lookup", "Live transfer to fire ladder"] },
] as const;
