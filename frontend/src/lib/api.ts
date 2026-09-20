import type { Assignment, CallDetail, CallRow, Incident, IncidentDetail, StateSnapshot, Taxonomy } from "./types";

const KEY = import.meta.env.VITE_DASHBOARD_API_KEY as string | undefined;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (KEY) headers.set("X-API-Key", KEY);
  if (init?.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  state: () => request<StateSnapshot>("/api/state"),
  incident: (id: string) => request<IncidentDetail>(`/api/incidents/${id}`),
  reviewQueue: () => request<Incident[]>("/api/review-queue"),
  taxonomy: () => request<Taxonomy>("/api/taxonomy"),
  calls: (opts: { limit?: number; offset?: number; sinceHours?: number | null; department?: string | null } = {}) => {
    const q = new URLSearchParams({ limit: String(opts.limit ?? 60), offset: String(opts.offset ?? 0) });
    if (opts.sinceHours) q.set("since_hours", String(opts.sinceHours));
    if (opts.department) q.set("department", opts.department);
    return request<CallRow[]>(`/api/calls?${q}`);
  },
  call: (id: string) => request<CallDetail>(`/api/calls/${id}`),
  review: (id: string, action: "release" | "reject") => post<Incident>(`/api/incidents/${id}/review`, { action }),
  setStatus: (id: string, status: "resolved" | "closed") => post<Incident>(`/api/incidents/${id}/status`, { status }),
  approve: (id: string, approver = "dhvani-console") => post<Assignment>(`/api/assignments/${id}/approve`, { approver }),
  reject: (id: string) => post<Assignment>(`/api/assignments/${id}/reject`),
  runDemo: (scenario: string, pace: number) =>
    post<{ started: string }>(`/api/dev/demo/${scenario}?pace=${pace}`),
};

export function socketUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const q = KEY ? `?key=${encodeURIComponent(KEY)}` : "";
  return `${proto}//${location.host}/ws/dashboard${q}`;
}
