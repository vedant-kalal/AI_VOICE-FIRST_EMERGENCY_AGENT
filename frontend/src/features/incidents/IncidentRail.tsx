import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Check, Layers, PhoneCall, ShieldAlert, Users, X } from "lucide-react";
import { ACTIVE, useLive } from "@/store/live";
import { useDepartmentLabel, useLensDept, useScopedIncidents } from "@/features/lens/scope";
import { DeptGlyph, deptAccent } from "@/features/lens/deptVisuals";
import { SeverityRing } from "@/components/SeverityRing";
import { Pill } from "@/components/Pill";
import { CATEGORY, categoryLabel, STATUS_LABEL } from "@/lib/taxonomy";
import { ago } from "@/lib/format";
import { api } from "@/lib/api";
import { Flip, gsap, prefersReducedMotion } from "@/lib/motion";
import type { Incident } from "@/lib/types";

type Tab = "active" | "review" | "closed";
const STATUS_TONE: Record<string, string> = {
  open: "ember", dispatched: "ice", escalated: "flare", needs_review: "sodium", resolved: "sage", closed: "bone",
};

function useNow(ms = 5000) {
  const [n, setN] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setN(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return n;
}

function IncidentCard({ i, selected, now, flashAt }: { i: Incident; selected: boolean; now: number; flashAt?: number }) {
  const el = useRef<HTMLLIElement>(null);
  const [busy, setBusy] = useState(false);
  const select = useLive((s) => s.select);
  const upsert = useLive((s) => s.upsertIncident);

  useEffect(() => {
    if (!flashAt || !el.current || prefersReducedMotion()) return;
    if (Date.now() - flashAt > 1500) return;
    gsap.fromTo(el.current.querySelector(".flash"), { opacity: 0.55 }, { opacity: 0, duration: 1.4, ease: "power2.out" });
  }, [flashAt]);

  const review = async (action: "release" | "reject") => {
    setBusy(true);
    try { upsert(await api.review(i.id, action)); } finally { setBusy(false); }
  };

  return (
    <li ref={el} data-flip-id={i.id} className="relative">
      <button
        onClick={() => select(selected ? null : i.id)}
        aria-pressed={selected}
        className={`group relative flex w-full gap-3.5 overflow-hidden rounded-[16px] border p-3.5 text-left transition-[background,border-color] duration-150
          ${selected ? "border-line-strong bg-ink-3" : "border-transparent hover:border-line hover:bg-ink-3/60"}`}>
        <span className="flash pointer-events-none absolute inset-0 opacity-0"
              style={{ background: "linear-gradient(90deg, rgb(255 74 28 / 0.35), transparent 70%)" }} />
        <SeverityRing score={i.severity} size={48} />
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex items-center gap-2 font-mono text-[10.5px] tracking-[0.1em] text-bone-faint">
            <span className="text-bone-dim">#{i.incident_number}</span>
            <span>·</span>
            <span>{CATEGORY[i.category]?.short ?? i.category.toUpperCase()}</span>
            <span className="ml-auto tabular">{ago(i.created_at, now)}</span>
          </span>
          <span className="truncate text-[16.5px] font-[600] leading-tight tracking-[-0.01em]">{categoryLabel(i.category)}</span>
          <span className="truncate text-[12.5px] text-bone-dim">{i.address_text ?? "Location not yet fixed"}</span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            {i.live_call && <Pill tone="flare" dot blink>On line</Pill>}
            <Pill tone={STATUS_TONE[i.status] ?? "bone"}>{STATUS_LABEL[i.status] ?? i.status}</Pill>
            {i.report_count > 1 && (
              <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-ice"><Layers size={11} />{i.report_count} callers</span>
            )}
            {!!i.people_affected && (
              <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-bone-dim"><Users size={11} />{i.people_affected}</span>
            )}
            {i.escalated && <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-flare"><ShieldAlert size={11} />esc</span>}
          </span>
        </span>
      </button>
      {i.status === "needs_review" && (
        <div className="mx-3.5 mb-2 -mt-1 flex items-center gap-2 rounded-[12px] border border-line bg-ink-2 p-2 pl-3">
          <span className="flex-1 truncate text-[11.5px] text-bone-dim">{i.confidence_reasons[0] ?? "Low-confidence caller"}</span>
          <button className="btn h-8 min-h-0 px-3 text-[12px]" disabled={busy} onClick={() => review("release")}><Check size={13} />Genuine</button>
          <button className="btn btn-ghost h-8 min-h-0 px-2.5 text-[12px] text-bone-dim" disabled={busy} onClick={() => review("reject")} aria-label="Reject as false alarm"><X size={14} /></button>
        </div>
      )}
    </li>
  );
}

export function IncidentRail({ onDrill }: { onDrill: () => void }) {
  const [tab, setTab] = useState<Tab>("active");
  const now = useNow();
  const { selectedId, flashAt, hydrated } = useLive(useShallow((s) => ({
    selectedId: s.selectedId, flashAt: s.flashAt, hydrated: s.hydrated })));
  const { list: scoped } = useScopedIncidents();
  const dept = useLensDept();
  const deptName = useDepartmentLabel(dept);

  const groups = useMemo(() => {
    const all = scoped;
    const byUrgency = (a: Incident, b: Incident) =>
      Number(b.live_call) - Number(a.live_call) || b.severity - a.severity || (b.created_at ?? "").localeCompare(a.created_at ?? "");
    return {
      active: all.filter((i) => ACTIVE.has(i.status)).sort(byUrgency),
      review: all.filter((i) => i.status === "needs_review").sort(byUrgency),
      closed: all.filter((i) => i.status === "resolved" || i.status === "closed")
        .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")),
    };
  }, [scoped]);
  const list = groups[tab];

  // FLIP: when severity reshuffles the queue, cards glide to their new rank instead of teleporting.
  const listRef = useRef<HTMLUListElement>(null);
  const flipState = useRef<ReturnType<typeof Flip.getState> | null>(null);
  const order = `${tab}:${list.map((i) => i.id).join()}`;
  const prevOrder = useRef(order);
  if (order !== prevOrder.current && listRef.current && !prefersReducedMotion())
    flipState.current = Flip.getState(listRef.current.children);
  prevOrder.current = order;
  useLayoutEffect(() => {
    if (!flipState.current) return;
    Flip.from(flipState.current, { targets: listRef.current?.children, duration: 0.5, ease: "expo.out", absolute: false,
      onEnter: (els) => gsap.fromTo(els, { opacity: 0, y: -10 }, { opacity: 1, y: 0, duration: 0.45, ease: "expo.out", stagger: 0.04 }) });
    flipState.current = null;
  }, [order]);

  const tabs: { k: Tab; label: string; n: number }[] = [
    { k: "active", label: "Active", n: groups.active.length },
    { k: "review", label: "Review", n: groups.review.length },
    { k: "closed", label: "Closed", n: groups.closed.length },
  ];

  return (
    <section className="panel flex h-full flex-col overflow-hidden" aria-label="Incident queue">
      <div className="flex items-end justify-between px-5 pt-5 pb-3">
        <div className="min-w-0">
          <div className="eyebrow flex items-center gap-1.5">
            {dept ? <><span style={{ color: deptAccent(dept) }}><DeptGlyph dept={dept} size={11} /></span>
              <span className="truncate" style={{ color: deptAccent(dept) }}>{deptName}</span></> : "Queue · city-wide"}
          </div>
          <h2 className="mt-1 text-[22px] font-[650] tracking-[-0.025em]">Incidents</h2>
        </div>
        <div role="tablist" className="flex rounded-full border border-line bg-ink-1/60 p-1">
          {tabs.map((t) => (
            <button key={t.k} role="tab" aria-selected={tab === t.k} onClick={() => setTab(t.k)}
                    className={`flex h-7 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-[550] transition-colors duration-150
                      ${tab === t.k ? "bg-ink-4 text-bone" : "text-bone-faint hover:text-bone-dim"}`}>
              {t.label}
              <span className={`font-mono text-[10px] tabular ${t.k === "review" && t.n ? "text-sodium" : ""}`}>{t.n}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="scroll-quiet fade-mask-b min-h-0 flex-1 overflow-y-auto px-2 pb-8">
        {!hydrated ? (
          <ul className="flex flex-col gap-2 px-1.5" aria-busy>
            {[0, 1, 2].map((k) => (
              <li key={k} className="relative h-[92px] overflow-hidden rounded-[16px] bg-ink-3/60">
                <span className="absolute inset-0 bg-gradient-to-r from-transparent via-bone/5 to-transparent" style={{ animation: "shimmer 1.4s infinite" }} />
              </li>
            ))}
          </ul>
        ) : list.length === 0 ? (
          <EmptyQueue tab={tab} onDrill={onDrill} />
        ) : (
          <ul ref={listRef} className="flex flex-col gap-1">
            {list.map((i) => (
              <IncidentCard key={i.id} i={i} selected={i.id === selectedId} now={now} flashAt={flashAt[i.id]} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function EmptyQueue({ tab, onDrill }: { tab: Tab; onDrill: () => void }) {
  const copy = {
    active: ["All quiet on the line.", "Incidents appear here the moment the agent logs them, ranked by severity."],
    review: ["Nothing waiting on a human.", "Low-confidence and prank reports park here — never dispatched until you release them."],
    closed: ["No closed incidents yet.", "Resolve an incident from its dossier and it moves here."],
  }[tab];
  return (
    <div className="flex flex-col items-start gap-3 px-4 pt-10">
      <PhoneCall size={20} className="text-bone-faint" />
      <p className="font-serif text-[28px] leading-[1.05] italic text-bone">{copy[0]}</p>
      <p className="max-w-[30ch] text-[13px] leading-relaxed text-bone-dim">{copy[1]}</p>
      {tab === "active" && <button className="btn mt-2" onClick={onDrill}>Open a scenario</button>}
    </div>
  );
}
