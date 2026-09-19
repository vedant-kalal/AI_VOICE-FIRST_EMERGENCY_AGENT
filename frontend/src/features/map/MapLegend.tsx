import { SEVERITY_COLOR, UNIT_STATUS_COLOR } from "@/lib/taxonomy";

const SEV = ["critical", "high", "medium", "low"] as const;
const UNITS = [["available", "Ready"], ["reserved", "Reserved"], ["en_route", "En route"], ["on_scene", "On scene"]] as const;

export function MapLegend() {
  return (
    <div className="panel flex items-center gap-5 rounded-[14px] px-4 py-2.5">
      <div className="flex items-center gap-3">
        <span className="eyebrow">Severity</span>
        {SEV.map((s) => (
          <span key={s} className="flex items-center gap-1.5 text-[11px] text-bone-dim capitalize">
            <span className="size-2 rounded-full" style={{ background: SEVERITY_COLOR[s], boxShadow: `0 0 8px ${SEVERITY_COLOR[s]}` }} />{s}
          </span>
        ))}
      </div>
      <span className="h-4 w-px bg-line" />
      <div className="flex items-center gap-3">
        <span className="eyebrow">Units</span>
        {UNITS.map(([k, l]) => (
          <span key={k} className="flex items-center gap-1.5 text-[11px] text-bone-dim">
            <span className="h-2 w-3 rounded-[3px] border" style={{ borderColor: UNIT_STATUS_COLOR[k] }} />{l}
          </span>
        ))}
      </div>
    </div>
  );
}
