import { api, socketUrl } from "./api";
import type { HubEvent } from "./types";
import { useLive } from "@/store/live";

/** The hub replays its last 200 events to every new socket. Anything older than this is history:
 *  it rebuilds transcripts and the wire, but must not replay animations. */
const REPLAY_AGE_MS = 4000;

export function startLiveLink(): () => void {
  let ws: WebSocket | null = null;
  let retry = 0;
  let stopped = false;
  let ping: number | undefined;
  let timer: number | undefined;
  const store = useLive.getState;

  const hydrate = () =>
    api.state().then((s) => store().hydrate(s)).catch((e) => console.warn("state fetch failed", e));

  // Department/category map for the access lens — served from the backend taxonomy, fetched once.
  api.taxonomy().then((t) => store().setTaxonomy(t)).catch((e) => console.warn("taxonomy fetch failed", e));

  const connect = () => {
    store().setConn("connecting");
    ws = new WebSocket(socketUrl());
    ws.onopen = () => {
      retry = 0;
      store().setConn("open");
      // let the backlog land first, then take the authoritative snapshot over it
      window.setTimeout(hydrate, 250);
      ping = window.setInterval(() => ws?.readyState === 1 && ws.send("ping"), 20000);
    };
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as HubEvent;
        store().apply(ev, Date.now() - Date.parse(ev.ts) > REPLAY_AGE_MS);
      } catch (e) {
        console.warn("bad hub event", e);
      }
    };
    ws.onclose = () => {
      window.clearInterval(ping);
      store().setConn("closed");
      if (stopped) return;
      const wait = Math.min(8000, 600 * 2 ** retry++);
      timer = window.setTimeout(connect, wait);
      if (retry === 1) hydrate(); // REST may still be up even if the socket blipped
    };
  };

  connect();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    window.clearInterval(ping);
    ws?.close();
  };
}
