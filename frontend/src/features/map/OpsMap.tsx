import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap, Marker } from "maplibre-gl";
// MapLibre v6 derives its worker URL from its own import.meta.url, which Vite's pre-bundling relocates.
// Let Vite bundle the worker itself and hand MapLibre the resulting URL (works in dev and in builds).
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { fx, incidentInScope, resourceInScope, useLive, type Fx } from "@/store/live";
import { RESOURCE, SEVERITY_COLOR, UNIT_STATUS_COLOR } from "@/lib/taxonomy";
import type { Assignment, Incident, Resource } from "@/lib/types";
import { gsap, prefersReducedMotion } from "@/lib/motion";

maplibregl.setWorkerUrl(workerUrl);

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const UNIT_TICK_S = 2; // background_workers.TICK_SECONDS — tween exactly one tick so units glide, never jump
const LIVE_LINK = new Set(["pending_approval", "dispatched", "en_route"]);

const FAC_ICON: Record<string, string> = {
  hospital: '<svg viewBox="0 0 12 12"><path d="M4.5 1h3v3.5H11v3H7.5V11h-3V7.5H1v-3h3.5z" fill="currentColor"/></svg>',
  fire_station: '<svg viewBox="0 0 12 12"><path d="M6 1c2 2.2 3.5 3.8 3.5 6A3.5 3.5 0 0 1 2.5 7C2.5 5.6 3.3 4.6 4 4c0 1 .5 1.7 1.2 2C5.2 4.4 5.4 2.6 6 1z" fill="currentColor"/></svg>',
  police_station: '<svg viewBox="0 0 12 12"><path d="M6 1l4.5 1.6v3C10.5 8.3 8.6 10.3 6 11 3.4 10.3 1.5 8.3 1.5 5.6v-3z" fill="currentColor"/></svg>',
};
const DEFAULT_FAC = '<svg viewBox="0 0 12 12"><rect x="2.5" y="2.5" width="7" height="7" rx="1.5" fill="currentColor"/></svg>';

/** Warm the CARTO dark basemap into the Dhvani palette: carbon land, ink water, whisper roads. */
function tint(map: MLMap) {
  for (const layer of map.getStyle().layers ?? []) {
    const id = layer.id;
    try {
      if (layer.type === "background") map.setPaintProperty(id, "background-color", "#0a0b0d");
      else if (layer.type === "fill" && /water/.test(id)) map.setPaintProperty(id, "fill-color", "#0c1014");
      else if (layer.type === "fill" && /building/.test(id)) map.setPaintProperty(id, "fill-color", "#15171a");
      else if (layer.type === "fill" && /park|landcover|landuse|wood|grass/.test(id)) map.setPaintProperty(id, "fill-color", "#0d0f0e");
      else if (layer.type === "line" && /motorway|trunk/.test(id)) map.setPaintProperty(id, "line-color", "#3a2c24");
      else if (layer.type === "line" && /road|street|path|highway|bridge|tunnel|rail/.test(id)) map.setPaintProperty(id, "line-color", "#202326");
      else if (layer.type === "line" && /water|river/.test(id)) map.setPaintProperty(id, "line-color", "#0c1014");
      else if (layer.type === "symbol") {
        map.setPaintProperty(id, "text-color", /place|city|town|suburb|neighbourhood/.test(id) ? "#8d887d" : "#5b5850");
        map.setPaintProperty(id, "text-halo-color", "#0a0b0d");
      }
    } catch { /* layer lacks that paint property — fine */ }
  }
}

function metersToPixels(map: MLMap, lat: number, meters: number) {
  const mpp = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** map.getZoom();
  return meters / mpp;
}

const incLevel = (i: Incident) => i.severity_level ?? "none";

export function OpsMap() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reduce = prefersReducedMotion();
    const { center } = useLive.getState();
    const map = new maplibregl.Map({
      container: host.current!,
      style: STYLE,
      center: [center.lng, center.lat],
      zoom: reduce ? 11.6 : 9.2,
      pitch: reduce ? 38 : 0,
      bearing: reduce ? -12 : 0,
      attributionControl: { compact: true },
      maxPitch: 60,
      fadeDuration: 0,
    });
    map.on("error", (e) => console.warn("[map]", e.error?.message ?? e));
    if (import.meta.env.DEV) (window as unknown as { __map: MLMap }).__map = map;
    const incM = new Map<string, Marker>();
    const unitM = new Map<string, { m: Marker; p: { lng: number; lat: number } }>();
    const facM = new Map<string, Marker>();
    let ready = false;
    let dashTimer: number | undefined;

    // ── incidents ──
    const syncIncidents = (incidents: Record<string, Incident>, selectedId: string | null) => {
      const { lens, taxonomy } = useLive.getState();
      const visible = (i: Incident) => i.lat != null && i.lng != null && incidentInScope(i, lens, taxonomy);
      for (const [id, m] of incM) {
        const i = incidents[id];
        if (!i || !visible(i)) { m.remove(); incM.delete(id); }
      }
      for (const i of Object.values(incidents)) {
        if (!visible(i)) continue;
        const at: [number, number] = [i.lng as number, i.lat as number];
        let m: Marker | undefined = incM.get(i.id);
        if (!m) {
          const el = document.createElement("button");
          el.className = "m-inc";
          el.setAttribute("aria-label", `Incident ${i.incident_number}`);
          el.innerHTML = '<span class="ring"></span><span class="ring"></span><span class="core"></span><span class="tag"></span>';
          el.addEventListener("click", (e) => { e.stopPropagation(); useLive.getState().select(i.id); });
          const made = new maplibregl.Marker({ element: el }).setLngLat(at).addTo(map);
          incM.set(i.id, made);
          m = made;
          if (!reduce) gsap.from(el.querySelector(".core"), { scale: 0, duration: 0.6, ease: "back.out(3)" });
        } else {
          m.setLngLat(at);
        }
        const el = m.getElement();
        const lvl = incLevel(i);
        el.style.setProperty("--c", SEVERITY_COLOR[lvl]);
        el.dataset.level = lvl;
        el.dataset.dim = String(!["open", "dispatched", "escalated", "needs_review"].includes(i.status));
        el.dataset.selected = String(i.id === selectedId);
        el.style.zIndex = String(i.id === selectedId ? 5 : lvl === "critical" ? 4 : 3);
        (el.querySelector(".tag") as HTMLElement).textContent =
          `#${i.incident_number}${i.report_count > 1 ? ` ×${i.report_count}` : ""}`;
      }
    };

    // ── units ──
    const syncUnits = (resources: Record<string, Resource>) => {
      const { lens, taxonomy } = useLive.getState();
      for (const [id, u] of unitM) {
        const r = resources[id];
        if (!r || !resourceInScope(r, lens, taxonomy)) { gsap.killTweensOf(u.p); u.m.remove(); unitM.delete(id); }
      }
      for (const r of Object.values(resources)) {
        if (!resourceInScope(r, lens, taxonomy)) continue;
        let u = unitM.get(r.id);
        if (!u) {
          const el = document.createElement("div");
          el.className = "m-unit";
          el.textContent = RESOURCE[r.type]?.code ?? r.type.slice(0, 3).toUpperCase();
          el.title = `${r.callsign} · ${RESOURCE[r.type]?.label ?? r.type}`;
          const p = { lng: r.lng, lat: r.lat };
          u = { m: new maplibregl.Marker({ element: el }).setLngLat([r.lng, r.lat]).addTo(map), p };
          unitM.set(r.id, u);
        } else if (u.p.lng !== r.lng || u.p.lat !== r.lat) {
          const { m, p } = u;
          gsap.killTweensOf(p);
          if (reduce) { p.lng = r.lng; p.lat = r.lat; m.setLngLat([r.lng, r.lat]); }
          else gsap.to(p, { lng: r.lng, lat: r.lat, duration: UNIT_TICK_S, ease: "none",
                            onUpdate: () => { m.setLngLat([p.lng, p.lat]); refreshLinks(); } });
        }
        const el = u.m.getElement();
        el.style.setProperty("--c", UNIT_STATUS_COLOR[r.status] ?? "var(--color-bone-faint)");
        el.dataset.status = r.status;
      }
    };

    // ── facilities ──
    const syncFacilities = () => {
      for (const f of useLive.getState().facilities) {
        if (facM.has(f.id)) continue;
        const el = document.createElement("div");
        el.className = "m-fac";
        el.title = f.name;
        el.innerHTML = FAC_ICON[f.type] ?? DEFAULT_FAC;
        facM.set(f.id, new maplibregl.Marker({ element: el }).setLngLat([f.lng, f.lat]).addTo(map));
      }
    };

    // ── dispatch links: unit → incident, updated as units glide (coalesced to one setData per frame) ──
    let linkRaf = 0;
    const refreshLinks = () => {
      if (!ready || linkRaf) return;
      linkRaf = requestAnimationFrame(() => { linkRaf = 0; writeLinks(); });
    };
    const writeLinks = () => {
      const { assignments, incidents } = useLive.getState();
      const features = Object.values(assignments)
        .filter((a: Assignment) => LIVE_LINK.has(a.status))
        .flatMap((a) => {
          const i = incidents[a.incident_id];
          const u = unitM.get(a.resource_id);
          if (!i || i.lat == null || !u) return [];
          return [{ type: "Feature" as const, properties: { pending: a.status === "pending_approval" },
                    geometry: { type: "LineString" as const, coordinates: [[u.p.lng, u.p.lat], [i.lng!, i.lat]] } }];
        });
      (map.getSource("links") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features });
    };

    // ── transient effects ──
    const playFx = (e: Fx) => {
      if (e.kind === "fly") {
        const opts = { center: [e.lng, e.lat] as [number, number], zoom: e.zoom ?? 13.4, pitch: 48, bearing: -14 };
        reduce ? map.jumpTo(opts) : map.flyTo({ ...opts, speed: 0.9, curve: 1.5, essential: true });
        return;
      }
      if (e.kind === "sweep") {
        const px = Math.min(900, Math.max(60, metersToPixels(map, e.lat, e.radiusKm * 1000) * 2));
        const el = document.createElement("div");
        el.className = "m-sweep";
        el.style.width = el.style.height = `${px}px`;
        el.innerHTML = '<div class="disc"></div><div class="arm"></div>';
        const mk = new maplibregl.Marker({ element: el, pitchAlignment: "map", rotationAlignment: "map" })
          .setLngLat([e.lng, e.lat]).addTo(map);
        const rays = e.rays.map((r) => ({ type: "Feature" as const, properties: { d: r.decision },
          geometry: { type: "LineString" as const, coordinates: [[e.lng, e.lat], [r.lng, r.lat]] } }));
        (map.getSource("rays") as GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: rays });
        const o = { v: 0 };
        const setRay = () => map.getLayer("rays") && map.setPaintProperty("rays", "line-opacity", o.v);
        const tl = gsap.timeline({ onComplete: () => mk.remove() });
        tl.fromTo(el.querySelector(".disc"), { scale: 0.05, opacity: 0 }, { scale: 1, opacity: 1, duration: reduce ? 0 : 0.7, ease: "expo.out" })
          .fromTo(el.querySelector(".arm"), { rotate: 0 }, { rotate: 540, duration: reduce ? 0 : 1.8, ease: "power2.inOut" }, 0)
          .to(o, { v: 1, duration: 0.4, onUpdate: setRay }, 0.5)
          .to(el, { opacity: 0, duration: 0.5, ease: "power2.in" }, 2.4)
          .to(o, { v: 0, duration: 0.8, onUpdate: setRay }, 3.6);
        return;
      }
      if (e.kind === "places") {
        const hot: HTMLElement[] = [];
        for (const p of e.points.slice(0, 5)) {
          const el = document.createElement("div");
          el.className = "pointer-events-none";
          el.innerHTML = `<div style="display:flex;align-items:center;gap:6px;transform:translateY(-18px);font:500 10px/1 var(--font-mono);color:${p.best ? "var(--color-sage)" : "var(--color-bone-dim)"};background:rgb(11 12 14 / .85);border:1px solid ${p.best ? "rgb(156 197 161 / .5)" : "var(--color-line)"};padding:5px 7px;border-radius:7px;white-space:nowrap">${p.best ? "◆ " : ""}${p.name.slice(0, 34)}</div>`;
          const mk = new maplibregl.Marker({ element: el }).setLngLat([p.lng, p.lat]).addTo(map);
          hot.push(el);
          gsap.fromTo(el, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: reduce ? 0 : 0.35, ease: "power3.out", delay: hot.length * 0.06 });
          gsap.to(el, { opacity: 0, duration: 0.4, delay: 5, onComplete: () => mk.remove() });
        }
      }
    };

    // Keep camera targets centred in the gap between the floating panels, not under them.
    const pad = () => map.setPadding(window.innerWidth > 1180
      ? { left: window.innerWidth > 1440 ? 400 : 356, right: window.innerWidth > 1440 ? 440 : 396, top: 110, bottom: 110 }
      : { left: 0, right: 0, top: 0, bottom: 0 });
    pad();
    window.addEventListener("resize", pad);

    map.on("load", () => {
      tint(map);
      map.addSource("links", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "links-glow", type: "line", source: "links",
        paint: { "line-color": ["case", ["get", "pending"], "#f6c453", "#ff8a3d"], "line-width": 8, "line-opacity": 0.14, "line-blur": 6 } });
      map.addLayer({ id: "links", type: "line", source: "links", layout: { "line-cap": "round" },
        paint: { "line-color": ["case", ["get", "pending"], "#f6c453", "#ff8a3d"], "line-width": 1.6, "line-dasharray": [0, 2, 3] } });
      map.addSource("rays", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "rays", type: "line", source: "rays",
        paint: { "line-color": ["match", ["get", "d"], "selected", "#8ec5ff", "candidate", "#5f7c99", "#3a3d42"],
                 "line-width": ["match", ["get", "d"], "selected", 2, 1], "line-opacity": 0,
                 "line-dasharray": ["match", ["get", "d"], "selected", ["literal", [1, 0]], ["literal", [2, 2]]] } });
      ready = true;
      refreshLinks();

      // Marching ants on dispatch links — a status loop, so linear time; ~12 fps is plenty for dashes.
      if (!reduce) {
        const seqs = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
        let step = 0;
        dashTimer = window.setInterval(() => {
          if (document.hidden || !map.getLayer("links")) return;
          step = (step + 1) % seqs.length;
          map.setPaintProperty("links", "line-dasharray", seqs[step]);
        }, 80);
      }

      if (!reduce) map.flyTo({ center: [center.lng, center.lat], zoom: 11.7, pitch: 42, bearing: -12, duration: 2600, curve: 1.2, essential: true });
    });

    // Imperative subscriptions: marker work never goes through React render.
    const st = useLive.getState();
    syncIncidents(st.incidents, st.selectedId);
    syncUnits(st.resources);
    syncFacilities();
    const unsub = useLive.subscribe((s, prev) => {
      if (s.incidents !== prev.incidents || s.selectedId !== prev.selectedId) syncIncidents(s.incidents, s.selectedId);
      if (s.resources !== prev.resources) syncUnits(s.resources);
      if (s.facilities !== prev.facilities) syncFacilities();
      if (s.assignments !== prev.assignments || s.incidents !== prev.incidents) refreshLinks();
      if (s.lens !== prev.lens || s.taxonomy !== prev.taxonomy) {
        syncIncidents(s.incidents, s.selectedId);   // a lens change adds or removes markers wholesale
        syncUnits(s.resources);
        refreshLinks();
      }
      if (s.selectedId && s.selectedId !== prev.selectedId) {
        const i = s.incidents[s.selectedId];
        if (i?.lat != null) playFx({ kind: "fly", lat: i.lat, lng: i.lng!, zoom: 14.2 });
      }
    });
    const offFx = fx.on(playFx);
    map.on("click", () => useLive.getState().select(null));

    return () => {
      unsub();
      offFx();
      window.clearInterval(dashTimer);
      window.removeEventListener("resize", pad);
      cancelAnimationFrame(linkRaf);
      unitM.forEach((u) => gsap.killTweensOf(u.p));
      map.remove();
    };
  }, []);

  return (
    <div className="absolute inset-0">
      <div ref={host} style={{ position: "absolute", inset: 0 }} aria-label="Operations map of Ahmedabad" role="region" />
      {/* vignette keeps floating panels legible without dimming the centre of the map */}
      <div className="pointer-events-none absolute inset-0"
           style={{ background: "radial-gradient(120% 90% at 50% 45%, transparent 45%, rgb(7 8 10 / 0.75) 100%)" }} />
    </div>
  );
}
