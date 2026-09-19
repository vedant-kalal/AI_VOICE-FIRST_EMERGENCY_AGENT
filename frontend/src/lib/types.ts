// Shapes mirror the FastAPI serializers in app/api/v1/endpoints/dashboard.py and
// app/services/incident_service.serialize_incident — keep them in sync.

export type SeverityLevel = "low" | "medium" | "high" | "critical";
export type IncidentStatus =
  | "open" | "dispatched" | "escalated" | "needs_review" | "resolved" | "closed" | "merged" | string;
export type ResourceStatus = "available" | "reserved" | "en_route" | "on_scene" | "busy" | "offline";
export type AssignmentStatus =
  | "pending_approval" | "dispatched" | "en_route" | "arrived" | "completed" | "rejected" | string;

export interface Incident {
  id: string;
  incident_number: number;
  category: string;
  sub_type: string | null;
  description: string | null;
  address_text: string | null;
  lat: number | null;
  lng: number | null;
  location_confirmed: boolean;
  severity: number;
  severity_level: SeverityLevel | null;
  severity_explanation: string | null;
  status: IncidentStatus;
  confidence: "low" | "medium" | "high" | string;
  confidence_reasons: string[];
  people_affected: number | null;
  report_count: number;
  escalated: boolean;
  live_call: boolean;
  summary: string | null;
  merged_into_id: string | null;
  created_at: string | null;
}

export interface Resource {
  id: string;
  callsign: string;
  type: string;
  status: ResourceStatus;
  lat: number;
  lng: number;
  capabilities: string[];
  is_synthetic: boolean;
}

export interface Facility {
  id: string;
  type: string;
  name: string;
  lat: number;
  lng: number;
  capacity: Record<string, unknown>;
}

export interface Assignment {
  id: string;
  incident_id: string;
  resource_id: string;
  callsign: string | null;
  type: string | null;
  status: AssignmentStatus;
  eta_minutes: number | null;
  distance_km: number | null;
  reason: string | null;
  score_breakdown?: Record<string, number | string> | null;
  approved_by?: string | null;
  created_at?: string | null;
}

export interface StateSnapshot {
  dispatch_mode: "autonomous" | "approval" | string;
  critical_severity: number;
  center: { lat: number; lng: number };
  incidents: Incident[];
  resources: Resource[];
  facilities: Facility[];
  assignments: Assignment[];
  live_calls: number;
}

export interface ToolCallRecord {
  tool: string;
  status: string;
  duration_ms: number | null;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  at: string | null;
}

export interface IncidentDetail {
  incident: Incident;
  severity_signals: Record<string, unknown>;
  timeline: { type: string; actor: string; payload: Record<string, unknown> | null; at: string | null }[];
  handoffs: {
    id: string; status: string; department: string | null; total_steps: number; reason: string | null;
    attempts: { step: number; cycle: number; name: string; role: string; sms: string | null;
                dial_status: string | null; answered: boolean | null; duration_seconds: number | null }[];
  }[];
  reports: { description: string | null; people_affected: number | null; match_score: number | null;
             note: string | null; created_at: string | null }[];
  assignments: Assignment[];
  escalations: { reason: string; escalate_to: string[] | null; source: string; created_at: string | null }[];
  notifications: { channel: string; recipient: string | null; status: string; body: string | null }[];
  calls: { id: string; call_sid: string; status: string; is_live: boolean; duration_seconds: number | null;
           summary: string | null; caller_language: string | null;
           transcript: { role: string; content: string }[] }[];
  tool_calls: ToolCallRecord[];
}

export interface CallRow {
  id: string;
  call_sid: string;
  from: string | null;
  status: string;
  is_live: boolean;
  duration_seconds: number | null;
  summary: string | null;
  incident_id: string | null;
  transferred_to_human: boolean;
}

/** Every message on /ws/dashboard: {type, ts, data}. */
export interface HubEvent<T = Record<string, any>> {
  type: string;
  ts: string;
  data: T;
}
