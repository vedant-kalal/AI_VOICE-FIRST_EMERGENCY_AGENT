import { beforeEach, describe, expect, it, vi } from "vitest";
import { fx, incidentInScope, resourceInScope, useLive, type Lens, type TaxonomyMap } from "./live";
import { ago, parseTs } from "@/lib/format";
import type { HubEvent } from "@/lib/types";

const inc = (over: Record<string, unknown> = {}) => ({
  id: "i1", incident_number: 1001, category: "road_accident", sub_type: null, description: "crash",
  address_text: "SG Highway", lat: 23.05, lng: 72.53, location_confirmed: false, severity: 40,
  severity_level: "medium", severity_explanation: null, status: "open", confidence: "high",
  confidence_reasons: [], people_affected: 2, report_count: 1, escalated: false, live_call: true,
  summary: null, merged_into_id: null, created_at: "2026-09-19T11:00:00", ...over,
});
const ev = (type: string, data: Record<string, unknown>, ts = new Date().toISOString()): HubEvent => ({ type, ts, data });

beforeEach(() => {
  useLive.setState({ incidents: {}, resources: {}, assignments: {}, calls: {}, wire: [], flashAt: {}, focusSid: null, pinnedFocus: false });
});

describe("live store", () => {
  it("builds a call from call_started, transcript and tool_call events", () => {
    const { apply } = useLive.getState();
    apply(ev("call_started", { call_sid: "CA1", from: "+919000000001" }), false);
    apply(ev("transcript", { call_sid: "CA1", role: "caller", content: "Help!" }), false);
    apply(ev("tool_call", { call_sid: "CA1", tool: "geocode_location", status: "success", arguments: {}, result: { found: true } }), false);
    const c = useLive.getState().calls.CA1;
    expect(c.live).toBe(true);
    expect(c.lines.map((l) => l.content)).toEqual(["Help!"]);
    expect(c.tools[0].tool).toBe("geocode_location");
    expect(useLive.getState().focusSid).toBe("CA1");
  });

  it("drops merged incidents and keeps the primary", () => {
    const { apply } = useLive.getState();
    apply(ev("incident_created", inc()), true);
    apply(ev("incident_created", inc({ id: "i2", incident_number: 1002 })), true);
    apply(ev("incident_merged", inc({ id: "i2", incident_number: 1002, status: "merged", merged_into_id: "i1" })), true);
    expect(Object.keys(useLive.getState().incidents)).toEqual(["i1"]);
  });

  it("completes an incident's assignments when it is resolved", () => {
    const { apply } = useLive.getState();
    apply(ev("incident_created", inc()), true);
    apply(ev("assignment_created", { assignment_id: "a1", incident_id: "i1", resource_id: "r1", callsign: "AMB-A",
                                     type: "ambulance", status: "pending_approval", eta_minutes: 6 }), true);
    expect(useLive.getState().assignments.a1.status).toBe("pending_approval");
    apply(ev("incident_updated", inc({ status: "resolved" })), true);
    expect(useLive.getState().assignments.a1.status).toBe("completed");
  });

  it("plays map effects only for fresh events, never for the replayed backlog", () => {
    const seen = vi.fn();
    const off = fx.on(seen);
    const search = { incident_id: null, considered: [], ranked: [], search: { lat: 23, lng: 72.5, radius_km: 15, resource_type: "ambulance" } };
    useLive.getState().apply(ev("resource_search", search), true);
    expect(seen).not.toHaveBeenCalled();
    useLive.getState().apply(ev("resource_search", search), false);
    expect(seen).toHaveBeenCalledWith(expect.objectContaining({ kind: "sweep", radiusKm: 15 }));
    off();
  });
});

describe("department lens", () => {
  const tax: TaxonomyMap = {
    departments: [{ key: "fire_dept", label: "Fire & Rescue Services" }, { key: "ems", label: "EMS" }],
    categoryDepartments: { fire: ["fire_dept"], road_accident: ["traffic_police", "ems"] },
    categoryLabels: { fire: "Fire", road_accident: "Road accident" },
    departmentResources: { fire_dept: ["fire_truck", "ambulance"], ems: ["ambulance"] },
  };
  const fire: Lens = { mode: "department", dept: "fire_dept" };
  const command: Lens = { mode: "command", dept: null };

  it("shows a department the incidents it owns", () => {
    expect(incidentInScope(inc({ category: "fire" }) as never, fire, tax)).toBe(true);
    expect(incidentInScope(inc({ category: "road_accident" }) as never, fire, tax)).toBe(false);
  });

  it("also shows incidents escalated into it", () => {
    const escalated = inc({ category: "road_accident", escalated: true, escalated_to: ["fire_dept"] });
    expect(incidentInScope(escalated as never, fire, tax)).toBe(true);
  });

  it("shows command everything", () => {
    expect(incidentInScope(inc({ category: "road_accident" }) as never, command, tax)).toBe(true);
  });

  it("scopes units to the resource types a department works with", () => {
    const unit = (type: string) => ({ id: "r", callsign: "X", type, status: "available", lat: 0, lng: 0,
                                      capabilities: [], is_synthetic: true }) as never;
    expect(resourceInScope(unit("fire_truck"), fire, tax)).toBe(true);
    expect(resourceInScope(unit("tow_truck"), fire, tax)).toBe(false);
    expect(resourceInScope(unit("tow_truck"), command, tax)).toBe(true);
  });

  it("drops a selected incident the new lens cannot see", () => {
    useLive.setState({ taxonomy: tax });
    useLive.getState().apply(ev("incident_created", inc({ category: "road_accident" })), true);
    useLive.getState().select("i1");
    useLive.getState().setLens(fire);
    expect(useLive.getState().selectedId).toBeNull();
    useLive.getState().setLens(command);
  });
});

describe("time", () => {
  it("reads offset-less backend timestamps as UTC", () => {
    expect(parseTs("2026-09-19T11:00:00")).toBe(Date.UTC(2026, 8, 19, 11, 0, 0));
    expect(parseTs("2026-09-19T11:00:00+00:00")).toBe(Date.UTC(2026, 8, 19, 11, 0, 0));
    expect(parseTs("2026-09-19T16:30:00+05:30")).toBe(Date.UTC(2026, 8, 19, 11, 0, 0));
  });
  it("formats elapsed time", () => {
    const now = Date.UTC(2026, 8, 19, 12, 5, 0);
    expect(ago("2026-09-19T12:04:30", now)).toBe("30s");
    expect(ago("2026-09-19T11:00:00", now)).toBe("1h 5m");
  });
});
