PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id              TEXT PRIMARY KEY,
    started_at_utc      TEXT NOT NULL,
    finished_at_utc     TEXT,
    status              TEXT NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    window_start_utc    TEXT NOT NULL,
    window_end_utc      TEXT NOT NULL,
    records_extracted   INTEGER,
    records_valid       INTEGER,
    records_rejected    INTEGER,
    rows_inserted       INTEGER,
    rows_updated        INTEGER,
    rows_unchanged      INTEGER,
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS silver_earthquakes (
    event_id                TEXT PRIMARY KEY,
    magnitude               REAL NOT NULL CHECK (magnitude BETWEEN -2 AND 10),
    mag_type                TEXT,
    place                   TEXT,
    region                  TEXT,
    event_time_utc          TEXT NOT NULL,
    event_date_utc          TEXT NOT NULL,
    updated_time_utc        TEXT NOT NULL,
    latitude                REAL NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude               REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    depth_km                REAL NOT NULL CHECK (depth_km  BETWEEN -10  AND 800),
    felt_reports            INTEGER CHECK (felt_reports IS NULL OR felt_reports >= 0),
    cdi                     REAL,
    mmi                     REAL,
    alert                   TEXT CHECK (alert IS NULL OR alert IN ('green','yellow','orange','red')),
    tsunami                 INTEGER NOT NULL CHECK (tsunami IN (0,1)),
    significance            INTEGER NOT NULL CHECK (significance >= 0),
    status                  TEXT,
    network                 TEXT,
    nst                     INTEGER,
    gap                     REAL,
    rms                     REAL,
    url                     TEXT,
    magnitude_class         TEXT NOT NULL CHECK (magnitude_class IN ('minor','light','moderate','strong','major')),
    depth_class             TEXT NOT NULL CHECK (depth_class IN ('shallow','intermediate','deep')),
    energy_joules           REAL NOT NULL CHECK (energy_joules > 0),
    is_significant          INTEGER NOT NULL CHECK (is_significant IN (0,1)),
    first_ingested_at_utc   TEXT NOT NULL,
    last_ingested_at_utc    TEXT NOT NULL,
    last_run_id             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quakes_event_time ON silver_earthquakes(event_time_utc);
CREATE INDEX IF NOT EXISTS idx_quakes_event_date ON silver_earthquakes(event_date_utc);
CREATE INDEX IF NOT EXISTS idx_quakes_magnitude  ON silver_earthquakes(magnitude);
CREATE INDEX IF NOT EXISTS idx_quakes_region     ON silver_earthquakes(region);

CREATE TABLE IF NOT EXISTS rejected_records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    event_id            TEXT,
    reason              TEXT NOT NULL,
    raw_json            TEXT NOT NULL,
    quarantined_at_utc  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_records(run_id);

CREATE VIEW IF NOT EXISTS gold_daily_summary AS
SELECT event_date_utc,
       COUNT(*)                         AS event_count,
       ROUND(AVG(magnitude), 2)         AS avg_magnitude,
       MAX(magnitude)                   AS max_magnitude,
       ROUND(AVG(depth_km), 1)          AS avg_depth_km,
       SUM(is_significant)              AS significant_count,
       SUM(tsunami)                     AS tsunami_flag_count
FROM silver_earthquakes
GROUP BY event_date_utc;

CREATE VIEW IF NOT EXISTS gold_region_summary AS
SELECT region,
       COUNT(*)                         AS event_count,
       ROUND(AVG(magnitude), 2)         AS avg_magnitude,
       MAX(magnitude)                   AS max_magnitude,
       SUM(is_significant)              AS significant_count
FROM silver_earthquakes
WHERE region IS NOT NULL
GROUP BY region;
