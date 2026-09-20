import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Search, ShieldCheck, X } from "lucide-react";
import { useLive } from "@/store/live";
import { useDepartmentCounts, useLens } from "./scope";
import { DEPT_ACCENT, DeptGlyph } from "./deptVisuals";
import { gsap, prefersReducedMotion, stagger } from "@/lib/motion";

/**
 * The access picker: super admin (everything) or one department. A department view hides every other
 * department's incidents, units, calls and wire entries — a view filter for operators, not a security boundary.
 */
export function DepartmentDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const lens = useLens();
  const setLens = useLive((s) => s.setLens);
  const departments = useLive((s) => s.taxonomy.departments);
  const counts = useDepartmentCounts();
  const [q, setQ] = useState("");

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const rows = departments.filter((d) => !needle || d.label.toLowerCase().includes(needle) || d.key.includes(needle));
    return rows.sort((a, b) => (counts[b.key]?.active ?? 0) - (counts[a.key]?.active ?? 0) || a.label.localeCompare(b.label));
  }, [departments, counts, q]);

  useEffect(() => {
    const d = dialog.current!;
    if (open && !d.open) {
      d.showModal();
      setQ("");
      if (!prefersReducedMotion()) {
        gsap.fromTo(d, { x: 40, opacity: 0 }, { x: 0, opacity: 1, duration: 0.35, ease: "expo.out" });
        gsap.fromTo(d.querySelectorAll("[data-row]"), { opacity: 0, x: 18 },
          { opacity: 1, x: 0, duration: 0.4, ease: "expo.out", delay: 0.05, stagger: (i: number) => stagger(i) });
      }
    } else if (!open && d.open) {
      if (prefersReducedMotion()) d.close();
      else gsap.to(d, { x: 24, opacity: 0, duration: 0.2, ease: "power3.in", onComplete: () => d.close() });
    }
  }, [open]);

  const choose = (dept: string | null) => {
    setLens(dept ? { mode: "department", dept } : { mode: "command", dept: null });
    onClose();
  };

  return (
    <dialog ref={dialog} onCancel={(e) => { e.preventDefault(); onClose(); }}
            onClick={(e) => { if (e.target === dialog.current) onClose(); }}
            aria-label="Choose access view"
            className="panel mt-0 mr-4 mb-0 ml-auto h-[calc(100vh-32px)] w-[min(460px,calc(100vw-32px))] translate-y-4 overflow-hidden p-0 text-bone backdrop:bg-ink-0/70 backdrop:backdrop-blur-sm">
      <div className="flex h-full flex-col">
        <header className="flex items-start justify-between gap-4 px-6 pt-6 pb-4">
          <div>
            <div className="eyebrow">Access view</div>
            <h2 className="mt-2 text-[28px] leading-[1] font-[680] tracking-[-0.03em]">
              Who is <span className="font-serif font-normal text-flare italic">watching?</span>
            </h2>
            <p className="mt-2 max-w-[42ch] text-[12.5px] leading-relaxed text-bone-dim">
              A department view shows only the incidents it owns or has been escalated into, with its own units,
              calls and wire.
            </p>
          </div>
          <button className="btn btn-ghost -mr-1 size-9 justify-center p-0" onClick={onClose} aria-label="Close"><X size={17} /></button>
        </header>

        <div className="px-6 pb-3">
          <button data-row onClick={() => choose(null)}
                  className={`group flex w-full items-center gap-3 rounded-[16px] border p-4 text-left transition-[border-color,background] duration-150
                    ${lens.mode === "command" ? "border-flare/50 bg-flare/10" : "border-line bg-ink-1/50 hover:border-line-strong hover:bg-ink-3"}`}>
            <span className="grid size-10 shrink-0 place-items-center rounded-[11px] border border-line-strong text-flare">
              <ShieldCheck size={18} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[16px] font-[650] tracking-[-0.01em]">Super admin</span>
              <span className="block text-[12px] text-bone-dim">City-wide command · every department</span>
            </span>
            {lens.mode === "command" && <Check size={16} className="text-flare" />}
          </button>
        </div>

        <div className="flex items-center gap-2 px-6 pb-3">
          <span className="eyebrow">Departments</span>
          <span className="h-px flex-1 bg-line" />
          <label className="flex h-8 items-center gap-2 rounded-full border border-line bg-ink-1/60 px-3">
            <Search size={13} className="text-bone-faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter"
                   aria-label="Filter departments"
                   className="w-24 bg-transparent text-[12px] text-bone placeholder:text-bone-faint focus:w-32 focus:outline-none" />
          </label>
        </div>

        <ul className="scroll-quiet fade-mask-b min-h-0 flex-1 overflow-y-auto px-6 pb-8">
          {list.map((d) => {
            const c = counts[d.key] ?? { active: 0, critical: 0 };
            const on = lens.dept === d.key && lens.mode === "department";
            const accent = DEPT_ACCENT[d.key] ?? "var(--color-ice)";
            return (
              <li key={d.key} data-row className="mb-2">
                <button onClick={() => choose(d.key)}
                        className={`group flex w-full items-center gap-3 rounded-[14px] border p-3 text-left transition-[border-color,background] duration-150
                          ${on ? "bg-ink-3" : "border-line bg-ink-1/40 hover:border-line-strong hover:bg-ink-3"}`}
                        style={on ? { borderColor: `color-mix(in srgb, ${accent} 55%, transparent)` } : undefined}>
                  <span className="grid size-9 shrink-0 place-items-center rounded-[10px] border"
                        style={{ color: accent, borderColor: `color-mix(in srgb, ${accent} 35%, transparent)`,
                                 background: `color-mix(in srgb, ${accent} 8%, transparent)` }}>
                    <DeptGlyph dept={d.key} size={16} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[14.5px] font-[600] tracking-[-0.01em]">{d.label}</span>
                    <span className="block font-mono text-[10.5px] text-bone-faint">{d.key}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2 text-right">
                    {c.critical > 0 && (
                      <span className="rounded-full bg-flare/15 px-2 py-0.5 font-mono text-[10px] text-flare">{c.critical} crit</span>
                    )}
                    <span className="font-mono text-[15px] tabular" style={{ color: c.active ? "var(--color-bone)" : "var(--color-bone-faint)" }}>
                      {String(c.active).padStart(2, "0")}
                    </span>
                    {on && <Check size={15} style={{ color: accent }} />}
                  </span>
                </button>
              </li>
            );
          })}
          {list.length === 0 && <li className="px-1 py-6 text-[13px] text-bone-faint">No department matches “{q}”.</li>}
        </ul>
      </div>
    </dialog>
  );
}
