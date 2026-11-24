"""
app/routes.py
-------------
Flask route definitions for the USGS Earthquake Analytics Dashboard.

Endpoints
---------
  GET /                         → Dashboard HTML page
  GET /api/earthquakes          → JSON list of latest earthquakes
  GET /api/summary/daily        → JSON daily aggregates
  GET /api/summary/regions      → JSON region aggregates
  GET /api/summary/magnitude    → JSON magnitude class distribution
  GET /api/pipeline/status      → JSON latest pipeline run + health
"""

import sqlite3
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

# Resolve DB path relative to project root (robust, no hardcoded paths)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "warehouse" / "earthquakes.db"

bp = Blueprint("main", __name__)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row   # rows behave like dicts
    return conn


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------
@bp.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: Earthquakes
# ---------------------------------------------------------------------------
@bp.route("/api/earthquakes")
def api_earthquakes():
    """
    Query params:
      limit    int   default 100, max 1000
      min_mag  float default 0.0
      days     int   default 30 (look back N days from latest event)
    """
    limit = min(int(request.args.get("limit", 100)), 1000)
    min_mag = float(request.args.get("min_mag", 0.0))
    days = int(request.args.get("days", 30))

    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                event_id, magnitude, mag_type, place, region,
                event_time_utc, event_date_utc,
                latitude, longitude, depth_km,
                magnitude_class, depth_class,
                significance, is_significant,
                alert, tsunami, felt_reports,
                energy_joules, url
            FROM silver_earthquakes
            WHERE magnitude >= ?
              AND event_date_utc >= DATE(
                    (SELECT MAX(event_date_utc) FROM silver_earthquakes),
                    '-' || ? || ' days'
                )
            ORDER BY event_time_utc DESC
            LIMIT ?
            """,
            (min_mag, days, limit),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"count": len(rows), "data": _rows_to_list(rows)})


# ---------------------------------------------------------------------------
# API: Daily summary
# ---------------------------------------------------------------------------
@bp.route("/api/summary/daily")
def api_daily_summary():
    days = int(request.args.get("days", 30))
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM gold_daily_summary
            ORDER BY event_date_utc DESC
            LIMIT ?
            """,
            (days,),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"data": _rows_to_list(rows)})


# ---------------------------------------------------------------------------
# API: Region summary
# ---------------------------------------------------------------------------
@bp.route("/api/summary/regions")
def api_region_summary():
    top = int(request.args.get("top", 15))
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM gold_region_summary
            ORDER BY event_count DESC
            LIMIT ?
            """,
            (top,),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"data": _rows_to_list(rows)})


# ---------------------------------------------------------------------------
# API: Magnitude class distribution
# ---------------------------------------------------------------------------
@bp.route("/api/summary/magnitude")
def api_magnitude_distribution():
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM gold_magnitude_distribution ORDER BY sort_order"
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"data": _rows_to_list(rows)})


# ---------------------------------------------------------------------------
# API: Pipeline health / latest run
# ---------------------------------------------------------------------------
@bp.route("/api/pipeline/status")
def api_pipeline_status():
    conn = _get_conn()
    try:
        latest = conn.execute(
            "SELECT * FROM gold_pipeline_health LIMIT 1"
        ).fetchone()
        total_quakes = conn.execute(
            "SELECT COUNT(*) AS total FROM silver_earthquakes"
        ).fetchone()
        total_rejected = conn.execute(
            "SELECT COUNT(*) AS total FROM rejected_records"
        ).fetchone()
    finally:
        conn.close()

    return jsonify({
        "latest_run": dict(latest) if latest else None,
        "total_earthquakes": total_quakes["total"] if total_quakes else 0,
        "total_rejected": total_rejected["total"] if total_rejected else 0,
    })
