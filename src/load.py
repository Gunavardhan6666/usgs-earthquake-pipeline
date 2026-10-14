import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from src.settings import SCHEMA_PATH

logger = logging.getLogger(__name__)

UPSERT_QUAKES_SQL = """
INSERT INTO silver_earthquakes (
    event_id, magnitude, mag_type, place, region, event_time_utc,
    event_date_utc, updated_time_utc, latitude, longitude, depth_km,
    felt_reports, cdi, mmi, alert, tsunami, significance, status,
    network, nst, gap, rms, url, magnitude_class, depth_class,
    energy_joules, is_significant, first_ingested_at_utc,
    last_ingested_at_utc, last_run_id
) VALUES (
    :event_id, :magnitude, :mag_type, :place, :region, :event_time_utc,
    :event_date_utc, :updated_time_utc, :latitude, :longitude, :depth_km,
    :felt_reports, :cdi, :mmi, :alert, :tsunami, :significance, :status,
    :network, :nst, :gap, :rms, :url, :magnitude_class, :depth_class,
    :energy_joules, :is_significant, :first_ingested_at_utc,
    :last_ingested_at_utc, :last_run_id
)
ON CONFLICT(event_id) DO UPDATE SET
    magnitude = excluded.magnitude,
    mag_type = excluded.mag_type,
    place = excluded.place,
    region = excluded.region,
    event_time_utc = excluded.event_time_utc,
    event_date_utc = excluded.event_date_utc,
    updated_time_utc = excluded.updated_time_utc,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    depth_km = excluded.depth_km,
    felt_reports = excluded.felt_reports,
    cdi = excluded.cdi,
    mmi = excluded.mmi,
    alert = excluded.alert,
    tsunami = excluded.tsunami,
    significance = excluded.significance,
    status = excluded.status,
    network = excluded.network,
    nst = excluded.nst,
    gap = excluded.gap,
    rms = excluded.rms,
    url = excluded.url,
    magnitude_class = excluded.magnitude_class,
    depth_class = excluded.depth_class,
    energy_joules = excluded.energy_joules,
    is_significant = excluded.is_significant,
    last_ingested_at_utc = excluded.last_ingested_at_utc,
    last_run_id = excluded.last_run_id
WHERE excluded.updated_time_utc > silver_earthquakes.updated_time_utc;
"""


def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    path_str = str(db_path)
    if path_str != ":memory:":
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path_str)
    conn.execute("PRAGMA foreign_keys = ON;")
    if path_str != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")

    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)


def start_run(
    conn: sqlite3.Connection,
    run_id: str,
    window_start: str,
    window_end: str,
) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO pipeline_runs (
            run_id, started_at_utc, status, window_start_utc, window_end_utc
        ) VALUES (?, ?, 'RUNNING', ?, ?);
        """,
        (run_id, now, window_start, window_end),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    stats: dict,
    error: Optional[str] = None,
) -> None:
    finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE pipeline_runs
        SET finished_at_utc = ?,
            status = ?,
            records_extracted = ?,
            records_valid = ?,
            records_rejected = ?,
            rows_inserted = ?,
            rows_updated = ?,
            rows_unchanged = ?,
            error_message = ?
        WHERE run_id = ?;
        """,
        (
            finished_at,
            status,
            stats.get("records_extracted", stats.get("extracted")),
            stats.get("records_valid", stats.get("valid")),
            stats.get("records_rejected", stats.get("rejected")),
            stats.get("rows_inserted", stats.get("inserted")),
            stats.get("rows_updated", stats.get("updated")),
            stats.get("rows_unchanged", stats.get("unchanged")),
            error[:500] if error else None,
            run_id,
        ),
    )
    conn.commit()


def insert_rejects(
    conn: sqlite3.Connection,
    run_id: str,
    rejects: list[dict],
) -> None:
    if not rejects:
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        (
            run_id,
            r.get("event_id"),
            r["reason"],
            r["raw_json"],
            now,
        )
        for r in rejects
    ]
    conn.executemany(
        """
        INSERT INTO rejected_records (
            run_id, event_id, reason, raw_json, quarantined_at_utc
        ) VALUES (?, ?, ?, ?, ?);
        """,
        rows,
    )


def upsert_quakes(
    conn: sqlite3.Connection,
    records: list[dict],
    run_id: str,
) -> dict:
    if not records:
        return {"inserted": 0, "updated": 0, "unchanged": 0}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_ids = [r["event_id"] for r in records]
    existing_map: dict[str, str] = {}
    chunk_size = 500

    for i in range(0, len(event_ids), chunk_size):
        chunk = event_ids[i : i + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        cursor = conn.execute(
            f"SELECT event_id, updated_time_utc FROM silver_earthquakes WHERE event_id IN ({placeholders});",
            chunk,
        )
        for eid, utime in cursor.fetchall():
            existing_map[eid] = utime

    inserted_records: list[dict] = []
    updated_records: list[dict] = []
    unchanged_count = 0

    for r in records:
        eid = r["event_id"]
        new_updated = r["updated_time_utc"]
        if eid not in existing_map:
            inserted_records.append(r)
        elif new_updated > existing_map[eid]:
            updated_records.append(r)
        else:
            unchanged_count += 1

    to_upsert = inserted_records + updated_records
    if to_upsert:
        upsert_data: list[dict] = []
        for r in to_upsert:
            row = dict(r)
            row["first_ingested_at_utc"] = now
            row["last_ingested_at_utc"] = now
            row["last_run_id"] = run_id
            upsert_data.append(row)

        conn.executemany(UPSERT_QUAKES_SQL, upsert_data)

    return {
        "inserted": len(inserted_records),
        "updated": len(updated_records),
        "unchanged": unchanged_count,
    }


if __name__ == "__main__":
    conn = connect(":memory:")
    init_schema(conn)

    rec1 = {
        "event_id": "quake1",
        "magnitude": 4.5,
        "mag_type": "mb",
        "place": "10 km SW of Ridgecrest, CA",
        "region": "CA",
        "event_time_utc": "2026-10-14T00:00:00Z",
        "event_date_utc": "2026-10-14",
        "updated_time_utc": "2026-10-14T01:00:00Z",
        "latitude": 35.6,
        "longitude": -117.6,
        "depth_km": 8.1,
        "felt_reports": 12,
        "cdi": 4.1,
        "mmi": 3.8,
        "alert": "green",
        "tsunami": 0,
        "significance": 326,
        "status": "reviewed",
        "network": "us",
        "nst": 45,
        "gap": 60.0,
        "rms": 0.8,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/quake1",
        "magnitude_class": "light",
        "depth_class": "shallow",
        "energy_joules": 354813389.23,
        "is_significant": 0,
    }
    rec2 = {
        "event_id": "quake2",
        "magnitude": 6.2,
        "mag_type": "mww",
        "place": "Tokyo, Japan",
        "region": "Japan",
        "event_time_utc": "2026-10-14T02:00:00Z",
        "event_date_utc": "2026-10-14",
        "updated_time_utc": "2026-10-14T02:30:00Z",
        "latitude": 35.68,
        "longitude": 139.76,
        "depth_km": 35.0,
        "felt_reports": 500,
        "cdi": 6.5,
        "mmi": 6.0,
        "alert": "yellow",
        "tsunami": 0,
        "significance": 620,
        "status": "reviewed",
        "network": "us",
        "nst": 110,
        "gap": 30.0,
        "rms": 0.5,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/quake2",
        "magnitude_class": "strong",
        "depth_class": "shallow",
        "energy_joules": 125892541179.42,
        "is_significant": 1,
    }

    test_records = [rec1, rec2]

    stats_run1 = upsert_quakes(conn, test_records, "run_1")
    conn.commit()
    print(stats_run1)

    stats_run2 = upsert_quakes(conn, test_records, "run_2")
    conn.commit()
    print(stats_run2)

    conn.close()
