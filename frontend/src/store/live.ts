import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { Assignment, Facility, HubEvent, Incident, Resource, StateSnapshot } from "@/lib/types";
import { categoryLabel, deptLabel, RESOURCE } from "@/lib/taxonomy";
import { maskPhone, mins } from "@/lib/format";

// ─────────────────────────────── transient map effects ───────────────────────────────
// Radar sweeps, candidate rays and facility pings are moments, not state: the map listens and plays them.
export type Fx =
  | { kind: "sweep"; lat: number; lng: number; radiusKm: number;
      rays: { lat: number; lng: number; decision: string; callsign: string }[] }
  | { kind: "places"; lat: number; lng: number; radiusKm: number; label: string;
      points: { lat: number; lng: number; name: string; best: boolean }[] }
  | { kind: "fly"; lat: number; lng: number; zoom?: number };

type FxListener = (fx: Fx) => void;
const fxListeners = new Set<FxListener>();
export const fx = {
  on(fn: FxListener) { fxListeners.add(fn); return () => { fxListeners.delete(fn); }; },
  emit(e: Fx) { fxListeners.forEach((fn) => fn(e)); },
};

// ─────────────────────────────── state ───────────────────────────────
export interface Line { id: number; role: "caller" | "agent"; content: string; ts: string; fresh: boolean }
export interface ToolHit {
  id: number; tool: string; status: string; duration_ms: number | null;
  args: Record<string, any>; result: Record<string, any>; ts: string; fresh: boolean;
}
export interface LiveCall {
  sid: string; from: string | null; startedAt: string; endedAt: string | null; live: boolean;
  incidentId: string | null; lines: Line[]; tools: ToolHit[];
  handoff: { department: string; ladder: { name: string; role: string }[]; status: string; step: number } | null;
}
export type Tone = "flare" | "ember" | "sodium" | "sage" | "ice" | "bone";
export interface WireItem { id: number; ts: string; tone: Tone; title: string; detail?: string; incidentId?: string | null }

type Conn = "connecting" | "open" | "closed";

interface LiveState {
  conn: Conn;
  hydrated: boolean;
  mode: string;
  criticalSeverity: number;
  center: { lat: number; lng: number };
  incidents: Record<string, Incident>;
  resources: Record<string, Resource>;
  facilities: Facility[];
  assignments: Record<string, Assignment>;
  calls: Record<string, LiveCall>;
  focusSid: string | null;
  pinnedFocus: boolean;
  wire: WireItem[];
  selectedId: string | null;
  flashAt: Record<string, number>;

  setConn(c: Conn): void;
  hydrate(s: StateSnapshot): void;
  apply(ev: HubEvent, replay: boolean): void;
  select(id: string | null): void;
  focusCall(sid: string | null): void;
  upsertIncident(i: Incident): void;
  upsertAssignment(a: Partial<Assignment> & { id: string }): void;
}

let seq = 0;
const WIRE_MAX = 80;

export const useLive = create<LiveState>((set, get) => {
  const wire = (item: Omit<WireItem, "id">) =>
    set((s) => ({ wire: [{ ...item, id: ++seq }, ...s.wire].slice(0, WIRE_MAX) }));
  const flash = (id: string | null | undefined) =>
    id && set((s) => ({ flashAt: { ...s.flashAt, [id]: Date.now() } }));
  const incNo = (id: string | null | undefined) => {
    const i = id ? get().incidents[id] : undefined;
    return i ? `#${i.incident_number}` : "incident";
  };
  const patchCall = (sid: string, fn: (c: LiveCall) => LiveCall) =>
    set((s) => {
      const c = s.calls[sid] ?? {
        sid, from: null, startedAt: new Date().toISOString(), endedAt: null, live: true,
        incidentId: null, lines: [], tools: [], handoff: null,
      };
      return { calls: { ...s.calls, [sid]: fn(c) } };
    });

  return {
    conn: "connecting",
    hydrated: false,
    mode: "autonomous",
    criticalSeverity: 85,
    center: { lat: 23.0225, lng: 72.5714 },
    incidents: {},
    resources: {},
    facilities: [],
    assignments: {},
    calls: {},
    focusSid: null,
    pinnedFocus: false,
    wire: [],
    selectedId: null,
    flashAt: {},

    setConn: (conn) => set({ conn }),

    hydrate: (s) =>
      set((prev) => ({
        hydrated: true,
        mode: s.dispatch_mode,
        criticalSeverity: s.critical_severity,
        center: s.center,
        incidents: Object.fromEntries(s.incidents.map((i) => [i.id, i])),
        resources: Object.fromEntries(s.resources.map((r) => [r.id, r])),
        facilities: s.facilities,
        assignments: Object.fromEntries(s.assignments.map((a) => [a.id, a])),
        // keep call transcripts built from the replay backlog; the snapshot has no transcripts
        calls: prev.calls,
      })),

    upsertIncident: (i) =>
      set((s) => {
        const incidents = { ...s.incidents };
        if (i.status === "merged") delete incidents[i.id];
        else incidents[i.id] = { ...incidents[i.id], ...i };
        return { incidents };
      }),

    upsertAssignment: (a) =>
      set((s) => ({ assignments: { ...s.assignments, [a.id]: { ...(s.assignments[a.id] as Assignment), ...a } } })),

    select: (selectedId) => set({ selectedId }),
    focusCall: (sid) => set({ focusSid: sid, pinnedFocus: sid !== null }),

    apply: (ev, replay) => {
      const d = ev.data as Record<string, any>;
      const fresh = !replay;
      const s = get();
      switch (ev.type) {
        case "call_started": {
          patchCall(d.call_sid, (c) => ({ ...c, from: d.from ?? c.from, startedAt: ev.ts, live: true }));
          if (!s.pinnedFocus || !s.focusSid || !s.calls[s.focusSid]?.live) set({ focusSid: d.call_sid, pinnedFocus: false });
          wire({ ts: ev.ts, tone: "flare", title: "Line open", detail: maskPhone(d.from) });
          break;
        }
        case "transcript": {
          patchCall(d.call_sid, (c) => ({
            ...c, incidentId: d.incident_id ?? c.incidentId,
            lines: [...c.lines, { id: ++seq, role: d.role === "caller" ? "caller" : "agent", content: d.content, ts: ev.ts, fresh }],
          }));
          break;
        }
        case "tool_call": {
          patchCall(d.call_sid, (c) => ({
            ...c, incidentId: d.incident_id ?? c.incidentId,
            tools: [...c.tools, { id: ++seq, tool: d.tool, status: d.status ?? "success", duration_ms: d.duration_ms,
                                  args: d.arguments ?? {}, result: d.result ?? {}, ts: ev.ts, fresh }],
          }));
          break;
        }
        case "call_ended": {
          if (s.calls[d.call_sid]) {
            patchCall(d.call_sid, (c) => ({ ...c, live: false, endedAt: ev.ts, incidentId: d.incident_id ?? c.incidentId }));
          }
          wire({ ts: ev.ts, tone: "bone", title: "Line closed", detail: d.duration_seconds != null ? `${d.duration_seconds}s · ${incNo(d.incident_id)}` : undefined, incidentId: d.incident_id });
          break;
        }
        case "incident_created":
        case "incident_updated":
        case "incident_merged": {
          const before = s.incidents[d.id] as Incident | undefined;
          get().upsertIncident(d as Incident);
          // POST /status completes the incident's assignments server-side but only broadcasts the incident,
          // so mirror that here or dispatch links and the approval count go stale.
          if (d.status === "resolved" || d.status === "closed") {
            set((st) => ({ assignments: Object.fromEntries(Object.entries(st.assignments).map(([k, a]) =>
              [k, a.incident_id === d.id && a.status !== "rejected" ? { ...a, status: "completed" } : a])) }));
          }
          if (ev.type === "incident_created") {
            wire({ ts: ev.ts, tone: d.status === "needs_review" ? "sodium" : "ember",
                   title: `#${d.incident_number} ${categoryLabel(d.category)}`,
                   detail: d.status === "needs_review" ? "Low confidence → human review" : d.address_text ?? "location pending",
                   incidentId: d.id });
            flash(d.id);
            if (fresh && d.lat != null) fx.emit({ kind: "fly", lat: d.lat, lng: d.lng, zoom: 13.4 });
          } else if (ev.type === "incident_merged") {
            const primary = d.merged_into_id ? get().incidents[d.merged_into_id] : undefined;
            wire({ ts: ev.ts, tone: "ice", title: `#${d.incident_number} merged`,
                   detail: primary ? `into #${primary.incident_number} · same event` : "duplicate report",
                   incidentId: d.merged_into_id });
            flash(d.merged_into_id);
          } else if (before) {
            if (d.severity > before.severity && d.severity_level !== before.severity_level) {
              wire({ ts: ev.ts, tone: d.severity_level === "critical" ? "flare" : "ember",
                     title: `#${d.incident_number} → ${d.severity_level}`, detail: `severity ${before.severity} → ${d.severity}`, incidentId: d.id });
              flash(d.id);
            }
            if (before.lat == null && d.lat != null && fresh) fx.emit({ kind: "fly", lat: d.lat, lng: d.lng, zoom: 13.4 });
          }
          break;
        }
        case "escalation": {
          const to = (d.escalate_to ?? d.escalated_to ?? []) as string[];
          wire({ ts: ev.ts, tone: "flare", title: `Escalated #${d.incident_number ?? ""}`.trim(),
                 detail: to.length ? to.map(deptLabel).join(" · ") : String(d.reason ?? "").slice(0, 60), incidentId: d.incident_id });
          flash(d.incident_id);
          break;
        }
        case "assignment_created": {
          const id = d.assignment_id ?? d.id;
          get().upsertAssignment({ id, incident_id: d.incident_id, resource_id: d.resource_id, callsign: d.callsign,
                                   type: d.type, status: d.status, eta_minutes: d.eta_minutes,
                                   distance_km: d.distance_km, reason: d.reason });
          const r = s.resources[d.resource_id];
          if (r) set((st) => ({ resources: { ...st.resources, [r.id]: { ...r, status: d.status === "pending_approval" ? "reserved" : "en_route" } } }));
          wire({ ts: ev.ts, tone: d.status === "pending_approval" ? "sodium" : "ice",
                 title: d.status === "pending_approval" ? `${d.callsign} awaiting approval` : `${d.callsign} dispatched`,
                 detail: `${incNo(d.incident_id)} · ETA ${mins(d.eta_minutes)}`, incidentId: d.incident_id });
          break;
        }
        case "assignment_updated": {
          get().upsertAssignment(d as Assignment);
          wire({ ts: ev.ts, tone: "bone", title: `${d.callsign} ${d.status}`, incidentId: d.incident_id });
          break;
        }
        case "resource_updated": {
          set((st) => ({ resources: { ...st.resources, [d.id]: d as Resource } }));
          break;
        }
        case "resource_moved": {
          const r = s.resources[d.resource_id];
          if (r) set((st) => ({ resources: { ...st.resources, [r.id]: { ...r, lat: d.lat, lng: d.lng, status: d.status } } }));
          const a = s.assignments[d.assignment_id];
          if (a && a.status !== d.assignment_status) {
            get().upsertAssignment({ id: a.id, status: d.assignment_status });
            if (d.assignment_status === "arrived")
              wire({ ts: ev.ts, tone: "sage", title: `${d.callsign} on scene`, detail: incNo(d.incident_id), incidentId: d.incident_id });
          }
          break;
        }
        case "resource_search": {
          const considered = (d.considered ?? []) as any[];
          const sel = considered.find((c) => c.decision === "selected");
          const typ = d.search?.resource_type as string | undefined;
          wire({ ts: ev.ts, tone: "ice", title: `Scanned ${considered.length} ${RESOURCE[typ ?? ""]?.label.toLowerCase() ?? "units"}${considered.length === 1 ? "" : "s"}`,
                 detail: sel ? `${sel.callsign} ranked first · ${sel.distance_km} km` : "none available in radius", incidentId: d.incident_id });
          if (fresh && d.search)
            fx.emit({ kind: "sweep", lat: d.search.lat, lng: d.search.lng, radiusKm: d.search.radius_km,
                      rays: considered.map((c) => ({ lat: c.lat, lng: c.lng, decision: c.decision, callsign: c.callsign })) });
          break;
        }
        case "facility_search": {
          const centers = (d.centers ?? []) as any[];
          wire({ ts: ev.ts, tone: "sage", title: `Nearest ${d.label ?? "centre"}`,
                 detail: centers[0] ? `${centers[0].name} · ${centers[0].road_distance_km ?? centers[0].distance_km} km${d.fallback ? " (fallback)" : ""}` : "none found",
                 incidentId: d.incident_id });
          if (fresh && d.search)
            fx.emit({ kind: "places", lat: d.search.lat, lng: d.search.lng, radiusKm: d.search.radius_km, label: d.label,
                      points: centers.filter((c) => c.lat != null).map((c, i) => ({ lat: c.lat, lng: c.lng, name: c.name, best: i === 0 })) });
          break;
        }
        case "hospital_query": {
          const best = d.recommended as any;
          wire({ ts: ev.ts, tone: "sage", title: `Hospital check · ${d.required_specialty ?? "general"}`,
                 detail: best ? `${best.name} · ${best.available_beds} beds · ${best.available_icu} ICU` : "no capacity nearby", incidentId: d.incident_id });
          if (fresh && d.lat != null)
            fx.emit({ kind: "places", lat: d.lat, lng: d.lng, radiusKm: 0, label: "Hospitals",
                      points: ((d.hospitals ?? []) as any[]).map((h) => ({ lat: h.lat, lng: h.lng, name: h.name, best: best?.facility_id === h.facility_id })) });
          break;
        }
        case "notification": {
          wire({ ts: ev.ts, tone: "bone", title: `Crew alert · ${d.callsign}`, detail: String(d.status ?? "").replaceAll("_", " "), incidentId: d.incident_id });
          break;
        }
        case "human_handoff": {
          if (d.call_sid)
            patchCall(d.call_sid, (c) => ({ ...c, handoff: { department: d.department, ladder: d.ladder ?? [], status: "preparing", step: 0 } }));
          wire({ ts: ev.ts, tone: "sodium", title: `Transfer → ${deptLabel(d.department)}`,
                 detail: `${(d.ladder ?? []).length} contacts on the ladder`, incidentId: d.incident_id });
          flash(d.incident_id);
          break;
        }
        case "handoff_update": {
          const sid = Object.values(get().calls).find((c) => c.handoff && c.incidentId === d.incident_id)?.sid;
          if (sid) patchCall(sid, (c) => ({ ...c, handoff: c.handoff && { ...c.handoff, status: d.status, step: d.current_step ?? 0 } }));
          wire({ ts: ev.ts, tone: "sodium", title: `Ladder ${d.status}`, detail: `step ${(d.current_step ?? 0) + 1} of ${d.total_steps}`, incidentId: d.incident_id });
          break;
        }
        default:
          break;
      }
    },
  };
});

// ─────────────────────────────── selectors ───────────────────────────────
export const ACTIVE = new Set(["open", "dispatched", "escalated"]);

export function useStats() {
  return useLive(useShallow((s: LiveState) => {
    const inc = Object.values(s.incidents);
    const res = Object.values(s.resources);
    return {
      active: inc.filter((i) => ACTIVE.has(i.status)).length,
      critical: inc.filter((i) => ACTIVE.has(i.status) && i.severity_level === "critical").length,
      review: inc.filter((i) => i.status === "needs_review").length,
      available: res.filter((r) => r.status === "available").length,
      moving: res.filter((r) => r.status === "en_route").length,
      total: res.length,
      live: Object.values(s.calls).filter((c) => c.live).length,
      pending: Object.values(s.assignments).filter((a) => a.status === "pending_approval").length,
    };
  }));
}
