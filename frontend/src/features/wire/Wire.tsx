import { useLayoutEffect, useRef } from "react";
import { useLive, type Tone } from "@/store/live";
import { clockIST } from "@/lib/format";
import { gsap, prefersReducedMotion } from "@/lib/motion";

const TONE: Record<Tone, string> = {
  flare: "var(--color-flare)", ember: "var(--color-ember)", sodium: "var(--color-sodium)",
  sage: "var(--color-sage)", ice: "var(--color-ice)", bone: "var(--color-bone-dim)",
};

/** The wire: every backend event, newest first, as one horizontal strip. */
export function Wire() {
  const wire = useLive((s) => s.wire);
  const select = useLive((s) => s.select);
  const track = useRef<HTMLOListElement>(null);
  const lastTop = useRef<number | null>(null);

  useLayoutEffect(() => {
    const top = wire[0]?.id ?? null;
    const prev = lastTop.current;
    lastTop.current = top;
    if (prev === null || top === prev || !track.current || prefersReducedMotion()) return;
    const added = wire.findIndex((w) => w.id === prev);
    const n = added < 0 ? 1 : added;
    const kids = Array.from(track.current.children);
    gsap.fromTo(kids.slice(0, n), { opacity: 0, x: -24 }, { opacity: 1, x: 0, duration: 0.5, ease: "expo.out", stagger: 0.05 });
    gsap.fromTo(kids.slice(n, n + 8), { x: -20 }, { x: 0, duration: 0.5, ease: "expo.out" });
  }, [wire]);

  return (
    <section className="panel flex h-full items-stretch overflow-hidden" aria-label="Event wire">
      <div className="flex w-[92px] shrink-0 flex-col justify-center gap-1 border-r border-line pl-5">
        <span className="eyebrow">Wire</span>
        <span className="font-mono text-[18px] text-bone tabular">{String(wire.length).padStart(2, "0")}</span>
      </div>
      {wire.length === 0 ? (
        <p className="flex items-center px-5 font-serif text-[18px] text-bone-faint italic">Every event from the backend lands here as it happens.</p>
      ) : (
        <ol ref={track} className="scroll-quiet fade-mask-x flex min-w-0 flex-1 items-center gap-2 overflow-x-auto px-4">
          {wire.map((w) => (
            <li key={w.id} className="shrink-0">
              <button onClick={() => w.incidentId && select(w.incidentId)} disabled={!w.incidentId}
                      className="flex h-[58px] max-w-[260px] min-w-[170px] flex-col justify-center gap-1 rounded-[13px] border border-line bg-ink-1/50 px-3 text-left transition-colors duration-150 enabled:hover:border-line-strong enabled:hover:bg-ink-3 disabled:cursor-default">
                <span className="flex items-center gap-2 font-mono text-[9.5px] text-bone-faint tabular">
                  <span className="size-1.5 rounded-full" style={{ background: TONE[w.tone] }} />
                  {clockIST(w.ts)}
                </span>
                <span className="truncate text-[13px] leading-none font-[600]" style={{ color: w.tone === "bone" ? "var(--color-bone)" : TONE[w.tone] }}>{w.title}</span>
                {w.detail && <span className="truncate text-[11px] leading-none text-bone-dim">{w.detail}</span>}
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
