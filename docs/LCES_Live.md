# LCES Live: Escape-Route Margin Monitor
Code Metal Forge Hackathon 2026, "Operation Lost and Found"
Nov 6–8, 2026 · BASE-X, Boston · Build window: Sat 10:00 to Sun 12:00 (~26 h)
Objective fit: OBJ.03 Map Changing Conditions (+ OBJ.02 hazard detection)
Judging: mission relevance · working live demo · deployability

---

## 1. One-liner
A few cheap, offline sensor nodes plus a thermal "lookout" feed a map that continuously answers one question:
**"Can this crew still reach safety before the fire does, and how much margin is left?"**
When wind shifts or a spot fire appears, routes change from green to amber to red, the map reroutes, and an alert fires within seconds.

LCES = Lookouts, Communications, Escape routes, Safety zones (the wildland firefighting safety system).

## 2. The problem (evidence)
- **Yarnell Hill, 2013: 19 Granite Mountain Hotshots killed.**
  - The forecast weather in the update hadn't materialized at the crew's location.
  - The lookout left at a trigger point, and the crew lost sight of the fire.
  - The alternate escape route was not scouted, timed or marked.
  - The wind shifted twice, and the crew had under 2 minutes to prepare a deployment site.
  - Source: Yarnell Hill Serious Accident Investigation Report.
- **GAO-25-107905:** the Forest Service can't track most firefighters on foot, and situational information "can quickly become outdated."
- **Esri, on national wildfire mapping:** the 24-hour mapping cycle leaves gaps in awareness.
- **Persona:** wildland crew bosses, lookouts, division supervisors.
- **Severity:** happens on every large fire, and failures are fatal.

## 3. What it does
**Inputs**
- Wind speed and direction, temperature, humidity, and flame/IR from each node.
- Thermal camera detections of hot spots and the fire front.
- Crew position (fixed, or a tag as a stretch goal).

**Computation**
1. **Fire arrival time:** a simplified spread model (an ellipse stretched by wind; for grass, roughly 10% of wind speed) estimates when fire reaches each route segment and safety zone.
2. **Crew escape time:** pgRouting over a slope-weighted trail network, using Tobler's hiking function as the cost:
   `speed_kmh = 6 * exp(-3.5 * |slope + 0.05|)`
3. **Margin:** fire arrival − crew escape.
   - Green: comfortable margin.
   - Amber: margin is shrinking.
   - Red: below the safety buffer, which triggers a reroute to an alternate route or safety zone.
4. **Dynamic trigger points:** recomputed as conditions change, instead of fixed landmarks.
5. **Data age:** every reading shows its age. A silent node goes grey instead of staying green.

**Output**
An offline map showing:
- routes colored by margin,
- a countdown,
- node health and data age,
- fire points,
- an alert banner.

The sensors are about 20% of the value. The margin engine and reroute map are the product.

## 4. Hardware
| Part | Source | Notes |
|---|---|---|
| Raspberry Pi 5 (16 GB) + 26 TOPS AI accelerator | Baseline kit | Base station: broker, DB, engine, UI |
| ESP32 / Arduino nodes (2–3) | Baseline + shared | Sensor nodes |
| Temperature/humidity, IR flame sensors | Grove kit / shared | Per node |
| Thermal camera | Shared library (contested) | Lookout; grab it at checkout first thing |
| LoRa modules | Shared library | Optional field link for one node |
| **Anemometer + wind vane** | **NOT on the list; bring your own** | Cup anemometer + vane (Micro Center/Adafruit) or a hot-wire sensor (Modern Device Rev C) |

Fallback if no thermal camera: Grove IR flame sensors.
Email forge@codemetal.ai to confirm outside hardware is allowed.

## 5. Architecture
```
[ESP32 sensor nodes] --Wi-Fi (demo) / LoRa (field)--> [Pi 5 base station] --> [Browser]
 wind, temp/RH, flame                                  Mosquitto (MQTT broker)
[Thermal camera on Pi] -----------------------------> Python ingest -> PostGIS
                                                      Margin engine (Python + pgRouting)
                                                      FastAPI + WebSocket -> MapLibre (PMTiles, offline)
```
- **Demo link:** the Pi runs a Wi-Fi hotspot, and the ESP32s publish over MQTT. This is the most reliable option indoors.
- **Field story:** LoRa (kilometers of range, no infrastructure). The LoRa receiver forwards packets into MQTT, so nothing downstream changes.
- Everything runs offline on the Pi, with no cloud.

### MQTT message (node → broker)
Topic: `lces/node/<node_id>/reading`
```json
{
  "node_id": "N1",
  "ts": "2026-11-07T14:02:11Z",
  "wind_speed_ms": 4.2,
  "wind_dir_deg": 225,
  "temp_c": 31.5,
  "rh_pct": 18,
  "flame": false,
  "battery_v": 3.9
}
```
Topic: `lces/lookout/detection` (thermal hot spot → ground coordinates via homography)

### PostGIS tables (sketch)
- `nodes(id, geom POINT, label)`, with positions entered once
- `readings(node_id, ts, wind_speed, wind_dir, temp, rh, flame)`
- `fire_points(id, ts, geom, source, confidence)`
- `trails(id, geom LINESTRING, source, target, slope, cost_s)`, the pgRouting topology
- `escape_routes(id, crew_id, geom, margin_s, status, updated_ts)`
- `safety_zones(id, geom POLYGON)`

## 6. Tech stack
- **Nodes:** Arduino/ESP32, PubSubClient (MQTT); anemometer read by interrupt pulse counting, wind vane by analog read mapped to degrees.
- **Broker:** Mosquitto.
- **Backend:** Python, paho-mqtt, PostGIS, pgRouting, FastAPI with WebSocket.
- **Vision:** OpenCV temperature threshold and blob detection on thermal frames (ML on the AI accelerator is a stretch goal).
- **Frontend:** MapLibre GL, PMTiles offline basemap, DEM/hillshade, GeoJSON layers pushed live.

## 7. What to learn (priority order)
1. ESP32 + Arduino: read sensors, publish MQTT.
2. MQTT basics: topics, publish/subscribe, paho-mqtt.
3. pgRouting: topology, `pgr_dijkstra` with a custom Tobler cost column.
4. Offline MapLibre + PMTiles + live GeoJSON over WebSocket.
5. Simple fire spread: elliptical spread with arrival time = distance / rate; keep it conservative.
6. Thermal camera on the Pi: frames, threshold, blobs, pixel-to-ground homography.
7. LoRa point-to-point (optional).

Already strong: 3, 4, 5. Real learning curve: 1, 2, 6.

## 8. Weekend plan
- **Before Saturday:**
  - PostGIS schema, trail network for a real place (Yarnell Hill terrain), offline tiles.
  - MapLibre shell, Mosquitto running.
  - Buy the anemometer and vane.
- **Sat 10:00:** check out the thermal camera and ESP32s. Validate the pain point with an operator mentor and get a quote for the pitch.
- **Sat morning:** one node streaming real wind and temperature end to end onto the map.
- **Sat afternoon:** margin engine and route coloring.
- **Sat evening:** thermal lookout and reroute logic. **Freeze features at about 20:00.**
- **Overnight / Sun morning:** tabletop terrain model, demo script, rehearse 5+ times, record a backup video.
- **Sun 12:00:** submission.

## 9. Demo script (about 90 seconds)
1. Open with the Yarnell Hill quote: an unscouted escape route, and under 2 minutes after the wind shift.
2. The tabletop map shows the route green, margin 6:00.
3. A fan swings around to simulate the wind shift, and a heat gun near the thermal node simulates a spot fire.
4. The margin collapses to 1:00, the route turns red, the alternate route is drawn, and the alert fires.
5. Close on cost per node, fully offline, LoRa-capable, and nodes placed at decision points.

## 10. Weaknesses and answers
- **Only 2–3 nodes:** "Nodes go at decision points (ridges, route junctions, safety zones), not as a dense sensor field."
- **Crude spread model:** "A conservative estimate with uncertainty bands, built to plug into real models (FARSITE, Rothermel)."
- **"Works on a mountain?":** cost per node, battery life, LoRa range, offline-first, and the Yarnell Hill timeline.
- **"Isn't this just a weather station?":** no. The product is the escape margin and the dynamic trigger points, a calculation nobody on the line can do in their head.
- **Honest risk:** the core is software-heavy. Make the thermal lookout plus live sensor-driven reroute the visible hardware moment.

## 11. Safety and rules
- No open flame or smoke machines: use a heat gun or hair dryer, and a fan for wind.
- SDR is receive-only. LoRa, Wi-Fi and BLE stay on certified modules at default power.

## 12. Sources
- Yarnell Hill Serious Accident Investigation Report: https://lessonslearned-prod-media-bucket.s3.us-gov-west-1.amazonaws.com/s3fs-public/irdoc/Yarnell_Hill_Fire_Investigation_Report.pdf
- GAO-25-107905: https://www.gao.gov/assets/gao-25-107905.pdf
- Esri, wildfire mapping: https://www.esri.com/about/newsroom/blog/uniting-everyone-fighting-americas-wildfires
- Forge hackathon page: https://www.codemetal.ai/forge-hackathon
