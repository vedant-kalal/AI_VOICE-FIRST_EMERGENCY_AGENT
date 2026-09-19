import type { Assignment, CallRow, Incident, IncidentDetail, StateSnapshot } from "./types";

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
  calls: (limit = 50) => request<CallRow[]>(`/api/calls?limit=${limit}`),
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
