import { useEffect, useRef } from "react";
import { gsap, prefersReducedMotion } from "@/lib/motion";

/** Counts to a new value instead of snapping — the number is the content, so it renders immediately. */
export function AnimatedNumber({ value, className, pad = 0 }: { value: number; className?: string; pad?: number }) {
  const el = useRef<HTMLSpanElement>(null);
  const cur = useRef({ v: value });
  useEffect(() => {
    const fmt = (n: number) => String(Math.round(n)).padStart(pad, "0");
    if (prefersReducedMotion()) {
      cur.current.v = value;
      if (el.current) el.current.textContent = fmt(value);
      return;
    }
    const t = gsap.to(cur.current, {
      v: value, duration: 0.6, ease: "power3.out",
      onUpdate: () => { if (el.current) el.current.textContent = fmt(cur.current.v); },
    });
    return () => { t.kill(); };
  }, [value, pad]);
  return <span ref={el} className={`tabular ${className ?? ""}`}>{String(value).padStart(pad, "0")}</span>;
}
