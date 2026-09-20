import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, Clock, Languages, PhoneForwarded, RotateCw, Search, Tag, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import type { CallRow } from "@/lib/types";
import { useLive } from "@/store/live";
import { useDepartmentLabel, useLensDept } from "@/features/lens/scope";
import { DeptGlyph, deptAccent } from "@/features/lens/deptVisuals";
import { CATEGORY, SEVERITY_COLOR, categoryLabel, levelOf } from "@/lib/taxonomy";
import { ago, clockIST, humanize, maskPhone } from "@/lib/format";
import { Pill } from "@/components/Pill";
import { gsap, prefersReducedMotion, stagger } from "@/lib/motion";

const RANGES = [
  { k: "24h", label: "24 hours", hours: 24 },
  { k: "week", label: "7 days", hours: 24 * 7 },
  { k: "month", label: "30 days", hours: 24 * 30 },
  { k: "all", label: "All time", hours: null },
] as const;
type RangeKey = (typeof RANGES)[number]["k"];

const duration = (s: number | null) =>
  s == null ? "—" : s < 60 ? `${s} sec` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} sec`;

function CallCard({ c, index, selected, onOpen }: { c: CallRow; index: number; selected: boolean; onOpen: () => void }) {
  const level = c.severity_level ?? (c.severity != null ? levelOf(c.severity) : null);
  return (
    <li data-card>
      <button onClick={onOpen} aria-pressed={selected}
              className={`flex h-full w-full flex-col gap-3 rounded-[18px] border p-4 text-left transition-[border-color,background,translate] duration-150 hover:-translate-y-0.5
                ${selected ? "border-line-strong bg-ink-3" : "border-line bg-ink-1/45 hover:border-line-strong hover:bg-ink-3/70"}`}>
        <div className="flex items-start gap-2.5">
          <span className="grid size-7 shrink-0 place-items-center rounded-[9px] border border-line font-mono text-[10.5px] text-bone-faint tabular">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-[12px] text-bone">{c.call_sid}</span>
            <span className="mt-1 block font-mono text-[10.5px] text-bone-faint">
              {c.created_at ? `${new Date(c.created_at.endsWith("Z") ? c.created_at : `${c.created_at}Z`).toLocaleDateString("en-IN", { day: "2-digit", month: "2-digit", year: "numeric" })} ${clockIST(c.created_at)}` : "—"}
            </span>
          </span>
          {c.is_live ? <Pill tone="flare" dot blink>Live</Pill>
            : c.transferred_to_human ? <Pill tone="sodium">Transferred</Pill>
            : <Pill tone="bone">{humanize(c.status)}</Pill>}
        </div>

        <dl className="grid grid-cols-[16px_1fr] items-center gap-x-2 gap-y-1.5 text-[12.5px] text-bone-dim">
          <dt className="text-bone-faint"><Languages size={13} /></dt>
          <dd className="truncate">{c.caller_language ?? "Language not detected"}</dd>
          <dt className="text-bone-faint"><Clock size={13} /></dt>
          <dd className="tabular">{duration(c.duration_seconds)}</dd>
          <dt className="text-bone-faint"><Tag size={13} /></dt>
          <dd className="truncate">{c.from ? maskPhone(c.from) : "Unknown caller"}</dd>
        </dl>

        {c.category ? (
          <div className="mt-auto rounded-[12px] border border-line bg-ink-0/40 p-2.5">
            <div className="flex items-center gap-2">
              <span className="size-2 shrink-0 rounded-full" style={{ background: level ? SEVERITY_COLOR[level] : "var(--color-bone-faint)" }} />
              <span className="truncate text-[13px] font-[600]">{categoryLabel(c.category)}</span>
              <span className="ml-auto font-mono text-[10.5px] text-bone-faint">#{c.incident_number}</span>
            </div>
            <p className="mt-1 truncate text-[11.5px] text-bone-dim">
              {c.sub_type ? `${humanize(c.sub_type)} · ` : ""}{c.address_text ?? "location not fixed"}
            </p>
          </div>
        ) : (
          <div className="mt-auto rounded-[12px] border border-dashed border-line p-2.5 text-[11.5px] text-bone-faint">
            No incident logged on this call
          </div>
        )}
      </button>
    </li>
  );
}

function TranscriptDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.call>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLElement>(null);
  const select = useLive((s) => s.select);
  const departments = useLive((s) => s.taxonomy.departments);
  const label = (k: string) => departments.find((x) => x.key === k)?.label ?? k;

  useEffect(() => {
    let live = true;
    setD(null); setError(null);
    api.call(id).then((r) => live && setD(r)).catch((e) => live && setError(e instanceof Error ? e.message : "Failed"));
    return () => { live = false; };
  }, [id]);

  useEffect(() => {
    if (!d || !panel.current || prefersReducedMotion()) return;
    gsap.timeline()
      .fromTo(panel.current, { x: 30, opacity: 0 }, { x: 0, opacity: 1, duration: 0.4, ease: "expo.out" })
      .fromTo(panel.current.querySelectorAll("[data-line]"), { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.4, ease: "expo.out", stagger: { each: 0.03, amount: 0.3 } }, 0.05);
  }, [d]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  return (
    <aside ref={panel} aria-label="Call transcript" className="panel flex h-full flex-col overflow-hidden">
      <header className="flex items-start justify-between gap-3 border-b border-line px-6 py-4">
        <div className="min-w-0">
          <div className="eyebrow">Transcript</div>
          <div className="mt-1 truncate font-mono text-[13px] text-bone">{d?.call_sid ?? "loading…"}</div>
          {d && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Pill tone="ice">{d.caller_language ?? "language n/a"}</Pill>
              <Pill tone="bone">{duration(d.duration_seconds)}</Pill>
              {d.transferred_to_human && <Pill tone="sodium"><PhoneForwarded size={10} /> transferred</Pill>}
            </div>
          )}
        </div>
        <button className="btn btn-ghost size-9 shrink-0 justify-center p-0" onClick={onClose} aria-label="Close transcript">✕</button>
      </header>

      <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {error && <p className="text-[13px] text-flare">{error}</p>}
        {!d && !error && <div className="grid h-full place-items-center text-bone-faint"><RotateCw className="animate-spin" size={18} /></div>}
        {d && (
          <>
            {d.incident_id && (
              <button onClick={() => select(d.incident_id!)}
                      className="group mb-5 flex w-full items-center gap-2 rounded-[14px] border border-line bg-ink-1/50 p-3 text-left transition-colors hover:border-line-strong hover:bg-ink-3">
                <span className="size-2 rounded-full" style={{ background: d.severity_level ? SEVERITY_COLOR[d.severity_level] : "var(--color-bone-faint)" }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[14px] font-[600]">#{d.incident_number} {d.category ? categoryLabel(d.category) : ""}</span>
                  <span className="block truncate text-[11.5px] text-bone-dim">{d.address_text ?? "location not fixed"}</span>
                </span>
                <ArrowUpRight size={14} className="text-bone-faint transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </button>
            )}

            {(d.departments.length > 0 || d.escalated_to.length > 0) && (
              <div className="mb-5">
                <div className="eyebrow mb-2">Responsible</div>
                <div className="flex flex-wrap gap-1.5">
                  {d.departments.map((k) => (
                    <span key={k} className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px]"
                          style={{ color: deptAccent(k), borderColor: `color-mix(in srgb, ${deptAccent(k)} 35%, transparent)` }}>
                      <DeptGlyph dept={k} size={11} />{label(k)}
                    </span>
                  ))}
                  {d.escalated_to.filter((k) => !d.departments.includes(k)).map((k) => (
                    <span key={k} className="inline-flex items-center gap-1.5 rounded-full border border-dashed px-2.5 py-1 text-[11.5px]"
                          style={{ color: deptAccent(k), borderColor: `color-mix(in srgb, ${deptAccent(k)} 35%, transparent)` }}>
                      <DeptGlyph dept={k} size={11} />{label(k)} · escalated
                    </span>
                  ))}
                </div>
              </div>
            )}

            {d.transcript.length === 0 ? (
              <p className="text-[13px] text-bone-faint">No transcript was recorded for this call.</p>
            ) : (
              <ol className="flex flex-col gap-4">
                {d.transcript.map((t, k) => {
                  const caller = t.role === "user" || t.role === "caller";
                  return (
                    <li key={k} data-line className="flex flex-col gap-1">
                      <span className="eyebrow" style={{ color: caller ? undefined : "var(--color-ice)" }}>
                        {caller ? "Caller" : "Dhvani agent"}
                      </span>
                      <p className={caller ? "font-serif text-[19px] leading-[1.25] text-bone italic"
                                           : "pl-4 text-[13.5px] leading-[1.55] text-ice/90"}>{t.content}</p>
                    </li>
                  );
                })}
              </ol>
            )}

            {d.summary && (
              <p className="mt-6 rounded-[12px] border border-line bg-ink-1/50 p-3 text-[12.5px] leading-relaxed text-bone-dim">
                {d.summary}
              </p>
            )}

            {d.tool_calls.length > 0 && (
              <div className="mt-6 border-t border-line pt-4">
                <div className="eyebrow mb-3 flex items-center gap-1.5"><Wrench size={11} /> {d.tool_calls.length} tool calls</div>
                <ol className="flex flex-col gap-1.5">
                  {d.tool_calls.map((t, k) => (
                    <li key={k} className="flex items-center gap-2 text-[12px]">
                      <span className="size-1.5 shrink-0 rounded-full"
                            style={{ background: t.status === "rejected" ? "var(--color-sodium)" : t.status === "error" ? "var(--color-flare)" : "var(--color-ice)" }} />
                      <span className="truncate text-bone-dim">{humanize(t.tool)}</span>
                      <span className="ml-auto shrink-0 font-mono text-[10px] text-bone-faint tabular">{t.duration_ms ?? "–"}ms</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

export function CallLog({ openId, onOpen }: { openId: string | null; onOpen: (id: string | null) => void }) {
  const dept = useLensDept();
  const deptName = useDepartmentLabel(dept);
  const [range, setRange] = useState<RangeKey>("all");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<CallRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const grid = useRef<HTMLUListElement>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const hours = RANGES.find((r) => r.k === range)?.hours ?? null;
      setRows(await api.calls({ limit: 120, sinceHours: hours, department: dept }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load calls");
      setRows([]);
    }
  }, [range, dept]);

  useEffect(() => { setRows(null); load(); }, [load]);

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle || !rows) return rows ?? [];
    return rows.filter((c) => [c.call_sid, c.from, c.caller_language, c.category, c.address_text,
      c.incident_number != null ? `#${c.incident_number}` : ""]
      .some((v) => (v ?? "").toString().toLowerCase().includes(needle)));
  }, [rows, q]);

  useEffect(() => {
    if (!grid.current || !rows || prefersReducedMotion()) return;
    gsap.fromTo(grid.current.querySelectorAll("[data-card]"), { opacity: 0, y: 14 },
      { opacity: 1, y: 0, duration: 0.45, ease: "expo.out", stagger: (i: number) => stagger(i) });
  }, [rows]);

  const languages = useMemo(() => {
    const set = new Map<string, number>();
    for (const c of rows ?? []) set.set(c.caller_language ?? "unknown", (set.get(c.caller_language ?? "unknown") ?? 0) + 1);
    return [...set.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
  }, [rows]);

  return (
    <section className="panel flex h-full min-h-0 flex-col overflow-hidden" aria-label="Call log">
      <header className="flex flex-wrap items-end justify-between gap-4 px-6 pt-6 pb-4">
        <div>
          <div className="eyebrow flex items-center gap-1.5">
            {dept ? <><span style={{ color: deptAccent(dept) }}><DeptGlyph dept={dept} size={11} /></span>
              <span style={{ color: deptAccent(dept) }}>{deptName}</span></> : "Archive · city-wide"}
          </div>
          <h2 className="mt-1.5 text-[26px] leading-none font-[650] tracking-[-0.03em]">Call log</h2>
          <p className="mt-2 max-w-[62ch] text-[12.5px] text-bone-dim">
            Every call the agent has handled, with its transcript, the incident it produced and the tools it used.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-9 items-center gap-2 rounded-full border border-line bg-ink-1/60 px-3">
            <Search size={13} className="text-bone-faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search number, SID, area…"
                   aria-label="Search calls"
                   className="w-44 bg-transparent text-[12.5px] text-bone placeholder:text-bone-faint focus:outline-none" />
          </label>
          <div role="tablist" aria-label="Time range" className="flex rounded-full border border-line bg-ink-1/60 p-1">
            {RANGES.map((r) => (
              <button key={r.k} role="tab" aria-selected={range === r.k} onClick={() => setRange(r.k)}
                      className={`h-7 rounded-full px-2.5 text-[12px] font-[550] transition-colors duration-150
                        ${range === r.k ? "bg-ink-4 text-bone" : "text-bone-faint hover:text-bone-dim"}`}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-line px-6 py-3">
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-[18px] text-bone tabular">{String(list.length).padStart(2, "0")}</span>
          <span className="eyebrow">calls shown</span>
        </span>
        {languages.map(([lang, n]) => (
          <span key={lang} className="flex items-center gap-1.5 text-[12px] text-bone-dim">
            <span className="size-1.5 rounded-full bg-ice/60" />{lang}
            <span className="font-mono text-[10.5px] text-bone-faint">{n}</span>
          </span>
        ))}
        <span className="ml-auto font-mono text-[10.5px] text-bone-faint">
          {rows ? `updated ${ago(new Date().toISOString())} ago` : ""}
        </span>
      </div>

      <div className="scroll-quiet fade-mask-b min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {error ? (
          <div className="flex flex-col items-start gap-3">
            <p className="font-serif text-[24px] text-bone italic">The archive is unreachable.</p>
            <p className="text-[13px] text-bone-dim">{error}</p>
            <button className="btn" onClick={load}>Try again</button>
          </div>
        ) : !rows ? (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
            {[0, 1, 2, 3, 4, 5].map((k) => (
              <li key={k} className="relative h-[190px] overflow-hidden rounded-[18px] bg-ink-3/50">
                <span className="absolute inset-0 bg-gradient-to-r from-transparent via-bone/5 to-transparent" style={{ animation: "shimmer 1.4s infinite" }} />
              </li>
            ))}
          </ul>
        ) : list.length === 0 ? (
          <div className="flex flex-col items-start gap-3 pt-6">
            <p className="font-serif text-[28px] leading-[1.05] text-bone italic">
              {q ? "Nothing matches that." : dept ? `No calls for ${deptName} yet.` : "No calls in this window."}
            </p>
            <p className="max-w-[44ch] text-[13px] leading-relaxed text-bone-dim">
              {q ? "Try a phone number, a call SID, an incident number or an area name."
                 : "Open a scenario from the live board, or widen the time range."}
            </p>
          </div>
        ) : (
          <ul ref={grid} className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-3">
            {list.map((c, i) => (
              <CallCard key={c.id} c={c} index={i} selected={openId === c.id} onOpen={() => onOpen(openId === c.id ? null : c.id)} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export { TranscriptDrawer };
export const CALL_CATEGORY_KEYS = Object.keys(CATEGORY);
