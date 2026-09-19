const IST = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

/** Backend timestamps are UTC; SQLite drops the offset ("2026-09-19T11:02:13.5"), which browsers would read
 *  as local time. Treat any ISO string without Z/±hh:mm as UTC. */
export function parseTs(v: string | number | Date): number {
  if (typeof v !== "string") return new Date(v).getTime();
  return Date.parse(/[zZ]$|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`);
}

export const clockIST = (d: Date | string | number) => IST.format(new Date(parseTs(d)));

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - parseTs(iso)) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export const pad = (n: number, w = 4) => String(n).padStart(w, "0");
export const km = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(n < 10 ? 1 : 0)} km`);
export const mins = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n)} min`);
export const humanize = (s: string) => s.replaceAll("_", " ");
export const maskPhone = (p: string | null | undefined) =>
  !p ? "unknown" : p.length > 6 ? `${p.slice(0, 3)} ••• ${p.slice(-4)}` : p;
