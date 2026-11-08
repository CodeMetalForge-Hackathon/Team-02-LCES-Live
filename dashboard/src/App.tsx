import { useEffect, useRef, useState, type ReactElement } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map } from "maplibre-gl";
// MapLibre v6 loads its worker from a separate file; the Vite build must ship it, or no GeoJSON layer draws.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Feature, FeatureCollection } from "geojson";
// backdrop: 12 km x 12 km display layers (render_map_layers.py); the engine works on the 5 km crop inside it
import terrainUrl from "../../data/yarnell/wide/terrain.png";
import fuelUrl from "../../data/yarnell/wide/fuel.png";
import slopeUrl from "../../data/yarnell/wide/slope.png";
import hillshadeUrl from "../../data/yarnell/wide/hillshade.png";
import osmRaw from "../../data/yarnell/wide/map_osm.geojson?raw";
import wideMetaRaw from "../../data/yarnell/wide/meta.json?raw";
import perimeterRaw from "../../data/yarnell/perimeter.geojson?raw";
import scenarioRaw from "../../data/yarnell/scenario.geojson?raw";
import metaRaw from "../../data/yarnell/meta.json?raw";
import type { EngineState, Envelope, SensorReading } from "./types";
import { FireLayer } from "./fireLayer";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Corners = [[number, number], [number, number], [number, number], [number, number]];
const corners = JSON.parse(wideMetaRaw).corners_lonlat_tl_tr_br_bl as Corners;
// keep the view on the backdrop (no panning into the empty background)
// first view: the 5 km area the engine models (meta.json), so the setting reads before zooming in
const engineCorners = JSON.parse(metaRaw).corners_lonlat_tl_tr_br_bl as Corners;
const engineBounds = [[Math.min(...engineCorners.map(c => c[0])), Math.min(...engineCorners.map(c => c[1]))],
                      [Math.max(...engineCorners.map(c => c[0])), Math.max(...engineCorners.map(c => c[1]))]] as [[number, number], [number, number]];
const wideBounds = [[Math.min(...corners.map(c => c[0])), Math.min(...corners.map(c => c[1]))],
                    [Math.max(...corners.map(c => c[0])), Math.max(...corners.map(c => c[1]))]] as [[number, number], [number, number]];
const perimeter = JSON.parse(perimeterRaw) as FeatureCollection;
const osm = JSON.parse(osmRaw) as FeatureCollection;   // roads, tracks, buildings (render_map_layers.py)
const scenario = JSON.parse(scenarioRaw) as FeatureCollection;
const descentLine: FeatureCollection = { type: "FeatureCollection", features: scenario.features.filter(f => f.geometry.type === "LineString") };
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
const STATUS_COLOR: Record<string, string> = { green: "#50bb9d", amber: "#d9a95a", red: "#cf6a60", grey: "#8c9691" };
const CLEAR = "rgba(0,0,0,0)";
type OldRoute = { path: [number, number][]; status: string };
type Anim = { raf?: number; timers: number[]; pulse?: maplibregl.Marker };
const lineFc = (path: [number, number][], properties: Record<string, unknown> = {}) =>
  fc([{ type: "Feature", properties, geometry: { type: "LineString", coordinates: path } }]);

function stopReplan(m: Map, a: Anim) {
  if (a.raf) cancelAnimationFrame(a.raf);
  a.timers.forEach(clearTimeout);
  a.timers = [];
  a.pulse?.remove();
  m.setFilter("routes", null);
  (m.getSource("route-draw") as GeoJSONSource).setData(empty);
  (m.getSource("route-ghost") as GeoJSONSource).setData(empty);
}

// Re-plan animation (ticket 11): pulse from the crew, the new route drawn from the crew to the safety zone,
// the old path dashed for a few seconds. No banner: the alert list says what happened.
function playReplan(m: Map, a: Anim, r: EngineRoute, old: OldRoute | undefined, crew: EngineState["crew"]) {
  if (!r.path || r.path.length < 2) return;
  stopReplan(m, a);
  const color = STATUS_COLOR[r.status] ?? STATUS_COLOR.grey, DELAY_MS = 600, DRAW_MS = 1500;
  if (crew) {
    const el = document.createElement("div");
    el.className = "replan-pulse";
    a.pulse = new maplibregl.Marker({ element: el }).setLngLat([crew.lon, crew.lat]).addTo(m);
  }
  if (old?.path.length) (m.getSource("route-ghost") as GeoJSONSource).setData(lineFc(old.path, { color: STATUS_COLOR[old.status] ?? STATUS_COLOR.grey }));
  m.setFilter("routes", ["!=", ["get", "id"], r.id]);
  (m.getSource("route-draw") as GeoJSONSource).setData(lineFc(r.path));
  const t0 = performance.now();
  const frame = (t: number) => {
    const p = Math.min(Math.max((t - t0 - DELAY_MS) / DRAW_MS, 0), 1);
    m.setPaintProperty("route-draw", "line-gradient", ["step", ["line-progress"], color, Math.max(p, 1e-4), p >= 1 ? color : CLEAR]);
    if (p < 1) { a.raf = requestAnimationFrame(frame); return; }
    a.raf = undefined;
    a.pulse?.remove();
    m.setFilter("routes", null);
    (m.getSource("route-draw") as GeoJSONSource).setData(empty);
    a.timers.push(window.setTimeout(() => (m.getSource("route-ghost") as GeoJSONSource).setData(empty), 5000));
  };
  a.raf = requestAnimationFrame(frame);
}
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
// metres along a lon/lat path, cumulative per point (equirectangular: fine over a few km)
function pathMetres(path: [number, number][]): number[] {
  const out = [0];
  for (let i = 1; i < path.length; i++) {
    const [x0, y0] = path[i - 1], [x1, y1] = path[i], k = Math.cos((y0 + y1) / 2 * Math.PI / 180);
    out.push(out[i - 1] + Math.hypot((x1 - x0) * k, y1 - y0) * 111320);
  }
  return out;
}
const fmtDist = (m: number) => m < 1000 ? `${Math.round(m / 10) * 10} m` : `${(m / 1000).toFixed(1)} km`;
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
    c == null ? "Crew joins this route further on" : `Crew here in <b>${c.toFixed(1)} min</b>${at(c)} · ${fmtDist(pathMetres(r.path!)[i])}`,
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
  // backdrop (ticket 08): coloured relief from dem.tif, fuel overlay (toggle), roads and buildings from OSM
  map.addSource("terrain", { type: "image", url: terrainUrl, coordinates: corners });
  map.addLayer({ id: "terrain", type: "raster", source: "terrain", paint: { "raster-opacity": 1, "raster-fade-duration": 0 } });
  map.addSource("slope", { type: "image", url: slopeUrl, coordinates: corners });
  map.addLayer({ id: "slope", type: "raster", source: "slope", layout: { visibility: "none" }, paint: { "raster-fade-duration": 0 } });
  map.addSource("hillshade", { type: "image", url: hillshadeUrl, coordinates: corners });
  map.addLayer({ id: "hillshade", type: "raster", source: "hillshade", layout: { visibility: "none" }, paint: { "raster-opacity": .8, "raster-fade-duration": 0 } });
  map.addSource("fuel", { type: "image", url: fuelUrl, coordinates: corners });
  map.addLayer({ id: "fuel", type: "raster", source: "fuel", layout: { visibility: "none" }, paint: { "raster-opacity": .85, "raster-fade-duration": 0 } });
  map.addSource("osm", { type: "geojson", data: osm });
  map.addLayer({ id: "osm-buildings", type: "fill", source: "osm", filter: ["==", ["get", "kind"], "building"], paint: { "fill-color": "#5b4f43", "fill-opacity": .8 } });
  map.addLayer({ id: "osm-tracks", type: "line", source: "osm", filter: ["==", ["get", "kind"], "track"], paint: { "line-color": "#f4ead8", "line-width": 1, "line-opacity": .7, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "osm-roads", type: "line", source: "osm", filter: ["==", ["get", "kind"], "road"], paint: { "line-color": "#fbf6ec", "line-width": ["interpolate", ["linear"], ["zoom"], 12, .8, 16, 2.5], "line-opacity": .85 } });
  map.addSource("perimeter", { type: "geojson", data: perimeter });
  map.addLayer({ id: "perimeter-line", type: "line", source: "perimeter", paint: { "line-color": "#c76a5e", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": .7 } });
  map.addSource("descent", { type: "geojson", data: descentLine });
  map.addLayer({ id: "descent", type: "line", source: "descent", paint: { "line-color": "#e8eef0", "line-width": 1.5, "line-dasharray": [1, 2], "line-opacity": .8 } });
  map.addSource("routes", { type: "geojson", data: empty });
  map.addLayer({ id: "routes-casing", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: {
    "line-color": "#14181b", "line-width": ["case", ["get", "active"], 10, 6.5], "line-opacity": .7 } });
  map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: {
    "line-color": ["match", ["get", "status"], "green", "#50bb9d", "amber", "#d9a95a", "red", "#cf6a60", "#8c9691"],
    "line-width": ["case", ["get", "active"], 7, 4], "line-opacity": ["case", ["get", "active"], 1, .75] } });
  // re-plan animation (playReplan): the old path dashed, the new one drawn from the crew outward
  map.addSource("route-ghost", { type: "geojson", data: empty });
  map.addLayer({ id: "route-ghost", type: "line", source: "route-ghost", paint: { "line-color": ["get", "color"], "line-width": 4, "line-dasharray": [1.5, 1.5], "line-opacity": .85 } });
  map.addSource("route-draw", { type: "geojson", data: empty, lineMetrics: true });
  map.addLayer({ id: "route-draw", type: "line", source: "route-draw", layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-width": 7, "line-gradient": ["step", ["line-progress"], CLEAR, 1, CLEAR] } });
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

type BaseLayer = "terrain" | "slope" | "hillshade";
const BASE_LAYERS: [BaseLayer, string][] = [["terrain", "Terrain (elevation)"], ["slope", "Slope"], ["hillshade", "Hillshade (grey)"]];
// slope.png colours (render_map_layers.py SLOPE_RAMP)
const SLOPE_KEY: [string, string][] = [["rgb(88,150,96)", "Flat (0°)"], ["rgb(186,196,92)", "10°"], ["rgb(232,188,76)", "20°"],
  ["rgb(226,128,60)", "30°"], ["rgb(178,52,48)", "45°+ (steep: fire runs uphill fast)"]];
// fuel.png colours (render_map_layers.py FUEL_RGBA)
const FUEL_KEY: [string, string][] = [["rgb(236,214,110)", "Grass"], ["rgb(204,178,98)", "Grass-shrub"], ["rgb(128,146,58)", "Shrub"],
  ["rgb(86,104,40)", "Old chaparral (SH5, 2013 burn area)"], ["rgb(62,122,66)", "Timber"]];

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
    [line("#cf6a60", "3 3"), "Previous path (just re-planned)"],
    [line("#e8eef0", "1 4", 2), "2013 crew route"],
    [line("#c76a5e", "6 4", 2), "2013 fire perimeter"],
    [fill("#0b0b0c", .6, "#3a3f44"), "The black (burned)"],
    [fill("#f0783a", .8), "Burning now"],
    [fill("#cf7a52", .36), "Fire within 5 min"],
    [fill("#d9a95a", .23), "Fire in 5–10 min"],
    [fill("#5e9ed6", .15, "#5e9ed6"), "Ranch safety zone"],
    [line("#fbf6ec", undefined, 2), "Road"],
    [line("#f4ead8", "3 2", 1.5), "Track"],
    [fill("#5b4f43", .8), "Building"],
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
  // map layers (ticket 08): one base raster + overlays
  const [base, setBase] = useState<BaseLayer>("terrain");
  const [overlays, setOverlays] = useState({ fuel: false, osm: true, perimeter: true });
  const [layersOpen, setLayersOpen] = useState(false);
  const anim = useRef<Anim>({ timers: [] });
  // what the last engine/state said, to spot a new plan (Start, reroute) or a route the fire bent (detour)
  const seen = useRef<{ replan: number | null | undefined; active?: string; replanned: Record<string, number | null | undefined>; paths: Record<string, OldRoute> } | null>(null);

  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);

  useEffect(() => {
    if (!mapElement.current || map.current) return;
    const instance = new maplibregl.Map({ container: mapElement.current, center: [-112.775, 34.22], zoom: 12.7, maxBounds: wideBounds, minZoom: 11.5,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#0e1215" } }] } });
    map.current = instance;
    // pad so the floating top bar and left rail don't cover the 2013 scenario area
    instance.on("load", () => { addLayers(instance); fireLayer.current = new FireLayer(instance, "descent"); instance.fitBounds(engineBounds, { padding: { top: 70, bottom: 20, left: 390, right: 20 } }); setMapReady(true); });
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

  useEffect(() => {
    if (!mapReady || !map.current || engineAt == null) return;
    const prev = seen.current, rs = engine.routes ?? [], active = rs.find(r => r.active);
    seen.current = { replan: engine.replan_ts, active: active?.id, replanned: Object.fromEntries(rs.map(r => [r.id, r.replanned_ts])),
      paths: Object.fromEntries(rs.map(r => [r.id, { path: r.path ?? [], status: r.status }])) };
    if (!prev) return;                              // first state after a page load: show it, don't replay
    if (engine.phase !== "running") { stopReplan(map.current, anim.current); return; }
    if (active && engine.replan_ts != null && engine.replan_ts !== prev.replan) {
      const atStart = engine.replan_ts === engine.started_ts;
      playReplan(map.current, anim.current, active, atStart || !prev.active ? undefined : prev.paths[prev.active], engine.crew);
      return;
    }
    const bent = rs.find(r => r.replanned_ts != null && r.replanned_ts !== prev.replanned[r.id]);
    if (bent) playReplan(map.current, anim.current, bent, prev.paths[bent.id], engine.crew);
  }, [engine, engineAt, mapReady]);

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

  useEffect(() => {
    const m = map.current;
    if (!mapReady || !m) return;
    const vis = (id: string, on: boolean) => m.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    BASE_LAYERS.forEach(([id]) => vis(id, id === base));
    vis("fuel", overlays.fuel);
    ["osm-buildings", "osm-tracks", "osm-roads"].forEach(id => vis(id, overlays.osm));
    vis("perimeter-line", overlays.perimeter);
  }, [base, overlays, mapReady]);

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
          <span className="sb-state" title={active ? `route ${active.status}` : "no active route"}><i /></span>
        </div>
        <p className="sb-note">{crewNote || "Awaiting engine assessment"}</p>
        <div className="sb-metrics">
          <div><span>MARGIN</span><b>{active && active.margin_min != null ? active.margin_min.toFixed(1) : "—"}</b><small>{active?.margin_min == null ? "no fire threat" : "min · worst point"}</small></div>
          <div><span>DISTANCE</span><b>{active?.path?.length ? fmtDist(pathMetres(active.path)[active.path.length - 1]) : "—"}</b><small>to the zone</small></div>
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
    <div className="layer-switch">
      <button className={`layers-btn ${layersOpen ? "on" : ""}`} onClick={() => setLayersOpen(v => !v)} aria-expanded={layersOpen} title="Map layers">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 9 5-9 5-9-5 9-5z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="m3 13 9 5 9-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>Layers
      </button>
      {layersOpen && <div className="layers-panel">
        <h3>Base map</h3>
        {BASE_LAYERS.map(([id, label]) => <label key={id}><input type="radio" name="base" checked={base === id} onChange={() => setBase(id)} />{label}</label>)}
        <h3>Overlays</h3>
        {([["fuel", "Fuel (vegetation)"], ["osm", "Roads and buildings"], ["perimeter", "2013 fire perimeter"]] as const).map(([k, label]) =>
          <label key={k}><input type="checkbox" checked={overlays[k]} onChange={() => setOverlays(o => ({ ...o, [k]: !o[k] }))} />{label}</label>)}
        {(base === "slope" || overlays.fuel) && <ul className="layer-key">
          {base === "slope" && SLOPE_KEY.map(([c, t]) => <li key={t}><i style={{ background: c }} />{t}</li>)}
          {overlays.fuel && FUEL_KEY.map(([c, t]) => <li key={t}><i style={{ background: c }} />{t}</li>)}
        </ul>}
      </div>}
    </div>
    <div className="map-note">Hover a route or a sensor node for live times and readings</div>
  </main>;
}
