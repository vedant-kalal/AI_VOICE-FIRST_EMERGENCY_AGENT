import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";

/** The key that starts the cued scenario. Shown on screen so a presenter can find it. */
export const CUE_KEY = "v";
export const CUE_SCENARIO = "f";

type Status = { state: "idle" } | { state: "starting" } | { state: "running"; startedAt: number } | { state: "error"; message: string };

const isTyping = () => {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
};

/**
 * Press V to put the cued call on the line. The scenario runs on its own absolute clock (the "at" cues on
 * the backend), so pressing the key at the right moment is all the synchronisation it needs.
 */
export function useCueHotkey() {
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const busy = useRef(false);

  const fire = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setStatus({ state: "starting" });
    try {
      await api.runDemo(CUE_SCENARIO, 1);
      setStatus({ state: "running", startedAt: Date.now() });
    } catch (e) {
      setStatus({
        state: "error",
        message: e instanceof ApiError && e.status === 404
          ? "Scenarios are off on the backend (ENABLE_DEV_ENDPOINTS=true)."
          : e instanceof Error ? e.message : "Could not start",
      });
    } finally {
      // Guard only against a double tap, not against running it twice in a demo.
      window.setTimeout(() => { busy.current = false; }, 1500);
    }
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== CUE_KEY) return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || isTyping()) return;
      e.preventDefault();
      void fire();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fire]);

  // The banner clears itself once the call is well under way.
  useEffect(() => {
    if (status.state === "idle") return;
    const t = window.setTimeout(() => setStatus({ state: "idle" }), status.state === "error" ? 6000 : 4000);
    return () => window.clearTimeout(t);
  }, [status]);

  return { status, fire };
}
