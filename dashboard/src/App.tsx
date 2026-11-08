import { useEffect, useRef, useState, type ReactElement } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map } from "maplibre-gl";
// MapLibre v6 loads its worker from a separate file; the Vite build must ship it, or no GeoJSON layer draws.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Feature, FeatureCollection } from "geojson";
import hillshadeUrl from "../../data/yarnell/hillshade.png";
import perimeterRaw from "../../data/yarnell/perimeter.geojson?raw";
import scenarioRaw from "../../data/yarnell/scenario.geojson?raw";
import metaRaw from "../../data/yarnell/meta.json?raw";
import type { EngineState, Envelope, SensorReading } from "./types";
import { FireLayer } from "./fireLayer";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Corners = [[number, number], [number, number], [number, number], [number, number]];
const corners = JSON.parse(metaRaw).corners_lonlat_tl_tr_br_bl as Corners;
const perimeter = JSON.parse(perimeterRaw) as FeatureCollection;
const scenario = JSON.parse(scenarioRaw) as FeatureCollection;
const descentLine: FeatureCollection = { type: "FeatureCollection", features: scenario.features.filter(f => f.geometry.type === "LineString") };
// zoom to the 2013 crew area (descent line, crew start, ranch), not the whole hillshade
const scenarioBounds = (() => {
  const xs: number[] = [], ys: number[] = [];
  const walk = (c: any): void => { if (typeof c[0] === "number") { xs.push(c[0]); ys.push(c[1]); } else c.forEach(walk); };
  scenario.features.filter(f => f.geometry.type !== "Polygon").forEach(f => walk((f.geometry as any).coordinates));
  return [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]] as [[number, number], [number, number]];
})();
const empty: FeatureCollection = { type: "FeatureCollection", features: [] };
// 2013 places, labelled with HTML markers (MapLibre text needs a glyph server, which FireNet doesn't have)
const placeAt = (name: string) => (scenario.features.find(f => f.properties?.name === name)?.geometry as any)?.coordinates as [number, number] | undefined;
const RANCH = placeAt("Boulder Springs Ranch");
const RANCH_R_M = Number(scenario.features.find(f => f.properties?.name === "Boulder Springs Ranch")?.properties?.radius_m ?? 100);
const circle = ([lon, lat]: [number, number], r: number): FeatureCollection => {
  const k = 111320, ring = Array.from({ length: 65 }, (_, i) => {
    const a = i / 64 * 2 * Math.PI;
    return [lon + r * Math.sin(a) / (k * Math.cos(lat * Math.PI / 180)), lat + r * Math.cos(a) / k];
  });
  return fc([{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } }]);
};
// 16-point compass names for wind directions
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compassName = (deg: number) => COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
const fmt = (v: number | null | undefined, d = 1) => v == null || v < 0 ? "—" : v.toFixed(d);

const socketUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8000/ws`;
// Start / Reset / Ignite -> ws_server POST /api/cmd -> engine/cmd (port 8000 also from the Vite dev server)
const cmdUrl = import.meta.env.VITE_API_URL || `${location.protocol}//${location.hostname}:8000/api/cmd`;
// scenario clock: scenario_start ("16:20") + seconds since Start, as HH:MM:SS
const scenarioClock = (start: string, elapsed: number) => {
  const [h, m] = start.split(":").map(Number), t = h * 3600 + m * 60 + Math.max(0, Math.floor(elapsed));
  return [Math.floor(t / 3600) % 24, Math.floor(t / 60) % 60, t % 60].map(v => String(v).padStart(2, "0")).join(":");
};
const fc = (features: Feature[]): FeatureCollection => ({ type: "FeatureCollection", features });
// crew marker: a person glyph in a disc, drawn as a map image (icon symbols work offline; only text needs a glyph server)
const CREW_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40"><circle cx="20" cy="20" r="18" fill="#4a86c4" stroke="#0e1215" stroke-width="2.5"/><circle cx="20" cy="15.5" r="4.6" fill="#fff"/><path d="M20 21c-5 0-8 3.2-8 7.4 0 .5.3.9.9.9h14.2c.6 0 .9-.4.9-.9 0-4.2-3-7.4-8-7.4z" fill="#fff"/></svg>`;
const point = (lon: number, lat: number, properties: Record<string, unknown> = {}): Feature =>
  ({ type: "Feature", properties, geometry: { type: "Point", coordinates: [lon, lat] } });

type EngineRoute = NonNullable<EngineState["routes"]>[number];
const GREEN_MIN = 5, RED_MIN = 2;   // config.py margin thresholds
const marginClass = (m: number) => m >= GREEN_MIN ? "good" : m >= RED_MIN ? "warn" : "danger";
// index of the path point where fire - crew is smallest (the route's margin), or null if no fire reaches it
function tightestIndex(r: EngineRoute): number | null {
  let best: number | null = null, bestM = Infinity;
  r.path?.forEach((_, i) => {
    const c = r.t_crew_min?.[i], f = r.t_fire_min?.[i];
    if (c != null && f != null && f - c < bestM) { bestM = f - c; best = i; }
  });
  return best;
}
function nearestIndex(path: [number, number][], lon: number, lat: number): number {
  const k = Math.cos(lat * Math.PI / 180);
  let best = 0, bestD = Infinity;
  path.forEach(([x, y], i) => { const d = ((x - lon) * k) ** 2 + (y - lat) ** 2; if (d < bestD) { bestD = d; best = i; } });
  return best;
}
// popup HTML for one route point; clock times only once the scenario runs
function routePointHtml(engine: EngineState, r: EngineRoute, i: number): string {
  const c = r.t_crew_min?.[i] ?? null, f = r.t_fire_min?.[i] ?? null;
  const at = (min: number) => engine.phase === "running" && engine.started_ts != null && engine.ts != null
    ? ` (${scenarioClock(engine.scenario_start ?? "16:20", engine.ts - engine.started_ts + min * 60).slice(0, 5)})` : "";
  const rows = [
    c == null ? "Crew joins this route further on" : `Crew here in <b>${c.toFixed(1)} min</b>${at(c)}`,
    f == null ? "Fire not expected here" : `Fire here in <b>${f.toFixed(1)} min</b>${at(f)}`,
  ];
  if (c != null && f != null) rows.push(`<span class="${marginClass(f - c)}">Margin here <b>${(f - c).toFixed(1)} min</b></span>`);
  return `<div class="route-tip"><strong>${r.name ?? r.id}</strong>${rows.map(x => `<p>${x}</p>`).join("")}`
    + (f != null ? `<small>Fire time includes the 1.5× safety factor</small>` : "") + `</div>`;
}

// engine/state -> one FeatureCollection per map source
function engineLayers(engine: EngineState): Record<string, FeatureCollection> {
  const out: Record<string, FeatureCollection> = {
    routes: fc((engine.routes ?? []).filter(r => r.path?.length).map(r => ({
      type: "Feature", properties: { id: r.id, status: r.status, active: r.active },
      geometry: { type: "LineString", coordinates: r.path! } }))),
    nodes: fc((engine.nodes ?? []).filter(n => n.lat != null && n.lon != null).map(n => point(n.lon!, n.lat!, { id: n.id, status: n.status }))),
    fires: fc((engine.fires ?? []).map(f => point(f.lon, f.lat))),
    crew: fc(engine.crew ? [point(engine.crew.lon, engine.crew.lat)] : []),
    tightest: fc((engine.routes ?? []).flatMap(r => {
      const i = tightestIndex(r);
      return i == null || !r.path ? [] : [point(r.path[i][0], r.path[i][1], { id: r.id, status: r.status })];
    })),
  };
  return out;
}

function addLayers(map: Map) {
  map.addSource("hillshade", { type: "image", url: hillshadeUrl, coordinates: corners });
  map.addLayer({ id: "hillshade", type: "raster", source: "hillshade", paint: { "raster-opacity": .68, "raster-saturation": -.75 } });
  map.addSource("perimeter", { type: "geojson", data: perimeter });
  map.addLayer({ id: "perimeter-line", type: "line", source: "perimeter", paint: { "line-color": "#c76a5e", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": .7 } });
  map.addSource("descent", { type: "geojson", data: descentLine });
  map.addLayer({ id: "descent", type: "line", source: "descent", paint: { "line-color": "#e8eef0", "line-width": 1.5, "line-dasharray": [1, 2], "line-opacity": .8 } });
  map.addSource("routes", { type: "geojson", data: empty });
  map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: {
    "line-color": ["match", ["get", "status"], "green", "#50bb9d", "amber", "#d9a95a", "red", "#cf6a60", "#8c9691"],
    "line-width": ["case", ["get", "active"], 7, 4], "line-opacity": ["case", ["get", "active"], 1, .75] } });
  // invisible wide line: easier hover / tap target for the route tooltip
  map.addLayer({ id: "routes-hit", type: "line", source: "routes", paint: { "line-width": 16, "line-opacity": 0 } });
  map.addSource("tightest", { type: "geojson", data: empty });
  map.addLayer({ id: "tightest", type: "circle", source: "tightest", paint: { "circle-radius": 9, "circle-opacity": 0,
    "circle-stroke-width": 2.5, "circle-stroke-color": ["match", ["get", "status"], "green", "#50bb9d", "amber", "#d9a95a", "red", "#cf6a60", "#8c9691"] } });
  if (RANCH) {   // Boulder Springs Ranch safety zone (100 m)
    map.addSource("ranch-zone", { type: "geojson", data: circle(RANCH, RANCH_R_M) });
    map.addLayer({ id: "ranch-zone", type: "fill", source: "ranch-zone", paint: { "fill-color": "#5e9ed6", "fill-opacity": .12 } }, "routes");
    map.addLayer({ id: "ranch-zone-line", type: "line", source: "ranch-zone", paint: { "line-color": "#5e9ed6", "line-width": 1.5, "line-dasharray": [2, 1.5] } }, "routes");
  }
  map.addSource("fires", { type: "geojson", data: empty });
  map.addLayer({ id: "fires", type: "circle", source: "fires", paint: { "circle-color": "#be4e44", "circle-radius": 7, "circle-stroke-color": "#d9a95a", "circle-stroke-width": 2 } });
  map.addSource("nodes", { type: "geojson", data: empty });
  map.addLayer({ id: "nodes", type: "circle", source: "nodes", paint: {
    "circle-color": ["match", ["get", "status"], "fresh", "#5e9ed6", "aging", "#d9a95a", "#6a7074"], "circle-radius": 7, "circle-stroke-color": "#0e1215", "circle-stroke-width": 3 } });
  const crewIcon = new Image(40, 40);
  crewIcon.onload = () => { if (!map.hasImage("crew")) map.addImage("crew", crewIcon); };
  crewIcon.src = "data:image/svg+xml;base64," + btoa(CREW_SVG);
  map.addSource("crew", { type: "geojson", data: empty });
  map.addLayer({ id: "crew", type: "symbol", source: "crew", layout: { "icon-image": "crew", "icon-size": 0.62, "icon-allow-overlap": true, "icon-anchor": "center" } });
}

// sensor node popup: live reading + health from engine/state
function nodeHtml(id: string, label: string | undefined, status: string, ageS: number | null, r: SensorReading | undefined): string {
  const rows: string[] = [`<span class="node-${status}">${status === "fresh" ? "Live" : status === "aging" ? "Aging" : "Stale: engine uses worst case"}</span>`
    + (ageS != null ? ` · ${ageS}s ago` : " · never heard")];
  if (r) {
    if (r.temp_c > -50) rows.push(`Temperature <b>${fmt(r.temp_c)} °C</b>`);
    if (r.rh >= 0) rows.push(`Humidity <b>${fmt(r.rh, 0)} %</b>`);
    if (r.wind_ms >= 0) rows.push(`Wind <b>${fmt(r.wind_ms)} m/s</b> ` + (r.wind_dir >= 0 ? `from <b>${compassName(r.wind_dir)} (${Math.round(r.wind_dir)}°)</b>` : "direction unknown"));
    rows.push(`<small>seq ${r.seq}</small>`);
  } else rows.push("No reading this session");
  return `<div class="route-tip"><strong>${id}${label && label !== id ? ` · ${label}` : ""}</strong>${rows.map(x => `<p>${x}</p>`).join("")}</div>`;
}

// Minimalist wind dial: a thin ring and a single arrow pointing where the wind pushes the fire (wind "from" + 180)
function WindPanel({ engine }: { engine: EngineState }) {
  const w = engine.weather, from = w?.wind_from, to = from == null ? null : (from + 180) % 360;
  return <section className="panel wind-panel"><div className="panel-heading"><div><h2>Wind and fire spread</h2></div></div>
    <div className="wind-body">
      <svg viewBox="-50 -50 100 100" className="wind-dial" role="img" aria-label={to == null ? "wind direction unknown" : `fire pushed toward ${compassName(to)}`}>
        <circle r="40" className="dial-ring" />
        <circle cx="0" cy="-40" r="1.8" className="dial-n" />
        {to == null
          ? <circle r="6" className="dial-unknown" />
          : <g transform={`rotate(${to})`} className="wind-arrow">
              <path d="M0,-32 L8,-15 L2.5,-15 L2.5,30 L-2.5,30 L-2.5,-15 L-8,-15 Z" />
            </g>}
      </svg>
      <div className="wind-text">
        {to == null ? <><strong className="warn">Direction unknown</strong><p className="muted">A sensor is stale or no wind reading yet; fire spreads in every direction.</p></>
          : <><strong>Fire pushed {compassName(to)}</strong><p>Wind {compassName(from!)} ({Math.round(from!)}°) · {fmt(w?.wind_ms)} m/s</p></>}
        <p className="muted">{fmt(w?.temp_c)} °C · {fmt(w?.rh, 0)} % RH{w?.m1h_pct != null ? ` · FFM ${w.m1h_pct} %` : ""}</p>
      </div>
    </div></section>;
}

function Legend() {
  const [open, setOpen] = useState(false);
  const line = (color: string, dash?: string, width = 4) => <svg width="30" height="10"><line x1="2" y1="5" x2="28" y2="5" stroke={color} strokeWidth={width} strokeDasharray={dash} strokeLinecap="round" /></svg>;
  const fill = (color: string, op = 1, stroke?: string) => <svg width="30" height="12"><rect x="3" y="1" width="24" height="10" rx="2" fill={color} fillOpacity={op} stroke={stroke} /></svg>;
  const dot = (color: string, stroke: string, r = 4.5, sw = 2) => <svg width="30" height="12"><circle cx="15" cy="6" r={r} fill={color} stroke={stroke} strokeWidth={sw} /></svg>;
  const person = <svg width="30" height="12" viewBox="0 0 30 12"><g transform="translate(15 6)" fill="#4a86c4"><circle cy="-3" r="2.2" /><path d="M0 0c-2.4 0-3.7 1.6-3.7 3.6 0 .3.2.4.4.4h6.6c.2 0 .4-.1.4-.4C3.7 1.6 2.4 0 0 0z" /></g></svg>;
  const items: [ReactElement, string][] = [
    [line("#50bb9d", undefined, 7), "Active route (thick)"],
    [line("#50bb9d"), "Safe (margin ≥ 5 min)"],
    [line("#d9a95a"), "Tight (2–5 min)"],
    [line("#cf6a60"), "Cut: fire arrives first"],
    [<svg width="30" height="12"><circle cx="15" cy="6" r="5" fill="none" stroke="#cf6a60" strokeWidth="2" /></svg>, "Tightest point"],
    [line("#e8eef0", "1 4", 2), "2013 crew route"],
    [line("#c76a5e", "6 4", 2), "2013 fire perimeter"],
    [fill("#0b0b0c", .6, "#3a3f44"), "The black (burned)"],
    [fill("#f0783a", .8), "Burning now"],
    [fill("#cf7a52", .36), "Fire within 5 min"],
    [fill("#d9a95a", .23), "Fire in 5–10 min"],
    [fill("#5e9ed6", .15, "#5e9ed6"), "Ranch safety zone"],
    [person, "Crew"],
    [dot("#5e9ed6", "#0e1215"), "Sensor: live"],
    [dot("#d9a95a", "#0e1215"), "Aging"],
    [dot("#6a7074", "#0e1215"), "Stale"],
    [dot("#be4e44", "#d9a95a"), "Spot fire"],
  ];
  return <section className="panel legend"><div className="panel-heading"><div><h2>Map legend</h2></div>
    <button className="legend-toggle" onClick={() => setOpen(o => !o)} aria-expanded={open} aria-label={open ? "Collapse legend" : "Expand legend"}>{open ? "–" : "+"}</button></div>
    {open && <ul>{items.map(([icon, text]) => <li key={text}>{icon}<span>{text}</span></li>)}</ul>}</section>;
}

export default function App() {
  const mapElement = useRef<HTMLDivElement>(null), map = useRef<Map | null>(null);
  const fireLayer = useRef<FireLayer | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [sensors, setSensors] = useState<Record<string, SensorReading>>({});
  const [engine, setEngine] = useState<EngineState>({ type: "state", routes: [], alerts: [] });
  const [engineAt, setEngineAt] = useState<number>();
  const [, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [resetArmed, setResetArmed] = useState(false);
  const [cmdError, setCmdError] = useState<string>();
  const routes = engine.routes ?? [];
  const engineRef = useRef(engine);
  const hover = useRef<{ popup: maplibregl.Popup; id: string; i: number } | null>(null);
  const nodeHover = useRef<{ popup: maplibregl.Popup; id: string } | null>(null);
  const sensorsRef = useRef<Record<string, SensorReading>>({});
  const [bearing, setBearing] = useState(0);

  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);

  useEffect(() => {
    if (!mapElement.current || map.current) return;
    const instance = new maplibregl.Map({ container: mapElement.current, center: [-112.775, 34.22], zoom: 12.7,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#0e1215" } }] } });
    map.current = instance;
    // pad so the floating top bar and left rail don't cover the 2013 scenario area
    instance.on("load", () => { addLayers(instance); fireLayer.current = new FireLayer(instance, "descent"); instance.fitBounds(scenarioBounds, { padding: { top: 150, bottom: 80, left: 400, right: 80 } }); setMapReady(true); });
    // route tooltip: nearest path point to the pointer (hover, or tap on touch)
    const showRoute = (e: maplibregl.MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id as string | undefined;
      const r = engineRef.current.routes?.find(x => x.id === id);
      if (!r?.path?.length) return;
      const i = nearestIndex(r.path, e.lngLat.lng, e.lngLat.lat);
      const popup = hover.current?.popup ?? new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: "lces-popup" });
      popup.setLngLat(r.path[i]).setHTML(routePointHtml(engineRef.current, r, i)).addTo(instance);
      hover.current = { popup, id: r.id, i };
      instance.getCanvas().style.cursor = "pointer";
    };
    instance.on("mousemove", "routes-hit", showRoute);
    instance.on("click", "routes-hit", showRoute);
    instance.on("mouseleave", "routes-hit", () => { hover.current?.popup.remove(); hover.current = null; instance.getCanvas().style.cursor = ""; });
    // sensor node popup (hover, or tap on touch)
    const showNode = (e: maplibregl.MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id as string | undefined;
      if (!id) return;
      const popup = nodeHover.current?.popup ?? new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, className: "lces-popup" });
      nodeHover.current = { popup, id };
      renderNodePopup();
      popup.addTo(instance);
      instance.getCanvas().style.cursor = "pointer";
    };
    instance.on("mouseenter", "nodes", showNode);
    instance.on("click", "nodes", showNode);
    instance.on("mouseleave", "nodes", () => { nodeHover.current?.popup.remove(); nodeHover.current = null; instance.getCanvas().style.cursor = ""; });
    instance.on("rotate", () => setBearing(instance.getBearing()));
    if (RANCH) {
      const el = document.createElement("div");
      el.className = "place ranch";
      el.textContent = "Boulder Springs Ranch";
      new maplibregl.Marker({ element: el, anchor: "bottom", offset: [0, -14] }).setLngLat(RANCH).addTo(instance);
    }
    return () => { fireLayer.current?.destroy(); fireLayer.current = null; instance.remove(); map.current = null; setMapReady(false); };
  }, []);

  // reconnect every 2 s, so the page survives a ws_server restart
  useEffect(() => {
    let socket: WebSocket, retry: number, closed = false;
    const open = () => {
      socket = new WebSocket(import.meta.env.VITE_WS_URL || socketUrl);
      socket.onopen = () => setConnection("live");
      socket.onclose = () => { setConnection("offline"); if (!closed) retry = window.setTimeout(open, 2000); };
      socket.onmessage = ({ data }) => {
        const message = JSON.parse(data) as Envelope;
        if (message.topic.startsWith("sensors/") && (message.data as SensorReading).type === "wx") {
          const reading = message.data as SensorReading;
          setSensors(c => ({ ...c, [reading.node]: reading }));
        }
        if (message.topic === "engine/state" && message.data.type === "state") { setEngine(message.data as EngineState); setEngineAt(message.rx_ts); }
      };
    };
    open();
    return () => { closed = true; clearTimeout(retry); socket.close(); };
  }, []);

  useEffect(() => {
    if (!mapReady || !map.current) return;
    for (const [id, data] of Object.entries(engineLayers(engine))) (map.current.getSource(id) as GeoJSONSource | undefined)?.setData(data);
    fireLayer.current?.update(engine.fire_grid, engine.ts).catch(err => console.error("fire_grid", err));
  }, [engine, mapReady]);

  // node popup content, kept live from the latest engine/state + sensor readings
  function renderNodePopup() {
    const h = nodeHover.current, n = engineRef.current.nodes?.find(x => x.id === h?.id);
    if (!h || !n || n.lat == null || n.lon == null) return;
    h.popup.setLngLat([n.lon, n.lat]).setHTML(nodeHtml(n.id, n.label, n.status, n.age_s, sensorsRef.current[n.id]));
  }
  useEffect(() => { sensorsRef.current = sensors; renderNodePopup(); }, [sensors]);

  // keep an open route tooltip live as engine/state updates every second
  useEffect(() => {
    engineRef.current = engine;
    renderNodePopup();
    const h = hover.current, r = engine.routes?.find(x => x.id === h?.id);
    if (!h) return;
    if (!r?.path?.length) { h.popup.remove(); hover.current = null; return; }
    const i = Math.min(h.i, r.path.length - 1);
    h.popup.setLngLat(r.path[i]).setHTML(routePointHtml(engine, r, i));
  }, [engine]);

  useEffect(() => { if (!resetArmed) return; const t = window.setTimeout(() => setResetArmed(false), 3000); return () => clearTimeout(t); }, [resetArmed]);

  const sendCmd = async (body: Record<string, unknown>) => {
    setCmdError(undefined);
    try {
      const res = await fetch(cmdUrl, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) setCmdError(`${body.cmd}: ${(await res.json().catch(() => ({}))).detail ?? res.status}`);
    } catch { setCmdError(`${body.cmd}: bridge unreachable`); }
  };
  const onReset = () => { if (resetArmed) { setResetArmed(false); sendCmd({ cmd: "reset" }); } else setResetArmed(true); };
  const phase = engineAt != null ? engine.phase : undefined;
  const clock = phase === "running" && engine.started_ts != null
    ? scenarioClock(engine.scenario_start ?? "16:20", (engine.ts ?? now) - engine.started_ts) : null;

  const nodes = engine.nodes ?? [];
  const liveCount = nodes.filter(n => n.status === "fresh" || n.status === "aging").length;
  // N2 already burning (engine merges fires within FIRE_MERGE_M = 30 m): a second Ignite would do nothing
  const n2 = nodes.find(n => n.id === "N2");
  const n2Lit = n2?.lat != null && n2?.lon != null && (engine.fires ?? []).some(f =>
    Math.hypot((f.lat - n2.lat!) * 111320, (f.lon - n2.lon!) * 111320 * Math.cos(n2.lat! * Math.PI / 180)) < 30);
  const active = routes.find(r => r.active);
  const crewNote = engine.crew?.arrived ? "Crew in the safety zone" : engine.crew?.walking ? "Crew walking this route" : phase === "ready" ? "Press play to begin" : "";
  const statusWord = active ? ({ green: "Clear", amber: "Tightening", red: "Route cut" } as Record<string, string>)[active.status] ?? "—" : "No active route";
  const leaveBy = engine.leave_by_ts
    ? (clock && engine.started_ts != null && engine.ts != null
        ? scenarioClock(engine.scenario_start ?? "16:20", engine.leave_by_ts - engine.started_ts).slice(0, 5)
        : new Date(engine.leave_by_ts * 1000).toLocaleTimeString().slice(0, 5))
    : "—";
  return <main className="console">
    <div ref={mapElement} className="map" />

    <div className="topbar">
      <div className="topbar-left">{clock && <div className="clock"><span>SCENARIO</span><b>{clock}</b></div>}</div>
      <div className="topbar-right">
        <div className="controls">
          <button className="icon-btn start" disabled={phase !== "ready"} onClick={() => sendCmd({ cmd: "start" })} title={phase === "running" ? "Scenario running" : "Start scenario"} aria-label="Start scenario">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z" fill="currentColor" /></svg>
          </button>
          {phase === "running" && <button className="icon-btn ignite" disabled={n2Lit} onClick={() => sendCmd({ cmd: "fire", node: "N2" })} title={n2Lit ? "Spot fire at N2 already burning (Reset to clear)" : "Stage backup: spot fire at sensor node N2"} aria-label="Ignite at N2">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3c2.8 3 4.3 5.4 4.3 7.9A4.3 4.3 0 0 1 7.7 11c0-.8.3-1.5.3-1.5s1.1 1 2.1 1c0-2.4 1-4.3 1.9-7.5z" fill="currentColor" /></svg>
          </button>}
          <button className={`icon-btn reset ${resetArmed ? "armed" : ""}`} onClick={onReset} title={resetArmed ? "Confirm reset" : "Reset scenario"} aria-label={resetArmed ? "Confirm reset" : "Reset scenario"}>
            {resetArmed
              ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 2.6-6.3M3 4.5v4h4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>}
          </button>
        </div>
        <div className={`connection ${liveCount ? "live" : "offline"}`}><i />{liveCount}/{nodes.length || 3} sensors</div>
      </div>
    </div>
    {cmdError && <div className="cmd-error">{cmdError}</div>}

    <aside className="rail">
      <section className={`status-banner ${active?.status ?? "none"}`}>
        <div className="sb-head">
          <span className="sb-label">ACTIVE ESCAPE ROUTE</span>
          <span className="sb-state"><i />{statusWord}</span>
        </div>
        <p className="sb-note">{crewNote || "Awaiting engine assessment"}</p>
        <div className="sb-metrics">
          <div><span>MARGIN</span><b>{active && active.margin_min != null ? active.margin_min.toFixed(1) : "—"}</b><small>{active?.margin_min == null ? "no fire threat" : "min · worst point"}</small></div>
          <div><span>CREW ETA</span><b>{active?.eta_min != null ? active.eta_min.toFixed(1) : "—"}</b><small>min to zone</small></div>
          <div><span>LEAVE BY</span><b>{leaveBy}</b><small>{engine.leave_by_ts ? "scenario time" : "no deadline"}</small></div>
        </div>
      </section>
      <WindPanel engine={engine} />
      {(engine.alerts?.length || engine.weather?.notes?.length)
        ? <section className="panel alert"><div className="panel-heading"><div><h2>Operational alerts</h2></div></div>
            {engine.alerts?.map(alert => {
              const sev = /no safe|red|cut/i.test(alert) ? "danger" : "warn";
              return <div className={`alert-row ${sev}`} key={alert}>
                <span className="alert-badge"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 1.5 21h21L12 3z" fill="currentColor" fillOpacity=".22" /><path d="M12 3 1.5 21h21L12 3z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M12 10v5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><circle cx="12" cy="17.6" r="1.1" fill="currentColor" /></svg></span>
                <p>{alert}</p>
              </div>;
            })}
            {engine.weather?.notes?.length ? <p className="weather-note">Fallback weather: {engine.weather.notes.join(" · ")}</p> : null}
          </section>
        : null}
      <Legend />
    </aside>

    <div className="compass" title="North"><div style={{ transform: `rotate(${-bearing}deg)` }}><svg viewBox="-24 -24 48 48"><circle r="22" className="compass-ring" /><path d="M0,-17 L6,2 L0,-2 L-6,2 Z" className="compass-n" /><path d="M0,17 L6,-2 L0,2 L-6,-2 Z" className="compass-s" /><text y="-4" dy="-14" className="compass-label">N</text></svg></div></div>
    <div className="map-note">Hover a route or a sensor node for live times and readings</div>
  </main>;
}
