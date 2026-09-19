import type { ToolHit } from "@/store/live";
import { categoryLabel, deptLabel } from "@/lib/taxonomy";
import { humanize } from "@/lib/format";

/** One human sentence per tool call — what the agent just decided, in the dispatcher's words. */
export function summarizeTool(t: ToolHit): string {
  const r = t.result ?? {};
  const a = t.args ?? {};
  if (t.status === "rejected" && r.gate) return `Gate held: ${humanize(String(r.gate))} — agent told to fix it first`;
  if (t.status === "error") return String(r.message ?? r.error ?? "Tool error").slice(0, 110);
  switch (t.tool) {
    case "geocode_location":
      if (a.caller_confirmed) return "Caller confirmed the location";
      return r.found ? `Pinned “${r.formatted}” · ${Math.round(Number(r.confidence ?? 0) * 100)}% via ${r.provider}` : "Couldn't place it — asking for a landmark";
    case "create_incident":
      if (r.flagged_for_review) return `Logged #${r.incident_number} as low confidence → human review`;
      return `Logged #${r.incident_number} · ${categoryLabel(String(r.category ?? a.category))} · ${r.confidence} confidence`;
    case "check_duplicate_incident":
      return r.duplicate
        ? `Same event as #${r.merged_into?.incident_number} (${r.report_count} callers) · match ${Math.round(Number(r.match_score ?? 0) * 100)}%`
        : "No matching incident nearby — new event";
    case "estimate_severity":
      return `Severity ${r.previous != null ? `${r.previous} → ` : ""}${r.severity} (${r.level})${r.auto_escalated ? " · auto-escalated" : ""}`;
    case "give_caller_safety_instructions":
      return `Safety protocol: ${humanize(String(r.protocol ?? a.category ?? ""))} · ${(r.steps ?? []).length} steps`;
    case "find_nearest_resource": {
      const c = (r.candidates ?? [])[0];
      return c ? `${c.callsign} ranked first · ${c.distance_km} km · ${Math.round(c.eta_minutes)} min` : `No ${humanize(String(a.resource_type))} in radius`;
    }
    case "assign_resource":
      if (r.already_assigned) return `${r.callsign} already assigned`;
      return r.pending_approval ? `${r.callsign} reserved — awaiting human approval` : `${r.callsign} dispatched · ETA ${Math.round(Number(r.eta_minutes))} min`;
    case "notify_dispatch_team":
      return `Crew alert to ${r.callsign} · ${humanize(String(r.delivery ?? ""))}`;
    case "get_hospital_capacity":
      return r.recommended ? `${r.recommended.name} · ${r.recommended.available_beds} beds free` : "No hospital reports capacity";
    case "find_nearest_department_center":
      return r.nearest ? `${r.nearest.name} · ${r.nearest.road_distance_km ?? r.nearest.distance_km} km (${r.provider})` : `No ${r.label ?? "centre"} found`;
    case "escalate_incident":
      return `Escalated to ${((r.escalate_to ?? a.escalate_to ?? []) as string[]).map(deptLabel).join(", ") || "supervisor"}`;
    case "transfer_to_human_operator":
      return `Handing the live call to ${deptLabel(String(r.department ?? a.department ?? "supervisor"))} · ${r.contacts_in_ladder ?? "?"} on ladder`;
    case "end_call":
      return "Call ended by agent";
    default:
      return humanize(t.tool);
  }
}
