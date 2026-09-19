import { useEffect, useRef } from "react";
import { gsap, prefersReducedMotion } from "@/lib/motion";
import { levelOf, SEVERITY_COLOR } from "@/lib/taxonomy";

/**
 * Severity as a 270° gauge with the critical threshold marked. `size` scales everything;
 * the arc animates on change (it answers "did severity just move?").
 */
export function SeverityRing({ score, size = 44, threshold = 85, stroke, showValue = true }:
  { score: number; size?: number; threshold?: number; stroke?: number; showValue?: boolean }) {
  const arc = useRef<SVGCircleElement>(null);
  const num = useRef<SVGTextElement>(null);
  const prev = useRef(0);
  const sw = stroke ?? Math.max(3, size / 12);
  const r = (size - sw) / 2 - 1;
  const C = 2 * Math.PI * r;
  const span = 0.75 * C;
  const color = score > 0 ? SEVERITY_COLOR[levelOf(score)] : SEVERITY_COLOR.none;
  const tick = (threshold / 100) * 270 + 135; // degrees, gauge starts at 135°

  useEffect(() => {
    const target = { v: prev.current };
    const draw = () => {
      arc.current?.setAttribute("stroke-dasharray", `${(target.v / 100) * span} ${C}`);
      if (num.current) num.current.textContent = String(Math.round(target.v));
    };
    if (prefersReducedMotion()) { target.v = score; draw(); prev.current = score; return; }
    const t = gsap.to(target, { v: score, duration: 0.9, ease: "expo.out", onUpdate: draw });
    prev.current = score;
    return () => { t.kill(); };
  }, [score, span, C]);

  const c = size / 2;
  const rad = (tick * Math.PI) / 180;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Severity ${score} of 100`}>
      <g transform={`rotate(135 ${c} ${c})`}>
        <circle cx={c} cy={c} r={r} fill="none" stroke="var(--color-ink-4)" strokeWidth={sw}
                strokeDasharray={`${span} ${C}`} strokeLinecap="round" />
        <circle ref={arc} cx={c} cy={c} r={r} fill="none" stroke={color} strokeWidth={sw}
                strokeDasharray={`0 ${C}`} strokeLinecap="round"
                style={{ filter: score >= threshold ? `drop-shadow(0 0 ${size / 10}px ${color})` : undefined, transition: "stroke 250ms" }} />
      </g>
      <line x1={c + (r - sw) * Math.cos(rad)} y1={c + (r - sw) * Math.sin(rad)}
            x2={c + (r + sw) * Math.cos(rad)} y2={c + (r + sw) * Math.sin(rad)}
            stroke="var(--color-bone-dim)" strokeWidth={1} opacity={0.7} />
      {showValue && (
        <text ref={num} x={c} y={c + size * 0.1} textAnchor="middle" fill="var(--color-bone)"
              style={{ font: `600 ${size * 0.3}px var(--font-display)`, fontVariantNumeric: "tabular-nums" }}>0</text>
      )}
    </svg>
  );
}
