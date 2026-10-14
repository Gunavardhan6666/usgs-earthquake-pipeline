import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

logger = logging.getLogger(__name__)


class DataQualityError(Exception):
    """Raised when one or more data quality checks fail."""


def run_checks(
    conn: sqlite3.Connection, extracted: int, rejected: int
) -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []

    # 1. extracted_nonzero: extracted > 0
    chk1 = extracted > 0
    msg1 = f"Extracted records: {extracted}"
    results.append(("extracted_nonzero", chk1, msg1))

    # 2. reject_rate_ok: (rejected / extracted) <= 0.05
    if extracted > 0:
        rate = rejected / extracted
        chk2 = rate <= 0.05
        msg2 = f"Rejection rate: {rate:.2%} ({rejected}/{extracted})"
    else:
        chk2 = False
        msg2 = "Extraction count is 0; cannot compute reject rate"
    results.append(("reject_rate_ok", chk2, msg2))

    # 3. no_null_keys: COUNT(*) WHERE event_id IS NULL OR magnitude IS NULL OR event_time_utc IS NULL == 0
    cursor = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE event_id IS NULL OR magnitude IS NULL OR event_time_utc IS NULL;
        """
    )
    null_count = cursor.fetchone()[0]
    chk3 = null_count == 0
    msg3 = f"Found {null_count} rows with null keys"
    results.append(("no_null_keys", chk3, msg3))

    # 4. timestamp_format_ok: 0 rows where event_time_utc NOT GLOB '????-??-??T??:??:??Z'
    cursor = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE event_time_utc NOT GLOB '????-??-??T??:??:??Z';
        """
    )
    bad_ts_count = cursor.fetchone()[0]
    chk4 = bad_ts_count == 0
    msg4 = f"Found {bad_ts_count} rows with non-conforming timestamps"
    results.append(("timestamp_format_ok", chk4, msg4))

    # 5. magnitude_range_ok: 0 rows outside [-2, 10]
    cursor = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE magnitude < -2 OR magnitude > 10;
        """
    )
    bad_mag_count = cursor.fetchone()[0]
    chk5 = bad_mag_count == 0
    msg5 = f"Found {bad_mag_count} rows outside [-2, 10] magnitude range"
    results.append(("magnitude_range_ok", chk5, msg5))

    # 6. freshness_ok: newest event_time_utc is within the last 48 hours
    cursor = conn.execute(
        """
        SELECT MAX(event_time_utc)
        FROM silver_earthquakes;
        """
    )
    max_ts_str = cursor.fetchone()[0]
    if max_ts_str:
        max_dt = datetime.strptime(max_ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        now_utc = datetime.now(timezone.utc)
        age = now_utc - max_dt
        chk6 = age <= timedelta(hours=48)
        msg6 = f"Newest event: {max_ts_str} (age: {age})"
    else:
        chk6 = False
        msg6 = "No events in silver_earthquakes table to evaluate freshness"
    results.append(("freshness_ok", chk6, msg6))

    return results
