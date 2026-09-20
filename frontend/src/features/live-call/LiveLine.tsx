import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, PhoneForwarded, PhoneOff } from "lucide-react";
import { useLive, type Line, type LiveCall, type ToolHit } from "@/store/live";
import { Waveform } from "./Waveform";
import { summarizeTool } from "./summarize";
import { Pill } from "@/components/Pill";
import { PIPELINE, PIPELINE_INDEX, deptLabel } from "@/lib/taxonomy";
import { maskPhone } from "@/lib/format";
import { gsap, prefersReducedMotion } from "@/lib/motion";
import { useDepartmentLabel, useLensDept, useScopedIncidents } from "@/features/lens/scope";

function useElapsed(from: string | undefined, to: string | null | undefined) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (to) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [to]);
  if (!from) return "00:00";
  const s = Math.max(0, Math.round(((to ? Date.parse(to) : now) - Date.parse(from)) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** A caption line. The newest live line is typed out; history renders whole. */
function Caption({ line, animate }: { line: Line; animate: boolean }) {
  const el = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!animate || !el.current || prefersReducedMotion()) return;
    const full = line.content;
    const o = { n: 0 };
    const t = gsap.to(o, {
      n: full.length, duration: Math.min(2.4, 0.25 + full.length * 0.022), ease: "none",
      onUpdate: () => { if (el.current) el.current.textContent = full.slice(0, Math.round(o.n)); },
    });
    gsap.fromTo(el.current.parentElement, { y: 8, opacity: 0 }, { y: 0, opacity: 1, duration: 0.35, ease: "expo.out" });
    return () => { t.kill(); if (el.current) el.current.textContent = full; };
  }, [animate, line.content]);

  const caller = line.role === "caller";
  return (
    <li className={`flex flex-col gap-1 ${caller ? "" : "pl-6"}`}>
      <span className="eyebrow" style={{ color: caller ? undefined : "var(--color-ice)" }}>{caller ? "Caller" : "Dhvani agent"}</span>
      <p className={caller
        ? "font-serif text-[21px] leading-[1.22] text-bone italic"
        : "text-[14.5px] leading-[1.5] text-ice/90"}>
        <span className="sr-only">{line.content}</span>
        <span ref={el} aria-hidden>{line.content}</span>
      </p>
    </li>
  );
}

function PipelineNode({ step, hits, last }: { step: (typeof PIPELINE)[number]; hits: ToolHit[]; last: ToolHit | undefined }) {
  const el = useRef<HTMLDivElement>(null);
  const latest = hits[hits.length - 1];
  const isLast = !!last && latest?.id === last.id;
  const tone = !latest ? null : latest.status === "error" ? "var(--color-flare)"
    : latest.status === "rejected" ? "var(--color-sodium)" : "var(--color-ice)";

  useEffect(() => {
    if (!isLast || !last?.fresh || !el.current || prefersReducedMotion()) return;
    gsap.fromTo(el.current, { scale: 0.92 }, { scale: 1, duration: 0.5, ease: "back.out(3)" });
    gsap.fromTo(el.current.querySelector(".glow"), { opacity: 0.35 }, { opacity: 0, duration: 1.4, ease: "power2.out" });
  }, [isLast, last?.id, last?.fresh]);

  return (
    <div ref={el} title={`${step.tool} — ${step.hint}`}
         className="relative flex flex-col gap-1 overflow-hidden rounded-[11px] border px-2.5 py-2"
         style={{ borderColor: tone ? `color-mix(in srgb, ${tone} 35%, transparent)` : "var(--color-line)",
                  background: tone ? `color-mix(in srgb, ${tone} 7%, transparent)` : "transparent" }}>
      <span className="glow pointer-events-none absolute inset-0 opacity-0" style={{ background: `radial-gradient(circle at 20% 0%, color-mix(in srgb, ${tone ?? "transparent"} 60%, transparent), transparent 75%)` }} />
      <span className="flex items-center justify-between font-mono text-[9.5px] text-bone-faint">
        <span>{String(PIPELINE_INDEX[step.tool] + 1).padStart(2, "0")}</span>
        {hits.length > 0 && <span style={{ color: tone ?? undefined }}>×{hits.length}</span>}
      </span>
      <span className="text-[12.5px] font-[600] leading-none" style={{ color: tone ? "var(--color-bone)" : "var(--color-bone-faint)" }}>{step.verb}</span>
    </div>
  );
}

function CallTabs({ calls, focus }: { calls: LiveCall[]; focus: string | null }) {
  const focusCall = useLive((s) => s.focusCall);
  if (calls.length < 2) return null;
  return (
    <div className="scroll-quiet -mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
      {calls.slice(0, 6).map((c) => (
        <button key={c.sid} onClick={() => focusCall(c.sid)}
                className={`flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 font-mono text-[10.5px] transition-colors duration-150
                  ${c.sid === focus ? "border-line-strong bg-ink-4 text-bone" : "border-line text-bone-faint hover:text-bone-dim"}`}>
          {c.live && <span className="size-1.5 animate-blink rounded-full bg-flare" />}
          {maskPhone(c.from)}
        </button>
      ))}
    </div>
  );
}

export function LiveLine({ onDrill }: { onDrill: () => void }) {
  const calls = useLive((s) => s.calls);
  const focusSid = useLive((s) => s.focusSid);
  const incidents = useLive((s) => s.incidents);
  const select = useLive((s) => s.select);

  const dept = useLensDept();
  const deptName = useDepartmentLabel(dept);
  const { ids } = useScopedIncidents();
  const ordered = useMemo(() => Object.values(calls)
    .filter((c) => !dept || (c.incidentId ? ids.has(c.incidentId) : false))
    .sort((a, b) => Number(b.live) - Number(a.live) || b.startedAt.localeCompare(a.startedAt)), [calls, dept, ids]);
  const focused = focusSid ? calls[focusSid] : undefined;
  const call = (focused && ordered.includes(focused) ? focused : undefined) ?? ordered[0];
  const elapsed = useElapsed(call?.startedAt, call?.endedAt);
  const inc = call?.incidentId ? incidents[call.incidentId] : undefined;

  const lines = call?.lines ?? [];
  const lastLine = lines[lines.length - 1];
  const tools = call?.tools ?? [];
  const lastTool = tools[tools.length - 1];
  const byTool = useMemo(() => {
    const m: Record<string, ToolHit[]> = {};
    for (const t of tools) (m[t.tool] ??= []).push(t);
    return m;
  }, [tools]);

  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [lines.length, call?.sid]);

  const burst = lastLine && lastLine.fresh ? { role: lastLine.role, chars: lastLine.content.length, key: lastLine.id } : null;

  return (
    <section className="panel flex h-full flex-col overflow-hidden" aria-label="Live call">
      <header className="flex flex-col gap-3 px-5 pt-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="eyebrow">Live line · Twilio ⇄ OpenAI Realtime</div>
            <h2 className="mt-1 flex items-center gap-2.5 text-[22px] font-[650] tracking-[-0.025em]">
              {call ? maskPhone(call.from) : "Standing by"}
              {call?.live && <Pill tone="flare" dot blink>Live</Pill>}
              {call && !call.live && <Pill tone="bone">Ended</Pill>}
            </h2>
          </div>
          <div className="text-right">
            <div className="eyebrow">Elapsed</div>
            <div className="mt-1 font-mono text-[20px] tabular" style={{ color: call?.live ? "var(--color-bone)" : "var(--color-bone-faint)" }}>{elapsed}</div>
          </div>
        </div>
        <CallTabs calls={ordered} focus={call?.sid ?? null} />
        {inc && (
          <button onClick={() => select(inc.id)}
                  className="group flex items-center gap-2 self-start rounded-full border border-line bg-ink-1/50 py-1 pr-2 pl-3 text-[12px] text-bone-dim transition-colors hover:border-line-strong hover:text-bone">
            <span className="font-mono text-bone">#{inc.incident_number}</span>
            <span className="max-w-[220px] truncate">{inc.address_text ?? "locating…"}</span>
            <ArrowUpRight size={13} className="transition-transform duration-150 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
          </button>
        )}
        <div className="rounded-[14px] border border-line bg-ink-0/60 px-3">
          <Waveform live={!!call?.live} burst={burst} />
        </div>
      </header>

      <div ref={scroller} className="scroll-quiet min-h-0 flex-1 overflow-y-auto px-5 py-5" aria-live="polite">
        {!call ? (
          <div className="flex h-full flex-col justify-center gap-3">
            <p className="font-serif text-[30px] leading-[1.05] text-bone italic">
              {dept ? "No call on your desk." : "The line is quiet."}
            </p>
            <p className="max-w-[34ch] text-[13px] leading-relaxed text-bone-dim">
              {dept
                ? `Calls appear here once the agent logs an incident ${deptName} owns or is escalated into.`
                : "A real call arrives through Twilio and is answered by the realtime agent. Every word and every decision it makes streams here."}
            </p>
            <button className="btn mt-1 self-start" onClick={onDrill}>Open a scenario</button>
          </div>
        ) : (
          <ol className="flex flex-col gap-4">
            {lines.map((l) => <Caption key={l.id} line={l} animate={l.fresh && l.id === lastLine?.id} />)}
            {call.handoff && (
              <li className="rounded-[14px] border border-sodium/30 bg-sodium/5 p-3.5">
                <div className="flex items-center gap-2 text-[13px] font-[600] text-sodium">
                  <PhoneForwarded size={14} /> Transferring to {deptLabel(call.handoff.department)}
                  <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.1em]">{call.handoff.status}</span>
                </div>
                <ol className="mt-2.5 flex flex-col gap-1.5">
                  {call.handoff.ladder.slice(0, 5).map((s, k) => (
                    <li key={k} className="flex items-center gap-2 text-[12px] text-bone-dim">
                      <span className="font-mono text-[10px] text-bone-faint">{k + 1}</span>
                      <span className={k === call.handoff!.step ? "text-bone" : ""}>{s.name}</span>
                      <span className="text-bone-faint">· {s.role}</span>
                    </li>
                  ))}
                </ol>
              </li>
            )}
            {!call.live && (
              <li className="flex items-center gap-2 font-mono text-[10.5px] tracking-[0.1em] text-bone-faint uppercase">
                <PhoneOff size={12} /> Line closed
              </li>
            )}
          </ol>
        )}
      </div>

      <footer className="border-t border-line px-5 pt-4 pb-5">
        <div className="mb-3 flex items-baseline justify-between">
          <span className="eyebrow">Agent trace · function calls</span>
          {lastTool && <span className="font-mono text-[10px] text-bone-faint tabular">{lastTool.duration_ms ?? "–"} ms</span>}
        </div>
        <div className="grid grid-cols-4 gap-1.5">
          {PIPELINE.map((p) => <PipelineNode key={p.tool} step={p} hits={byTool[p.tool] ?? []} last={lastTool} />)}
        </div>
        <ul className="mt-3 flex min-h-[64px] flex-col gap-1.5">
          {tools.slice(-3).reverse().map((t, k) => (
            <li key={t.id} className="flex items-start gap-2 text-[12px] leading-snug" style={{ opacity: 1 - k * 0.28 }}>
              <span className="mt-[5px] size-1.5 shrink-0 rounded-full"
                    style={{ background: t.status === "rejected" ? "var(--color-sodium)" : t.status === "error" ? "var(--color-flare)" : "var(--color-ice)" }} />
              <span className="text-bone-dim"><span className="text-bone">{PIPELINE[PIPELINE_INDEX[t.tool]]?.verb ?? t.tool}</span> — {summarizeTool(t)}</span>
            </li>
          ))}
          {tools.length === 0 && <li className="text-[12px] text-bone-faint">No tool calls yet on this line.</li>}
        </ul>
      </footer>
    </section>
  );
}
