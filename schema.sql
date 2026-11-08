CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    label TEXT,
    x_cm DOUBLE PRECISION,
    y_cm DOUBLE PRECISION,
    -- Reserved for the final EPSG:32612 coordinate after a tabletop transform is agreed.
    geom geometry(Point, 32612),
    last_seen TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS readings (
    id BIGSERIAL PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(id),
    seq BIGINT NOT NULL,
    temp_c DOUBLE PRECISION,
    rh DOUBLE PRECISION,
    wind_ms DOUBLE PRECISION,
    wind_dir DOUBLE PRECISION,
    flame INTEGER,
    batt DOUBLE PRECISION,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw JSONB NOT NULL
);

-- seq restarts at 0 when a node reboots, so (node_id, seq) is not unique.
ALTER TABLE readings DROP CONSTRAINT IF EXISTS readings_node_id_seq_key;

CREATE INDEX IF NOT EXISTS readings_node_time_idx
    ON readings (node_id, received_at DESC);

CREATE TABLE IF NOT EXISTS fire_points (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    x_cm DOUBLE PRECISION NOT NULL,
    y_cm DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION,
    geom geometry(Point, 32612),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw JSONB NOT NULL
);
