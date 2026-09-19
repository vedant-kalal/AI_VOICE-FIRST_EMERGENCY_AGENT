import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  flare: "var(--color-flare)", ember: "var(--color-ember)", sodium: "var(--color-sodium)",
  sage: "var(--color-sage)", ice: "var(--color-ice)", bone: "var(--color-bone-dim)",
};

export function Pill({ tone = "bone", children, dot, blink }: { tone?: keyof typeof TONES | string; children: ReactNode; dot?: boolean; blink?: boolean }) {
  const c = TONES[tone] ?? tone;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.12em] whitespace-nowrap"
          style={{ color: c, background: `color-mix(in srgb, ${c} 10%, transparent)`, border: `1px solid color-mix(in srgb, ${c} 28%, transparent)` }}>
      {dot && <span className={`size-1.5 rounded-full ${blink ? "animate-blink" : ""}`} style={{ background: c }} />}
      {children}
    </span>
  );
}
