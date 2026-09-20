import { useEffect, useRef } from "react";
import { PhoneIncoming, TriangleAlert } from "lucide-react";
import { gsap, prefersReducedMotion } from "@/lib/motion";
import { CUE_KEY } from "./useCueHotkey";

type Status = ReturnType<typeof import("./useCueHotkey").useCueHotkey>["status"];

/** Confirms the cue landed, then gets out of the way. */
export function CueBanner({ status }: { status: Status }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || prefersReducedMotion()) return;
    gsap.fromTo(el.current, { y: -12, opacity: 0 }, { y: 0, opacity: 1, duration: 0.3, ease: "expo.out" });
  }, [status.state]);

  if (status.state === "idle") return null;
  const error = status.state === "error";
  const tone = error ? "var(--color-flare)" : "var(--color-sage)";

  return (
    <div ref={el} role="status" aria-live="polite"
         className="pointer-events-none fixed top-[104px] left-1/2 z-50 -translate-x-1/2">
      <div className="flex items-center gap-2.5 rounded-full border px-4 py-2 backdrop-blur-md"
           style={{ borderColor: `color-mix(in srgb, ${tone} 45%, transparent)`,
                    background: `color-mix(in srgb, ${tone} 12%, rgb(11 12 14 / 0.85))`, color: tone }}>
        {error ? <TriangleAlert size={15} /> : <PhoneIncoming size={15} />}
        <span className="text-[13px] font-[600]">
          {status.state === "starting" ? "Opening the line…"
            : status.state === "running" ? "Call on the line"
            : status.message}
        </span>
        {!error && <kbd className="rounded border border-current/40 px-1.5 font-mono text-[10px] uppercase">{CUE_KEY}</kbd>}
      </div>
    </div>
  );
}
