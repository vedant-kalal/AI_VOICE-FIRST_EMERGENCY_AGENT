import { useEffect, useRef } from "react";
import { prefersReducedMotion } from "@/lib/motion";

/**
 * Speech-activity trace. The dashboard gets transcript events, not audio, so this shows *who is speaking
 * and for roughly how long* — a burst sized to the utterance, coloured by speaker — not a fake spectrum.
 */
export function Waveform({ live, burst }: { live: boolean; burst: { role: "caller" | "agent"; chars: number; key: number } | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const state = useRef({ energy: 0, until: 0, role: "caller" as "caller" | "agent", t: 0 });

  useEffect(() => {
    if (!burst) return;
    const s = state.current;
    s.role = burst.role;
    s.until = performance.now() + Math.min(5200, 500 + burst.chars * 45);
  }, [burst?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const c = canvas.current!;
    const ctx = c.getContext("2d")!;
    const reduce = prefersReducedMotion();
    let raf = 0;
    const css = getComputedStyle(document.documentElement);
    const col = {
      caller: css.getPropertyValue("--color-bone").trim() || "#ece6d9",
      agent: css.getPropertyValue("--color-ice").trim() || "#8ec5ff",
      idle: css.getPropertyValue("--color-ink-4").trim() || "#24282d",
    };

    const resize = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      c.width = c.clientWidth * dpr;
      c.height = c.clientHeight * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(c);

    const draw = (now: number) => {
      const s = state.current;
      const w = c.clientWidth, h = c.clientHeight;
      const speaking = live && now < s.until;
      const target = speaking ? 1 : live ? 0.12 : 0;
      s.energy += (target - s.energy) * 0.08;
      s.t += 0.016;
      ctx.clearRect(0, 0, w, h);
      const bars = Math.floor(w / 5);
      const mid = h / 2;
      ctx.fillStyle = s.energy > 0.2 ? col[s.role] : col.idle;
      for (let i = 0; i < bars; i++) {
        const x = i * 5;
        const env = Math.sin((i / bars) * Math.PI);  // taper at both ends
        const n = Math.sin(i * 0.55 + s.t * 7) * 0.5 + Math.sin(i * 1.7 - s.t * 11) * 0.3 + Math.sin(i * 0.13 + s.t * 3) * 0.2;
        const amp = Math.max(1, Math.abs(n) * env * s.energy * (h * 0.46));
        ctx.globalAlpha = 0.35 + 0.65 * env;
        ctx.fillRect(x, mid - amp, 2.2, amp * 2);
      }
      ctx.globalAlpha = 1;
      if (!reduce && !document.hidden && (live || s.energy > 0.01)) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    const onVis = () => { if (!document.hidden && live && !reduce) { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw); } };
    document.addEventListener("visibilitychange", onVis);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); document.removeEventListener("visibilitychange", onVis); };
  }, [live]);

  return <canvas ref={canvas} className="h-[64px] w-full" aria-hidden />;
}
