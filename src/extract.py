import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from src.settings import (
    API_LIMIT,
    MAX_RETRIES,
    MIN_MAGNITUDE,
    RAW_DIR,
    TIMEOUT,
    USER_AGENT,
    USGS_BASE_URL,
)

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ExtractionError(Exception):
    """Raised when USGS API extraction fails or returns an invalid payload."""


def fetch_earthquakes(
    start: datetime,
    end: datetime,
    min_magnitude: float,
    session: Optional[requests.Session] = None,
) -> dict:
    params = {
        "format": "geojson",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_magnitude,
        "eventtype": "earthquake",
        "orderby": "time",
        "limit": API_LIMIT,
    }
    s = session or requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    last_err = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            r = s.get(USGS_BASE_URL, params=params, timeout=TIMEOUT)
            if r.status_code in RETRY_STATUS:
                wait = (
                    int(r.headers.get("Retry-After", 2**attempt))
                    if r.status_code == 429
                    else 2**attempt
                )
                last_err = f"HTTP {r.status_code}"
                logger.warning(
                    "USGS request failed with %s (attempt %d/%d). Retrying in %ds...",
                    last_err,
                    attempt + 1,
                    MAX_RETRIES + 1,
                    wait,
                )
                time.sleep(wait)
                continue

            if r.status_code == 204:
                return {"type": "FeatureCollection", "features": []}

            r.raise_for_status()  # other 4xx -> raised immediately, never retried

            try:
                payload = r.json()
            except ValueError as e:
                raise ExtractionError("Invalid JSON") from e

            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = repr(e)
            wait = 2**attempt
            logger.warning(
                "USGS network error: %s (attempt %d/%d). Retrying in %ds...",
                last_err,
                attempt + 1,
                MAX_RETRIES + 1,
                wait,
            )
            time.sleep(wait)
    else:
        raise ExtractionError(f"Failed after {MAX_RETRIES + 1} attempts: {last_err}")

    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ExtractionError("Unexpected response shape: missing 'features' list")

    if len(payload["features"]) >= API_LIMIT:
        raise ExtractionError("API limit reached; narrow the time window")

    return payload


def save_raw(payload: dict, run_id: str, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"usgs_{run_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=1)
    logger.info(
        "Starting smoke extraction window from %s to %s (min_magnitude=%.1f)",
        start.isoformat(),
        end.isoformat(),
        MIN_MAGNITUDE,
    )
    payload = fetch_earthquakes(start, end, min_magnitude=MIN_MAGNITUDE)
    feature_count = len(payload.get("features", []))
    saved_path = save_raw(payload, run_id="smoke", raw_dir=RAW_DIR)
    print(f"Extracted {feature_count} features successfully.")
    print(f"Bronze raw payload saved to: {saved_path}")
