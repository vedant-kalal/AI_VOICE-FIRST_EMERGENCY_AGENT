import { useEffect, useState } from "react";
import { ChevronDown, LayoutGrid, PhoneCall, Radio, ShieldCheck, Zap } from "lucide-react";
import { useLive } from "@/store/live";
import { AnimatedNumber } from "@/components/AnimatedNumber";
import { clockIST } from "@/lib/format";
import { useDepartmentLabel, useLens, useScopedStats } from "@/features/lens/scope";
import { DeptGlyph, deptAccent } from "@/features/lens/deptVisuals";
import type { ConsoleView } from "@/lib/view";

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

function LensButton({ onOpen }: { onOpen: () => void }) {
  const lens = useLens();
  const label = useDepartmentLabel(lens.dept);
  const dept = lens.mode === "department" ? lens.dept : null;
  const accent = dept ? deptAccent(dept) : "var(--color-bone-dim)";
  return (
    <button onClick={onOpen} title="Switch access view"
            className="group flex h-11 items-center gap-2.5 rounded-full border px-3 transition-[border-color,background] duration-150 hover:bg-ink-3"
            style={{ borderColor: dept ? `color-mix(in srgb, ${accent} 45%, transparent)` : "var(--color-line-strong)",
                     background: dept ? `color-mix(in srgb, ${accent} 10%, transparent)` : "transparent" }}>
      <span className="grid size-6 place-items-center rounded-full" style={{ color: accent }}>
        {dept ? <DeptGlyph dept={dept} size={15} /> : <ShieldCheck size={15} />}
      </span>
      <span className="flex flex-col items-start leading-none">
        <span className="eyebrow">Viewing as</span>
        <span className="mt-1 max-w-[168px] truncate text-[13px] font-[600]" style={{ color: dept ? accent : "var(--color-bone)" }}>
          {dept ? label : "Super admin"}
        </span>
      </span>
      <ChevronDown size={14} className="text-bone-faint transition-transform duration-150 group-hover:translate-y-0.5" />
    </button>
  );
}

function ViewSwitch({ view, onView }: { view: ConsoleView; onView: (v: ConsoleView) => void }) {
  const items: { k: ConsoleView; label: string; Icon: typeof LayoutGrid }[] = [
    { k: "board", label: "Live board", Icon: LayoutGrid },
    { k: "calls", label: "Call log", Icon: PhoneCall },
  ];
  return (
    <div role="tablist" aria-label="Console view" className="flex rounded-full border border-line bg-ink-1/60 p-1">
      {items.map(({ k, label, Icon }) => (
        <button key={k} role="tab" aria-selected={view === k} onClick={() => onView(k)}
                className={`flex h-8 items-center gap-1.5 rounded-full px-3 text-[12.5px] font-[550] transition-colors duration-150
                  ${view === k ? "bg-ink-4 text-bone" : "text-bone-faint hover:text-bone-dim"}`}>
          <Icon size={14} />{label}
        </button>
      ))}
    </div>
  );
}

export function CommandBar({ onDrill, onLens, view, onView }:
  { onDrill: () => void; onLens: () => void; view: ConsoleView; onView: (v: ConsoleView) => void }) {
  const s = useScopedStats();
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

      <div className="flex shrink-0 items-center gap-2.5">
        <LensButton onOpen={onLens} />
        <ViewSwitch view={view} onView={onView} />
      </div>

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
          Scenarios
        </button>
      </div>
    </header>
  );
}
