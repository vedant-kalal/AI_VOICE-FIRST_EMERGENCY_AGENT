import { useEffect, useState } from "react";
import { Radio, Zap } from "lucide-react";
import { useLive, useStats } from "@/store/live";
import { AnimatedNumber } from "@/components/AnimatedNumber";
import { clockIST } from "@/lib/format";

function Brandmark() {
  // The mark is a spoken waveform that becomes a heartbeat — voice in, help out.
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden>
      <rect width="40" height="40" rx="12" fill="var(--color-ink-3)" stroke="var(--color-line-strong)" />
      <path d="M7 20h5l2.5-7 5 14 3.5-10 2.5 3H33" fill="none" stroke="var(--color-flare)" strokeWidth="2.4"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Stat({ label, value, tone, suffix }: { label: string; value: number; tone?: string; suffix?: string }) {
  return (
    <div className="flex flex-col gap-1 px-5 first:pl-0">
      <span className="eyebrow whitespace-nowrap">{label}</span>
      <span className="flex items-baseline gap-1 text-[26px] leading-none font-[620] tracking-[-0.02em]" style={{ color: tone }}>
        <AnimatedNumber value={value} pad={2} />
        {suffix && <span className="font-mono text-[11px] font-normal text-bone-faint">{suffix}</span>}
      </span>
    </div>
  );
}

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return (
    <div className="hide-sm flex flex-col items-end gap-1">
      <span className="eyebrow">Ahmedabad · IST</span>
      <span className="font-mono text-[15px] tabular text-bone">{clockIST(now)}</span>
    </div>
  );
}

export function CommandBar({ onDrill }: { onDrill: () => void }) {
  const s = useStats();
  const mode = useLive((st) => st.mode);
  const conn = useLive((st) => st.conn);
  const connTone = conn === "open" ? "var(--color-sage)" : conn === "connecting" ? "var(--color-sodium)" : "var(--color-flare)";

  return (
    <header className="panel flex h-[84px] items-center gap-6 px-5">
      <div className="flex items-center gap-3.5 pr-2">
        <Brandmark />
        <div className="leading-none">
          <div className="text-[25px] font-[680] tracking-[-0.035em]">
            Dhvani<span className="text-flare">.</span>
          </div>
          <div className="mt-1.5 font-serif text-[14px] italic text-bone-dim">voice-first emergency command</div>
        </div>
      </div>

      <div className="divider h-10 w-px bg-line" />

      <div className="stats scroll-quiet flex min-w-0 flex-1 items-center overflow-hidden [&>*+*]:border-l [&>*+*]:border-line">
        <Stat label="Active" value={s.active} />
        <Stat label="Critical" value={s.critical} tone={s.critical ? "var(--color-flare)" : undefined} />
        <Stat label="Review" value={s.review} tone={s.review ? "var(--color-sodium)" : undefined} />
        <Stat label="Units ready" value={s.available} suffix={`/${s.total}`} />
        <Stat label="En route" value={s.moving} tone={s.moving ? "var(--color-ember)" : undefined} />
        <Stat label="Live lines" value={s.live} tone={s.live ? "var(--color-flare)" : undefined} />
      </div>

      <div className="ml-auto flex items-center gap-5">
        <div className="hidden flex-col items-end gap-1 xl:flex" title="DISPATCH_MODE on the backend">
          <span className="eyebrow">Dispatch</span>
          <span className="flex items-center gap-1.5 font-mono text-[12px] uppercase tracking-[0.1em]"
                style={{ color: mode === "approval" ? "var(--color-sodium)" : "var(--color-ice)" }}>
            <Zap size={12} strokeWidth={2.4} />
            {mode === "approval" ? `Human approval${s.pending ? ` · ${s.pending}` : ""}` : "Autonomous"}
          </span>
        </div>
        <div className="hide-sm flex flex-col items-end gap-1" aria-live="polite">
          <span className="eyebrow">Link</span>
          <span className="flex items-center gap-1.5 font-mono text-[12px] uppercase tracking-[0.1em]" style={{ color: connTone }}>
            <span className={`size-1.5 rounded-full ${conn !== "open" ? "animate-blink" : ""}`} style={{ background: connTone }} />
            {conn === "open" ? "Live" : conn === "connecting" ? "Linking" : "Offline"}
          </span>
        </div>
        <Clock />
        <button className="btn btn-flare h-11 shrink-0 px-5 whitespace-nowrap" onClick={onDrill}>
          <Radio size={15} strokeWidth={2.4} />
          Run a drill
        </button>
      </div>
    </header>
  );
}
