import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map } from "maplibre-gl";
// MapLibre v6 loads its worker from a separate file; the Vite build must ship it, or no GeoJSON layer draws.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Feature, FeatureCollection } from "geojson";
import hillshadeUrl from "../../data/yarnell/hillshade.png";
import perimeterRaw from "../../data/yarnell/perimeter.geojson?raw";
import sitesRaw from "../../data/yarnell/sites.geojson?raw";
import scenarioRaw from "../../data/yarnell/scenario.geojson?raw";
import metaRaw from "../../data/yarnell/meta.json?raw";
import type { EngineState, Envelope, SensorReading } from "./types";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Corners = [[number, number], [number, number], [number, number], [number, number]];
const corners = JSON.parse(metaRaw).corners_lonlat_tl_tr_br_bl as Corners;
const perimeter = JSON.parse(perimeterRaw) as FeatureCollection;
const sites = JSON.parse(sitesRaw) as FeatureCollection;
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
const AREAS = ["front_10min", "front_5min", "burning", "black"] as const;

const socketUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.hostname}:8000/ws`;
const freshness = (at: number | undefined, now: number) => !at ? "unknown" : now - at < 20 ? "fresh" : now - at < 60 ? "aging" : "stale";
const age = (at: number | undefined, now: number) => !at ? "waiting" : now - at < 5 ? "live now" : `${Math.max(0, Math.round(now - at))}s ago`;
const routeMessage = (route: { margin_min: number | null; eta_min?: number }) =>
  (route.margin_min == null ? "No fire threat" : `${route.margin_min.toFixed(1)} min margin`) + (route.eta_min != null ? ` · ETA ${route.eta_min.toFixed(1)} min` : "");
const fc = (features: Feature[]): FeatureCollection => ({ type: "FeatureCollection", features });
const point = (lon: number, lat: number, properties: Record<string, unknown> = {}): Feature =>
  ({ type: "Feature", properties, geometry: { type: "Point", coordinates: [lon, lat] } });

// engine/state -> one FeatureCollection per map source
function engineLayers(engine: EngineState): Record<string, FeatureCollection> {
  const out: Record<string, FeatureCollection> = {
    routes: fc((engine.routes ?? []).filter(r => r.path?.length).map(r => ({
      type: "Feature", properties: { id: r.id, status: r.status, active: r.active },
      geometry: { type: "LineString", coordinates: r.path! } }))),
    nodes: fc((engine.nodes ?? []).filter(n => n.lat != null && n.lon != null).map(n => point(n.lon!, n.lat!, { id: n.id, status: n.status }))),
    fires: fc((engine.fires ?? []).map(f => point(f.lon, f.lat))),
    crew: fc(engine.crew ? [point(engine.crew.lon, engine.crew.lat)] : []),
  };
  for (const k of AREAS) {
    const geom = engine.areas?.[k];
    out[k] = fc(geom?.coordinates.length ? [{ type: "Feature", properties: {}, geometry: geom }] : []);
  }
  return out;
}

function addLayers(map: Map) {
  map.addSource("hillshade", { type: "image", url: hillshadeUrl, coordinates: corners });
  map.addLayer({ id: "hillshade", type: "raster", source: "hillshade", paint: { "raster-opacity": .68, "raster-saturation": -.75 } });
  map.addSource("perimeter", { type: "geojson", data: perimeter });
  map.addLayer({ id: "perimeter-line", type: "line", source: "perimeter", paint: { "line-color": "#ff765c", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": .7 } });
  const areaPaint: Record<string, [string, number]> = { front_10min: ["#ffd25e", .22], front_5min: ["#ff9a3c", .38], burning: ["#ff3b2f", .7], black: ["#050505", .62] };
  for (const k of AREAS) {
    map.addSource(k, { type: "geojson", data: empty });
    map.addLayer({ id: k, type: "fill", source: k, paint: { "fill-color": areaPaint[k][0], "fill-opacity": areaPaint[k][1] } });
  }
  map.addSource("descent", { type: "geojson", data: descentLine });
  map.addLayer({ id: "descent", type: "line", source: "descent", paint: { "line-color": "#e8eef0", "line-width": 1.5, "line-dasharray": [1, 2], "line-opacity": .8 } });
  map.addSource("routes", { type: "geojson", data: empty });
  map.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: {
    "line-color": ["match", ["get", "status"], "green", "#4fd18b", "amber", "#ffc857", "red", "#ff625f", "#8c9691"],
    "line-width": ["case", ["get", "active"], 7, 4], "line-opacity": ["case", ["get", "active"], 1, .75] } });
  map.addSource("sites", { type: "geojson", data: sites });
  map.addLayer({ id: "sites", type: "circle", source: "sites", paint: { "circle-color": "#f5d364", "circle-radius": 4, "circle-stroke-color": "#17221b", "circle-stroke-width": 2 } });
  map.addSource("fires", { type: "geojson", data: empty });
  map.addLayer({ id: "fires", type: "circle", source: "fires", paint: { "circle-color": "#ff3b2f", "circle-radius": 7, "circle-stroke-color": "#ffd25e", "circle-stroke-width": 2 } });
  map.addSource("nodes", { type: "geojson", data: empty });
  map.addLayer({ id: "nodes", type: "circle", source: "nodes", paint: {
    "circle-color": ["match", ["get", "status"], "fresh", "#55d7ff", "aging", "#ffc857", "#77847c"], "circle-radius": 7, "circle-stroke-color": "#071014", "circle-stroke-width": 3 } });
  map.addSource("crew", { type: "geojson", data: empty });
  map.addLayer({ id: "crew", type: "circle", source: "crew", paint: { "circle-color": "#ffffff", "circle-radius": 8, "circle-stroke-color": "#1f6feb", "circle-stroke-width": 4 } });
}

export default function App() {
  const mapElement = useRef<HTMLDivElement>(null), map = useRef<Map | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [sensors, setSensors] = useState<Record<string, SensorReading>>({});
  const [receivedAt, setReceivedAt] = useState<Record<string, number>>({});
  const [engine, setEngine] = useState<EngineState>({ type: "state", routes: [], alerts: [] });
  const [engineAt, setEngineAt] = useState<number>();
  const [connection, setConnection] = useState<"connecting" | "live" | "offline">("connecting");
  const [now, setNow] = useState(() => Date.now() / 1000);
  const routes = engine.routes ?? [];

  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);

  useEffect(() => {
    if (!mapElement.current || map.current) return;
    const instance = new maplibregl.Map({ container: mapElement.current, center: [-112.775, 34.22], zoom: 12.7,
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#17221b" } }] } });
    map.current = instance;
    instance.on("load", () => { addLayers(instance); instance.fitBounds(scenarioBounds, { padding: 90 }); setMapReady(true); });
    return () => { instance.remove(); map.current = null; setMapReady(false); };
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
          setReceivedAt(c => ({ ...c, [reading.node]: message.rx_ts }));
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
  }, [engine, mapReady]);

  const listed = useMemo(() => Object.entries(sensors).sort(([a], [b]) => a.localeCompare(b)), [sensors]);
  const nodes = engine.nodes ?? [];
  const freshCount = listed.filter(([id]) => freshness(receivedAt[id], now) === "fresh").length;
  const staleCount = listed.filter(([id]) => freshness(receivedAt[id], now) === "stale").length;
  const active = routes.find(r => r.active), hasFresh = freshCount > 0;
  const engineFresh = engineAt != null && now - engineAt < 10;
  const w = engine.weather;
  const burning = engine.areas?.burning.coordinates.length ?? 0;

  return <main><header><div><p className="eyebrow">LCES LIVE <span>/</span> FIREGUARD</p><h1>Escape-route status</h1><p className="subtitle">Decision support for crew evacuation and sensor health.</p></div><div className={`connection ${connection}`}><i />{connection === "live" ? "Bridge connected" : connection === "connecting" ? "Connecting" : "Bridge offline"}</div></header>
    <section className={`readiness ${hasFresh ? "ready" : "caution"}`}><b>{hasFresh ? "✓" : "!"}</b><div><strong>{hasFresh ? `${freshCount} live sensor${freshCount === 1 ? "" : "s"} reporting` : "No live sensor data"}</strong><p>{hasFresh ? "Route decisions include current weather from reporting nodes." : "Displayed sensor readings may be cached. The engine is using conservative fallback weather until a fresh heartbeat arrives."}</p></div>{staleCount > 0 && <small>{staleCount} stale cached reading{staleCount === 1 ? "" : "s"}</small>}</section>
    <section className="summary-grid">
      <div><span>MARGIN ENGINE</span><strong className={engineFresh ? "good" : "warn"}>{engineFresh ? "Running" : engineAt ? "No recent state" : "Waiting"}</strong><small>{engineAt ? `Last state ${age(engineAt, now)}` : "No engine/state received yet"}</small></div>
      <div><span>SENSOR HEALTH</span><strong className={hasFresh ? "good" : "warn"}>{freshCount} / {nodes.length || 3} live</strong><small>{hasFresh ? "Fresh readings in last 20 seconds" : "Waiting for a current heartbeat"}</small></div>
      <div><span>ACTIVE ESCAPE ROUTE</span><strong className={active?.status === "red" ? "danger" : active?.status === "amber" ? "warn" : undefined}>{active?.name ?? "—"}</strong><small>{active ? routeMessage(active) : "Engine state not received"}</small></div>
      <div><span>FIRE</span><strong className={burning || engine.fires?.length ? "danger" : "good"}>{burning ? "Front burning" : engine.areas ? "No active front" : "No fire data"}{engine.fires?.length ? ` · ${engine.fires.length} spot` : ""}</strong><small>{w?.wind_ms != null ? `Wind ${w.wind_ms} m/s from ${w.wind_from}° · ${w.temp_c} °C · ${w.rh}% RH` : "No weather yet"}</small></div>
    </section>
    <section className="layout"><div className="map-wrap"><div className="map-toolbar"><span><i className="fresh" /> Node fresh</span><span><i className="aging" /> Aging</span><span><i className="stale" /> Stale</span><span><i style={{ background: "#ff3b2f" }} /> Burning</span><span><i style={{ background: "#ff9a3c" }} /> +5 min</span><span><i style={{ background: "#ffd25e" }} /> +10 min</span><span><i style={{ background: "#050505", border: "1px solid #777" }} /> Black</span><span><i style={{ background: "#fff", border: "2px solid #1f6feb" }} /> Crew</span></div><div ref={mapElement} className="map" /><div className="map-note">Yarnell Hill, AZ <span>·</span> dashed red: 2013 final perimeter <span>·</span> dotted white: 2013 descent line</div></div><aside>
      <section className="panel"><div className="panel-heading"><div><h2>Escape routes</h2><p>Latest engine assessment</p></div><em>{routes.length ? "ENGINE" : "WAITING"}</em></div>{routes.length ? routes.map(route => <div className="route" key={route.id}><i className={`status ${route.status}`} /><div><strong>{route.name ?? route.id}</strong><small>{route.active ? "Active route" : "Alternate route"}</small></div><aside><b>{routeMessage(route)}</b>{route.active && <em>ACTIVE</em>}</aside></div>) : <p className="muted">No engine state received yet.</p>}</section>
      <section className="panel"><div className="panel-heading"><div><h2>Sensor feed</h2><p>Last received weather updates</p></div><em className={hasFresh ? "good" : "warn"}>{freshCount} LIVE</em></div>{listed.length ? listed.map(([id, reading]) => { const state = freshness(receivedAt[id], now); return <div className="sensor" key={id}><i className={state} /><div><strong>{id}</strong><small>{age(receivedAt[id], now)} · seq {reading.seq}</small></div><aside><b>{reading.temp_c.toFixed(1)}°C</b><b>{reading.rh.toFixed(1)}% RH</b>{reading.wind_ms >= 0 && <b>{reading.wind_ms.toFixed(1)} m/s from {reading.wind_dir}°</b>}</aside></div>; }) : <p className="muted">No weather messages received in this session.</p>}</section>
      <section className="panel alert"><div className="panel-heading"><div><h2>Operational alerts</h2><p>Engine notifications and evacuation timing</p></div></div>{engine.alerts?.length ? engine.alerts.map(alert => <p key={alert}>{alert}</p>) : <p className="muted">No active engine alerts.</p>}{engine.leave_by_ts && <p className="leave">Leave by {new Date(engine.leave_by_ts * 1000).toLocaleTimeString()}</p>}{engine.weather?.notes?.length ? <p className="weather-note">Fallback weather: {engine.weather.notes.join(" · ")}</p> : null}</section>
    </aside></section></main>;
}
