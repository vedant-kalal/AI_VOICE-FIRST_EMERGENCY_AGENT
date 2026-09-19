import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Check, ChevronRight, CircleCheck, Layers, MapPin, RotateCw, ShieldCheck, X } from "lucide-react";
import { useLive } from "@/store/live";
import { api } from "@/lib/api";
import type { IncidentDetail } from "@/lib/types";
import { SeverityRing } from "@/components/SeverityRing";
import { Pill } from "@/components/Pill";
import { CATEGORY, PIPELINE, PIPELINE_INDEX, RESOURCE, STATUS_LABEL, categoryLabel, deptLabel, levelOf, SEVERITY_COLOR } from "@/lib/taxonomy";
import { ago, clockIST, humanize, km, mins } from "@/lib/format";
import { gsap, prefersReducedMotion } from "@/lib/motion";
import { summarizeTool } from "@/features/live-call/summarize";

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section data-reveal className="border-t border-line px-7 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="eyebrow">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

const ASG_TONE: Record<string, string> = {
  pending_approval: "sodium", dispatched: "ice", en_route: "ember", arrived: "sage", completed: "bone", rejected: "bone",
};

function ScoreBars({ b }: { b: Record<string, number | string> }) {
  const nums = Object.entries(b).filter(([, v]) => typeof v === "number") as [string, number][];
  if (!nums.length) return null;
  const max = Math.max(1, ...nums.map(([, v]) => Math.abs(v)));
  return (
    <div className="mt-3 grid grid-cols-[92px_1fr_36px] items-center gap-x-2.5 gap-y-1.5">
      {nums.map(([k, v]) => (
        <div key={k} className="contents">
          <span className="truncate font-mono text-[10px] text-bone-faint uppercase">{humanize(k)}</span>
          <span className="relative h-[5px] overflow-hidden rounded-full bg-ink-4">
            <span data-bar className="absolute inset-y-0 left-0 origin-left rounded-full"
                  style={{ width: `${(Math.abs(v) / max) * 100}%`, background: v < 0 ? "var(--color-flare)" : "var(--color-ice)" }} />
          </span>
          <span className="text-right font-mono text-[10.5px] text-bone-dim tabular">{Number.isInteger(v) ? v : v.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

export function Dossier() {
  const selectedId = useLive((s) => s.selectedId);
  const select = useLive((s) => s.select);
  const live = useLive((s) => (s.selectedId ? s.incidents[s.selectedId] : undefined));
  const threshold = useLive((s) => s.criticalSeverity);
  const flashAt = useLive((s) => (s.selectedId ? s.flashAt[s.selectedId] : undefined));
  const assignmentsVersion = useLive((s) => s.assignments);
  const upsert = useLive((s) => s.upsertIncident);

  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const panel = useRef<HTMLElement>(null);
  const openFor = useRef<string | null>(null);

  const load = useCallback(async (id: string) => {
    try {
      const d = await api.incident(id);
      if (useLive.getState().selectedId === id) { setDetail(d); setError(null); }
    } catch (e) {
      if (useLive.getState().selectedId === id) setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);

  // open / switch
  useEffect(() => {
    if (!selectedId) { openFor.current = null; return; }
    setDetail(null);
    setError(null);
    load(selectedId);
  }, [selectedId, load]);

  // live refresh: anything that touched this incident (debounced so a burst of events is one fetch)
  useEffect(() => {
    if (!selectedId) return;
    const t = window.setTimeout(() => load(selectedId), 450);
    return () => window.clearTimeout(t);
  }, [selectedId, live, flashAt, assignmentsVersion, load]);

  // entrance choreography — once per opened incident, after its data first lands
  useEffect(() => {
    if (!detail || !panel.current || openFor.current === detail.incident.id) return;
    openFor.current = detail.incident.id;
    if (prefersReducedMotion()) return;
    // Not killed on re-render: a live refresh lands ~450 ms after open, and killing mid-flight would strand
    // the panel half-transparent. The timeline is short and only touches nodes that outlive it.
    gsap.timeline()
      .fromTo(panel.current, { x: 36, opacity: 0 }, { x: 0, opacity: 1, duration: 0.45, ease: "expo.out" })
      .fromTo(panel.current.querySelectorAll("[data-reveal]"), { y: 14, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.5, ease: "expo.out", stagger: { each: 0.04, amount: 0.24 } }, 0.08)
      .fromTo(panel.current.querySelectorAll("[data-bar]"), { scaleX: 0 }, { scaleX: 1, duration: 0.8, ease: "expo.out", stagger: 0.02 }, 0.2);
  }, [detail]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && select(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [select]);

  if (!selectedId) return null;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "incident_number" in r) upsert(r as never);
      await load(selectedId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  };

  const d = detail;
  const inc = d ? { ...d.incident, ...(live ?? {}) } : live;
  const active = inc && ["open", "dispatched", "escalated"].includes(inc.status);
  const explanation = (inc?.severity_explanation ?? "").split(";").map((x) => x.trim()).filter(Boolean);
  const signals = Object.entries(d?.severity_signals ?? {}).filter(([, v]) => v !== false && v != null);
  const transcript = d?.calls.flatMap((c) => c.transcript.map((t) => ({ ...t, sid: c.call_sid }))) ?? [];

  return (
    <aside ref={panel} aria-label="Incident dossier"
           className="panel flex h-full flex-col overflow-hidden shadow-[0_40px_120px_-20px_rgb(0_0_0/0.9)]">
      {!inc ? (
        <div className="grid flex-1 place-items-center text-bone-faint">
          {error ? <p className="px-8 text-center text-[13px]">{error}</p> : <RotateCw className="animate-spin" size={18} />}
        </div>
      ) : (
        <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto pb-6">
          <div className="sticky top-0 z-10 flex items-center justify-between border-b border-transparent bg-ink-2/80 px-7 py-3 backdrop-blur-md">
            <span className="eyebrow">Dossier · {CATEGORY[inc.category]?.short ?? inc.category} · #{inc.incident_number}</span>
            <button className="btn btn-ghost -mr-2 size-9 justify-center p-0" onClick={() => select(null)} aria-label="Close dossier"><X size={17} /></button>
          </div>
          <header data-reveal className="relative px-7 pt-2 pb-6">
            <div className="flex items-end gap-6">
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[13px] text-bone-faint">INCIDENT</div>
                <div className="text-[64px] leading-[0.85] font-[700] tracking-[-0.05em] tabular">#{inc.incident_number}</div>
                <div className="mt-2 text-[22px] font-[600] tracking-[-0.02em]">{categoryLabel(inc.category)}
                  {inc.sub_type && <span className="font-serif font-normal text-bone-dim italic"> — {humanize(inc.sub_type)}</span>}</div>
              </div>
              <SeverityRing score={inc.severity} size={112} threshold={threshold} stroke={8} />
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {inc.live_call && <Pill tone="flare" dot blink>On line</Pill>}
              <Pill tone={inc.status === "needs_review" ? "sodium" : inc.status === "escalated" ? "flare" : active ? "ice" : "sage"}>{STATUS_LABEL[inc.status] ?? inc.status}</Pill>
              <Pill tone={inc.confidence === "low" ? "sodium" : "bone"}>{inc.confidence} confidence</Pill>
              {inc.report_count > 1 && <Pill tone="ice"><Layers size={10} /> {inc.report_count} callers</Pill>}
              {!!inc.people_affected && <Pill tone="bone">{inc.people_affected} affected</Pill>}
              <span className="ml-auto font-mono text-[10.5px] text-bone-faint">opened {ago(inc.created_at)} ago</span>
            </div>
            <div className="mt-4 flex items-start gap-2 text-[13.5px] text-bone-dim">
              <MapPin size={15} className="mt-0.5 shrink-0 text-bone-faint" />
              <span>{inc.address_text ?? "No location fixed yet"}
                {inc.lat != null && <span className="ml-2 font-mono text-[10.5px] text-bone-faint">{inc.lat.toFixed(4)}, {inc.lng?.toFixed(4)}</span>}
                {inc.location_confirmed && <span className="ml-2 inline-flex items-center gap-1 text-sage"><ShieldCheck size={12} /> caller-confirmed</span>}
              </span>
            </div>
            {inc.description && <p className="mt-3 font-serif text-[19px] leading-snug text-bone italic">“{inc.description}”</p>}

            <div className="mt-5 flex flex-wrap gap-2">
              {inc.status === "needs_review" && (<>
                <button className="btn btn-flare" disabled={!!busy} onClick={() => act("release", () => api.review(inc.id, "release"))}><Check size={14} /> Genuine — release to queue</button>
                <button className="btn" disabled={!!busy} onClick={() => act("reject", () => api.review(inc.id, "reject"))}>False alarm</button>
              </>)}
              {active && (<>
                <button className="btn" disabled={!!busy} onClick={() => act("resolve", () => api.setStatus(inc.id, "resolved"))}><CircleCheck size={14} /> Mark resolved</button>
                <button className="btn btn-ghost text-bone-dim" disabled={!!busy} onClick={() => act("close", () => api.setStatus(inc.id, "closed"))}>Close</button>
              </>)}
            </div>
            {error && d && <p role="alert" className="mt-3 text-[12.5px] text-flare">{error}</p>}
          </header>

            {!d ? (
              <div className="space-y-3 px-7 py-6">{[0, 1, 2].map((k) => <div key={k} className="h-16 rounded-[14px] bg-ink-3/60" />)}</div>
            ) : (<>
              <Section title="Why this severity" aside={<span className="font-mono text-[10.5px] text-bone-faint">rules on stated facts · critical ≥ {threshold}</span>}>
                {explanation.length ? (
                  <ol className="flex flex-col gap-2">
                    {explanation.map((e, k) => (
                      <li key={k} className="flex gap-3 text-[13.5px] leading-snug text-bone-dim">
                        <span className="font-mono text-[10.5px] text-bone-faint tabular">{String(k + 1).padStart(2, "0")}</span>{e}
                      </li>
                    ))}
                  </ol>
                ) : <p className="text-[13px] text-bone-faint">Not triaged yet.</p>}
                {signals.length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-1.5">
                    {signals.map(([k, v]) => (
                      <span key={k} className="rounded-[8px] border border-line bg-ink-1/60 px-2 py-1 font-mono text-[10.5px] text-bone-dim">
                        {humanize(k)}{v === true ? "" : <span className="text-bone"> · {String(v)}</span>}
                      </span>
                    ))}
                  </div>
                )}
                {inc.confidence_reasons.length > 0 && (
                  <p className="mt-4 flex gap-2 text-[12.5px] text-bone-faint"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{inc.confidence_reasons.join(" · ")}</p>
                )}
              </Section>

              <Section title="Response" aside={<span className="font-mono text-[10.5px] text-bone-faint">{d.assignments.length} unit{d.assignments.length === 1 ? "" : "s"}</span>}>
                {d.assignments.length === 0 ? <p className="text-[13px] text-bone-faint">No unit assigned.</p> : (
                  <ul className="flex flex-col gap-2.5">
                    {d.assignments.map((a) => (
                      <li key={a.id} className="rounded-[16px] border border-line bg-ink-1/50 p-4">
                        <div className="flex items-center gap-3">
                          <span className="grid h-9 w-12 place-items-center rounded-[9px] border border-line-strong font-mono text-[11px] text-ice">{RESOURCE[a.type ?? ""]?.code ?? "UNIT"}</span>
                          <div className="min-w-0 flex-1">
                            <div className="text-[16px] font-[620] tracking-[-0.01em]">{a.callsign}</div>
                            <div className="text-[12px] text-bone-dim">{RESOURCE[a.type ?? ""]?.label ?? a.type}</div>
                          </div>
                          <div className="text-right">
                            <div className="font-mono text-[17px] text-bone tabular">{mins(a.eta_minutes)}</div>
                            <div className="font-mono text-[10.5px] text-bone-faint">{km(a.distance_km)}</div>
                          </div>
                          <Pill tone={ASG_TONE[a.status] ?? "bone"}>{humanize(a.status)}</Pill>
                        </div>
                        {a.reason && <p className="mt-3 text-[12.5px] leading-snug text-bone-dim">{a.reason}</p>}
                        {a.score_breakdown && <ScoreBars b={a.score_breakdown} />}
                        {a.status === "pending_approval" && (
                          <div className="mt-4 flex gap-2">
                            <button className="btn btn-flare" disabled={!!busy} onClick={() => act(`ap-${a.id}`, () => api.approve(a.id))}><Check size={14} /> Approve dispatch</button>
                            <button className="btn" disabled={!!busy} onClick={() => act(`rj-${a.id}`, () => api.reject(a.id))}>Reject</button>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </Section>

              {(d.escalations.length > 0 || d.handoffs.length > 0) && (
                <Section title="Escalation">
                  <ul className="flex flex-col gap-3">
                    {d.escalations.map((e, k) => (
                      <li key={k} className="flex gap-3">
                        <span className="mt-1.5 size-2 shrink-0 rounded-full bg-flare shadow-[0_0_10px_var(--color-flare)]" />
                        <div>
                          <div className="flex flex-wrap gap-1.5">{(e.escalate_to ?? []).map((t) => <Pill key={t} tone="flare">{deptLabel(t)}</Pill>)}</div>
                          <p className="mt-1.5 text-[12.5px] leading-snug text-bone-dim">{e.reason}</p>
                          <p className="mt-1 font-mono text-[10px] text-bone-faint uppercase">{humanize(e.source)} · {e.created_at ? clockIST(e.created_at) : ""}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                  {d.handoffs.map((h) => (
                    <div key={h.id} className="mt-4 rounded-[16px] border border-sodium/25 bg-sodium/5 p-4">
                      <div className="flex items-center justify-between text-[13.5px] font-[600] text-sodium">
                        Live transfer → {h.department ?? "department"}
                        <span className="font-mono text-[10px] uppercase">{h.status}</span>
                      </div>
                      <ol className="mt-3 flex flex-col gap-1.5">
                        {h.attempts.length === 0 && <li className="text-[12px] text-bone-faint">{h.total_steps} contacts on the ladder — no dial yet (Twilio not connected).</li>}
                        {h.attempts.map((a) => (
                          <li key={`${a.step}-${a.cycle}`} className="flex items-center gap-2 text-[12px] text-bone-dim">
                            <span className="font-mono text-[10px] text-bone-faint">{a.step + 1}</span>{a.name}<span className="text-bone-faint">· {a.role}</span>
                            <span className="ml-auto font-mono text-[10px]">{a.answered ? "answered" : a.dial_status ?? "pending"}</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  ))}
                </Section>
              )}

              {d.reports.length > 1 && (
                <Section title="Callers on this event" aside={<span className="font-mono text-[10.5px] text-ice">merged by the duplicate check</span>}>
                  <ol className="flex flex-col gap-2.5">
                    {d.reports.map((r, k) => (
                      <li key={k} className="flex gap-3 text-[13px] leading-snug">
                        <span className="font-mono text-[10.5px] text-bone-faint">{r.created_at ? clockIST(r.created_at) : ""}</span>
                        <span className="text-bone-dim">{r.description}
                          {r.match_score != null && <span className="ml-2 font-mono text-[10.5px] text-ice">match {Math.round(r.match_score * 100)}%</span>}</span>
                      </li>
                    ))}
                  </ol>
                </Section>
              )}

              {transcript.length > 0 && (
                <Section title="Transcript">
                  <ol className="flex flex-col gap-3">
                    {transcript.map((t, k) => (
                      <li key={k} className={t.role === "user" || t.role === "caller" ? "font-serif text-[17px] leading-snug text-bone italic" : "pl-5 text-[13px] leading-relaxed text-ice/85"}>
                        {t.content}
                      </li>
                    ))}
                  </ol>
                  {d.calls.some((c) => c.summary) && (
                    <p className="mt-4 rounded-[12px] border border-line bg-ink-1/50 p-3 text-[12.5px] text-bone-dim">{d.calls.find((c) => c.summary)?.summary}</p>
                  )}
                </Section>
              )}

              <Section title="Audit trail · every tool call" aside={<span className="font-mono text-[10.5px] text-bone-faint">{d.tool_calls.length} calls</span>}>
                <ol className="relative flex flex-col">
                  {d.tool_calls.map((t, k) => {
                    const c = t.status === "rejected" ? "var(--color-sodium)" : t.status === "error" ? "var(--color-flare)" : "var(--color-ice)";
                    const hit = { id: k, tool: t.tool, status: t.status, duration_ms: t.duration_ms, args: t.arguments ?? {}, result: t.result ?? {}, ts: t.at ?? "", fresh: false };
                    return (
                      <li key={k} className="group relative pl-6">
                        <span className="absolute top-0 bottom-0 left-[4px] w-px bg-line group-last:bottom-auto group-last:h-3" />
                        <span className="absolute top-[7px] left-0 size-[9px] rounded-full border-2" style={{ borderColor: c, background: "var(--color-ink-2)" }} />
                        <details className="pb-3">
                          <summary className="flex cursor-pointer list-none items-baseline gap-2 text-[13px] [&::-webkit-details-marker]:hidden">
                            <ChevronRight size={12} className="shrink-0 self-center text-bone-faint transition-transform duration-150 group-has-[details[open]]:rotate-90" />
                            <span className="font-[600] text-bone">{PIPELINE[PIPELINE_INDEX[t.tool]]?.verb ?? t.tool}</span>
                            <span className="min-w-0 flex-1 truncate text-bone-dim">{summarizeTool(hit)}</span>
                            <span className="shrink-0 font-mono text-[10px] text-bone-faint tabular">{t.duration_ms ?? "–"}ms</span>
                          </summary>
                          <div className="mt-2 grid gap-2 pl-5">
                            <div className="font-mono text-[10px] text-bone-faint">{t.tool} · {t.at ? clockIST(t.at) : ""}</div>
                            <pre className="scroll-quiet max-h-56 overflow-auto rounded-[10px] border border-line bg-ink-0/70 p-3 font-mono text-[10.5px] leading-relaxed text-bone-dim">{JSON.stringify({ arguments: t.arguments, result: t.result }, null, 2)}</pre>
                          </div>
                        </details>
                      </li>
                    );
                  })}
                  {d.tool_calls.length === 0 && <li className="text-[13px] text-bone-faint">No tool calls recorded.</li>}
                </ol>
              </Section>

              {d.timeline.length > 0 && (
                <Section title="Timeline">
                  <ol className="flex flex-col gap-2">
                    {d.timeline.map((e, k) => (
                      <li key={k} className="grid grid-cols-[64px_1fr] gap-3 text-[12.5px]">
                        <span className="font-mono text-[10.5px] text-bone-faint tabular">{e.at ? clockIST(e.at) : ""}</span>
                        <span className="text-bone-dim"><span className="text-bone">{humanize(e.type)}</span> · {e.actor}
                          {e.type === "severity_estimated" && e.payload && (
                            <span className="ml-1 font-mono text-[10.5px]" style={{ color: SEVERITY_COLOR[levelOf(Number(e.payload.score))] }}>{String(e.payload.score)}</span>)}
                        </span>
                      </li>
                    ))}
                  </ol>
                </Section>
              )}
            </>)}
        </div>
      )}
    </aside>
  );
}
