import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { incidentInScope, resourceInScope, useLive, type Lens, type TaxonomyMap } from "@/store/live";
import type { Incident } from "@/lib/types";

/** The department currently being viewed as, or null for city-wide command. */
export const useLensDept = () => useLive((s) => (s.lens.mode === "department" ? s.lens.dept : null));
export const useLens = () => useLive((s) => s.lens);
export const useTaxonomy = () => useLive((s) => s.taxonomy);

export function useDepartmentLabel(key: string | null | undefined): string {
  const tax = useTaxonomy();
  if (!key) return "City-wide command";
  return tax.departments.find((d) => d.key === key)?.label ?? key.replaceAll("_", " ");
}

/** Incidents the current lens is allowed to see, plus the ids for cheap membership tests. */
export function useScopedIncidents() {
  const { incidents, lens, taxonomy } = useLive(useShallow((s) => ({
    incidents: s.incidents, lens: s.lens, taxonomy: s.taxonomy })));
  return useMemo(() => {
    const list = Object.values(incidents).filter((i) => incidentInScope(i, lens, taxonomy));
    return { list, ids: new Set(list.map((i) => i.id)) };
  }, [incidents, lens, taxonomy]);
}

/** Stats recomputed through the lens — a department's counters must be its own, not the city's. */
export function useScopedStats() {
  const { incidents, resources, assignments, calls, lens, taxonomy } = useLive(useShallow((s) => ({
    incidents: s.incidents, resources: s.resources, assignments: s.assignments,
    calls: s.calls, lens: s.lens, taxonomy: s.taxonomy })));

  return useMemo(() => {
    const inc = Object.values(incidents).filter((i) => incidentInScope(i, lens, taxonomy));
    const ids = new Set(inc.map((i) => i.id));
    const res = Object.values(resources).filter((r) => resourceInScope(r, lens, taxonomy));
    const asg = Object.values(assignments).filter((a) => lens.mode === "command" || ids.has(a.incident_id));
    const active = new Set(["open", "dispatched", "escalated"]);
    return {
      active: inc.filter((i) => active.has(i.status)).length,
      critical: inc.filter((i) => active.has(i.status) && i.severity_level === "critical").length,
      review: inc.filter((i) => i.status === "needs_review").length,
      available: res.filter((r) => r.status === "available").length,
      moving: res.filter((r) => r.status === "en_route").length,
      total: res.length,
      live: Object.values(calls).filter((c) => c.live && (lens.mode === "command" || (c.incidentId ? ids.has(c.incidentId) : false))).length,
      pending: asg.filter((a) => a.status === "pending_approval").length,
    };
  }, [incidents, resources, assignments, calls, lens, taxonomy]);
}

/** Per-department counters for the picker, so choosing a lens is an informed choice. */
export function useDepartmentCounts() {
  const { incidents, taxonomy } = useLive(useShallow((s) => ({ incidents: s.incidents, taxonomy: s.taxonomy })));
  return useMemo(() => {
    const out: Record<string, { active: number; critical: number }> = {};
    const active = new Set(["open", "dispatched", "escalated", "needs_review"]);
    for (const d of taxonomy.departments) out[d.key] = { active: 0, critical: 0 };
    for (const i of Object.values(incidents) as Incident[]) {
      if (!active.has(i.status)) continue;
      const owners = new Set([...(taxonomy.categoryDepartments[i.category] ?? []), ...(i.escalated_to ?? [])]);
      for (const d of owners) {
        const row = (out[d] ??= { active: 0, critical: 0 });
        row.active += 1;
        if (i.severity_level === "critical") row.critical += 1;
      }
    }
    return out;
  }, [incidents, taxonomy]);
}

export const scopeOf = (lens: Lens, tax: TaxonomyMap) => ({
  incident: (i: Incident) => incidentInScope(i, lens, tax),
});
