import { Ambulance, Biohazard, Building2, ClipboardList, Flame, Leaf, LifeBuoy, PlugZap, Shield, TrafficCone, Wind } from "lucide-react";
import type { LucideIcon } from "lucide-react";

/** One accent per department so a lens reads instantly: severity colours stay reserved for severity. */
export const DEPT_ACCENT: Record<string, string> = {
  supervisor: "#ece6d9",
  fire_dept: "#ff4a1c",
  hazmat: "#f6c453",
  police: "#8ec5ff",
  ems: "#ff8fa3",
  traffic_police: "#ff8a3d",
  disaster_management: "#9cc5a1",
  municipal_corp: "#a29d91",
  utility_board: "#c3a6ff",
  pollution_control: "#7fd4c1",
  forest_dept: "#8fbf6a",
};

const ICONS: Record<string, LucideIcon> = {
  supervisor: ClipboardList,
  fire_dept: Flame,
  hazmat: Biohazard,
  police: Shield,
  ems: Ambulance,
  traffic_police: TrafficCone,
  disaster_management: LifeBuoy,
  municipal_corp: Building2,
  utility_board: PlugZap,
  pollution_control: Wind,
  forest_dept: Leaf,
};

export function DeptGlyph({ dept, size = 16 }: { dept: string; size?: number }) {
  const Icon = ICONS[dept] ?? Building2;
  return <Icon size={size} strokeWidth={1.9} aria-hidden />;
}

export const deptAccent = (dept: string | null | undefined) =>
  (dept && DEPT_ACCENT[dept]) || "var(--color-flare)";
