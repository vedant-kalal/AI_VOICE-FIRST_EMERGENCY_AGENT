import { useEffect, useRef, useState } from "react";
import { Play, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { SCENARIOS, categoryLabel } from "@/lib/taxonomy";
import { gsap, prefersReducedMotion, stagger } from "@/lib/motion";

/**
 * Launcher for the backend's scenario replayer (POST /api/dev/demo/{a-e}). It drives the REAL tool executor,
 * so everything on screen afterwards is genuine backend output, just with a scripted caller.
 */
export function DrillDeck({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pace, setPace] = useState(1.2);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const d = dialog.current!;
    if (open && !d.open) {
      d.showModal();
      setError(null);
      if (!prefersReducedMotion()) {
        gsap.fromTo(d, { opacity: 0, y: 16, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.35, ease: "expo.out" });
        gsap.fromTo(d.querySelectorAll("[data-card]"), { opacity: 0, y: 14 },
          { opacity: 1, y: 0, duration: 0.45, ease: "expo.out", delay: 0.05, stagger: (i: number) => stagger(i) });
      }
    } else if (!open && d.open) {
      if (prefersReducedMotion()) d.close();
      else gsap.to(d, { opacity: 0, y: 8, duration: 0.2, ease: "power3.in", onComplete: () => d.close() });
    }
  }, [open]);

  const run = async (key: string) => {
    setBusy(key);
    setError(null);
    try {
      await api.runDemo(key, pace);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404
        ? "Drills are off on the backend. Set ENABLE_DEV_ENDPOINTS=true in .env and restart it."
        : e instanceof Error ? e.message : "Could not start the drill");
    } finally {
      setBusy(null);
    }
  };

  return (
    <dialog ref={dialog} onCancel={(e) => { e.preventDefault(); onClose(); }}
            onClick={(e) => { if (e.target === dialog.current) onClose(); }}
            className="panel m-auto max-h-[calc(100vh-32px)] w-[min(1080px,calc(100vw-32px))] overflow-y-auto p-0 text-bone backdrop:bg-ink-0/70 backdrop:backdrop-blur-sm">
      <div className="flex items-start justify-between gap-6 px-8 pt-8">
        <div>
          <div className="eyebrow">Drill mode · scripted caller, real agent tools</div>
          <h2 className="mt-2 text-[42px] leading-[0.95] font-[680] tracking-[-0.04em]">
            Put a call <span className="font-serif font-normal text-flare italic">on the line.</span>
          </h2>
          <p className="mt-3 max-w-[62ch] text-[14px] leading-relaxed text-bone-dim">
            Each drill replays a scenario from the problem statement through the same tool executor the live voice agent
            uses: geocoding, duplicate merge, rule-based triage, ranked dispatch, escalation. Watch the map, the line and the wire.
          </p>
        </div>
        <button className="btn btn-ghost size-10 justify-center p-0" onClick={onClose} aria-label="Close"><X size={18} /></button>
      </div>

      <div className="grid grid-cols-1 gap-3 px-8 pt-7 sm:grid-cols-2 lg:grid-cols-5">
        {SCENARIOS.map((s, i) => (
          <button key={s.key} data-card onClick={() => run(s.key)} disabled={!!busy}
                  className="group relative flex min-h-[236px] flex-col overflow-hidden rounded-[18px] border border-line bg-ink-1/60 p-4 text-left transition-[border-color,background,translate] duration-200 hover:-translate-y-0.5 hover:border-flare/50 hover:bg-ink-3 disabled:opacity-50">
            <span className="flex items-center justify-between font-mono text-[10px] tracking-[0.12em] text-bone-faint uppercase">
              <span>Drill {s.key.toUpperCase()}</span>
              <span>{String(i + 1).padStart(2, "0")}</span>
            </span>
            <span className="mt-4 text-[21px] leading-[1.05] font-[650] tracking-[-0.02em]">{s.title}</span>
            <span className="mt-1 font-serif text-[15px] text-bone-dim italic">{s.place}</span>
            <ul className="mt-4 flex flex-1 flex-col gap-1.5">
              {s.beats.map((b) => (
                <li key={b} className="flex gap-2 text-[12px] leading-snug text-bone-dim">
                  <span className="mt-[7px] h-px w-2.5 shrink-0 bg-bone-faint" />{b}
                </li>
              ))}
            </ul>
            <span className="mt-4 flex items-center justify-between">
              <span className="min-w-0 truncate pr-2 font-mono text-[10px] text-bone-faint uppercase">{categoryLabel(s.cat)}</span>
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-ink-4 text-bone transition-colors duration-150 group-hover:bg-flare group-hover:text-ink-1">
                {busy === s.key
                  ? <span className="size-3 rounded-full border-2 border-current border-t-transparent" style={{ animation: "spin .7s linear infinite" }} />
                  : <Play size={13} fill="currentColor" />}
              </span>
            </span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-5 px-8 pt-6 pb-8">
        <label className="flex items-center gap-3 text-[13px] text-bone-dim">
          <span className="eyebrow">Pace</span>
          <input type="range" min={0.3} max={3} step={0.1} value={pace} onChange={(e) => setPace(Number(e.target.value))}
                 className="w-40 accent-[var(--color-flare)]" />
          <span className="w-12 font-mono text-bone tabular">{pace.toFixed(1)}s</span>
        </label>
        <span className="text-[12px] text-bone-faint">Seconds between agent steps; transcript lines take 1.4× longer.</span>
        {error && <p role="alert" className="w-full rounded-[12px] border border-flare/40 bg-flare/10 px-4 py-3 text-[13px] text-bone">{error}</p>}
      </div>
    </dialog>
  );
}
