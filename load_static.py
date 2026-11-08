"""Load the static Yarnell layers into PostGIS (docs/LCES_Design.md §3.2, §6), for map display and SQL.

Rerunnable: each run replaces what it loaded. Everything is stored in EPSG:32612 (metres).
  rasters  dem, slope, aspect, fuel       (raster2pgsql, ships with PostGIS; 100 x 100 tiles)
  features perimeter, sites, scenario, osm (one table, `layer` = file name; OSM `kind` = road/building/...)
  nodes    geom + label from nodes.json

Once per database (superuser):  sudo -u postgres psql -d fire -c 'CREATE EXTENSION postgis; CREATE EXTENSION postgis_raster'
Run (DATABASE_URL from .env):  uv run python load_static.py
"""
import json
import os
import subprocess
from pathlib import Path

import psycopg
from dotenv import load_dotenv

DATA = Path(__file__).parent / "data" / "yarnell"
RASTERS = ("dem", "slope", "aspect", "fuel")
LAYERS = ("perimeter", "sites", "scenario", "osm")
UTM = "ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), 32612)"


def main() -> None:
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute((Path(__file__).parent / "schema.sql").read_text())
        if not conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis_raster'").fetchone():
            raise SystemExit("PostGIS raster support is off. Once, as the database superuser:\n"
                             "  sudo -u postgres psql -d fire -c 'CREATE EXTENSION postgis_raster'")
        for name in RASTERS:
            # -d drop + recreate, -I spatial index, -C constraints
            sql = subprocess.run(["raster2pgsql", "-d", "-s", "32612", "-I", "-C", "-t", "100x100",
                                  str(DATA / f"{name}.tif"), name], check=True, capture_output=True, text=True).stdout
            conn.execute(sql.replace("BEGIN;", "").replace("END;", ""))   # one transaction for the whole load
            conn.execute(f"ANALYZE {name}")
            print(f"raster {name}")

        conn.execute("""CREATE TABLE IF NOT EXISTS static_features (
                          id BIGSERIAL PRIMARY KEY, layer TEXT NOT NULL, name TEXT, props JSONB NOT NULL,
                          geom geometry(Geometry, 32612) NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS static_features_geom_idx ON static_features USING GIST (geom)")
        conn.execute("DELETE FROM static_features")
        with conn.cursor() as cur:
            for layer in LAYERS:
                features = json.loads((DATA / f"{layer}.geojson").read_text())["features"]
                cur.executemany(f"INSERT INTO static_features (layer, name, props, geom) VALUES (%s, %s, %s, {UTM})",
                                [(layer, (f["properties"] or {}).get("name"), json.dumps(f["properties"] or {}),
                                  json.dumps(f["geometry"])) for f in features])
                print(f"layer {layer}: {len(features)} features")

            nodes = json.loads((DATA / "nodes.json").read_text())
            cur.executemany(f"""INSERT INTO nodes (id, label, geom) VALUES (%s, %s, {UTM})
                                ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label, geom = EXCLUDED.geom""",
                            [(nid, n["label"], json.dumps({"type": "Point", "coordinates": [n["lon"], n["lat"]]}))
                             for nid, n in nodes.items()])
            print(f"nodes: {len(nodes)}")


if __name__ == "__main__":
    main()
