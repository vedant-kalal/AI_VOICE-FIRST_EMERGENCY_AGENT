import { lazy, Suspense, useEffect, useState } from "react";
import { CommandBar } from "@/components/layout/CommandBar";
import { IncidentRail } from "@/features/incidents/IncidentRail";
import { LiveLine } from "@/features/live-call/LiveLine";
import { Wire } from "@/features/wire/Wire";
import { Dossier } from "@/features/incident-detail/Dossier";
import { DrillDeck } from "@/features/scenarios/DrillDeck";
import { MapLegend } from "@/features/map/MapLegend";
import { DepartmentDrawer } from "@/features/lens/DepartmentDrawer";
import { CallLog, TranscriptDrawer } from "@/features/call-log/CallLog";
import { startLiveLink } from "@/lib/socket";
import { useLive } from "@/store/live";
import type { ConsoleView } from "@/lib/view";

// MapLibre is ~2/3 of the bundle: let the console paint first and stream the map in behind it.
const OpsMap = lazy(() => import("@/features/map/OpsMap").then((m) => ({ default: m.OpsMap })));

export default function App() {
  const [drill, setDrill] = useState(false);
  const [lensOpen, setLensOpen] = useState(false);
  const [view, setView] = useState<ConsoleView>("board");
  const [openCall, setOpenCall] = useState<string | null>(null);
  const selected = useLive((s) => s.selectedId);
  useEffect(() => startLiveLink(), []);
  const openDrill = () => setDrill(true);

  // The dossier wins the drawer slot: opening an incident from a transcript should show the incident.
  const drawer = selected ? "incident" : view === "calls" && openCall ? "call" : null;

  return (
    <div className={`grain shell relative h-full w-full overflow-hidden bg-ink-0 ${view === "calls" ? "view-calls" : ""}`}>
      <div className="map-slot"><Suspense fallback={null}><OpsMap /></Suspense></div>

      {/* Floating console. The wrapper ignores the pointer so the map stays draggable between panels. */}
      <div className="console pointer-events-none">
        <div className="area-bar pointer-events-auto">
          <CommandBar onDrill={openDrill} onLens={() => setLensOpen(true)} view={view} onView={setView} />
        </div>

        {view === "board" ? (
          <>
            <div className="area-rail pointer-events-auto min-h-0"><IncidentRail onDrill={openDrill} /></div>
            <div className="area-line pointer-events-auto min-h-0"><LiveLine onDrill={openDrill} /></div>
            <div className="area-wire pointer-events-auto min-w-0"><Wire /></div>
            <div className="area-legend pointer-events-auto self-start justify-self-start"><MapLegend /></div>
          </>
        ) : (
          <div className="area-archive pointer-events-auto min-h-0">
            <CallLog openId={openCall} onOpen={setOpenCall} />
          </div>
        )}

        {drawer === "incident" && <div className="area-dossier pointer-events-auto min-h-0"><Dossier /></div>}
        {drawer === "call" && openCall && (
          <div className="area-dossier pointer-events-auto min-h-0">
            <TranscriptDrawer id={openCall} onClose={() => setOpenCall(null)} />
          </div>
        )}
      </div>

      <DrillDeck open={drill} onClose={() => setDrill(false)} />
      <DepartmentDrawer open={lensOpen} onClose={() => setLensOpen(false)} />
    </div>
  );
}
