import argparse
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone

from src.extract import fetch_earthquakes, save_raw
from src.load import (
    connect,
    finish_run,
    init_schema,
    insert_rejects,
    start_run,
    upsert_quakes,
)
from src.quality import DataQualityError, run_checks
from src.settings import (
    DB_PATH,
    LOOKBACK_DAYS,
    MIN_MAGNITUDE,
    RAW_DIR,
)
from src.transform import transform_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pipeline")


def print_report(conn) -> None:
    init_schema(conn)

    print("\n" + "=" * 80)
    print("PIPELINE RUNS (LAST 5)")
    print("=" * 80)
    cursor = conn.execute(
        """
        SELECT run_id, started_at_utc, finished_at_utc, status,
               records_extracted, records_valid, records_rejected,
               rows_inserted, rows_updated, rows_unchanged
        FROM pipeline_runs
        ORDER BY started_at_utc DESC
        LIMIT 5;
        """
    )
    runs = cursor.fetchall()
    if not runs:
        print("No pipeline runs recorded yet.")
    else:
        print(
            f"{'Run ID':<26} {'Status':<8} {'Extracted':<10} {'Valid':<8} {'Rejected':<9} {'Inserted':<9} {'Updated':<8} {'Unchanged':<10}"
        )
        print("-" * 94)
        for r in runs:
            print(
                f"{r[0]:<26} {r[3]:<8} {str(r[4]):<10} {str(r[5]):<8} {str(r[6]):<9} {str(r[7]):<9} {str(r[8]):<8} {str(r[9]):<10}"
            )

    print("\n" + "=" * 80)
    print("GOLD DAILY SUMMARY (MOST RECENT 5 DAYS)")
    print("=" * 80)
    cursor = conn.execute(
        """
        SELECT event_date_utc, event_count, avg_magnitude, max_magnitude,
               avg_depth_km, significant_count, tsunami_flag_count
        FROM gold_daily_summary
        ORDER BY event_date_utc DESC
        LIMIT 5;
        """
    )
    daily = cursor.fetchall()
    if not daily:
        print("No gold daily summary data found.")
    else:
        print(
            f"{'Date':<12} {'Count':<8} {'Avg Mag':<9} {'Max Mag':<9} {'Avg Depth':<11} {'Significant':<12} {'Tsunami':<8}"
        )
        print("-" * 72)
        for r in daily:
            print(
                f"{r[0]:<12} {r[1]:<8} {str(r[2]):<9} {str(r[3]):<9} {str(r[4]):<11} {str(r[5]):<12} {str(r[6]):<8}"
            )

    print("\n" + "=" * 80)
    print("GOLD REGION SUMMARY (TOP 5 REGIONS BY EVENT COUNT)")
    print("=" * 80)
    cursor = conn.execute(
        """
        SELECT region, event_count, avg_magnitude, max_magnitude, significant_count
        FROM gold_region_summary
        ORDER BY event_count DESC
        LIMIT 5;
        """
    )
    regions = cursor.fetchall()
    if not regions:
        print("No gold region summary data found.")
    else:
        print(
            f"{'Region':<35} {'Count':<8} {'Avg Mag':<9} {'Max Mag':<9} {'Significant':<12}"
        )
        print("-" * 75)
        for r in regions:
            print(
                f"{r[0]:<35} {r[1]:<8} {str(r[2]):<9} {str(r[3]):<9} {str(r[4]):<12}"
            )
    print("=" * 80 + "\n")


def run_pipeline(days: int = LOOKBACK_DAYS, min_magnitude: float = MIN_MAGNITUDE) -> int:
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid.uuid4().hex[:8]
    )
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=days)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info(
        "Starting pipeline run %s (window: %s to %s, min_magnitude=%.1f)",
        run_id,
        start_iso,
        end_iso,
        min_magnitude,
    )

    conn = connect(DB_PATH)
    init_schema(conn)
    start_run(conn, run_id, start_iso, end_iso)

    extracted_count = 0
    valid_count = 0
    rejects_count = 0
    upsert_stats = {"inserted": 0, "updated": 0, "unchanged": 0}

    try:
        # 1. Extraction (Bronze)
        logger.info("Step 1: Extracting GeoJSON from USGS API...")
        payload = fetch_earthquakes(start, end, min_magnitude=min_magnitude)
        saved_raw_path = save_raw(payload, run_id, RAW_DIR)
        features = payload.get("features", [])
        extracted_count = len(features)
        logger.info(
            "Extracted %d features -> Bronze saved at %s",
            extracted_count,
            saved_raw_path,
        )

        # 2. Transformation & Validation (Silver preparation)
        logger.info("Step 2: Validating and transforming features...")
        records, rejects = transform_payload(payload)
        valid_count = len(records)
        rejects_count = len(rejects)
        logger.info(
            "Transformation complete: %d valid records, %d rejected records",
            valid_count,
            rejects_count,
        )

        # 3. Stage into DB (Atomic transaction: quarantine + silver upsert)
        if rejects:
            logger.info(
                "Step 3a: Routing %d rejected records to quarantine table...",
                rejects_count,
            )
            insert_rejects(conn, run_id, rejects)

        logger.info(
            "Step 3b: Upserting %d valid records into silver_earthquakes...",
            valid_count,
        )
        upsert_stats = upsert_quakes(conn, records, run_id)
        logger.info("Upsert stage results: %s", upsert_stats)

        # 4. Quality Gate (Audit inside transaction)
        logger.info("Step 4: Running Write-Audit-Publish quality gate checks...")
        check_results = run_checks(conn, extracted_count, rejects_count)
        failed_checks = [
            f"{name} ({detail})"
            for name, passed, detail in check_results
            if not passed
        ]

        if failed_checks:
            raise DataQualityError(
                f"Quality gate checks failed: {'; '.join(failed_checks)}"
            )

        for name, passed, detail in check_results:
            logger.info("  Quality check [%s]: PASSED (%s)", name, detail)

        # 5. Publish (Commit transaction)
        conn.commit()
        logger.info("Step 5: Quality checks passed. Transaction committed.")

        full_stats = {
            "records_extracted": extracted_count,
            "records_valid": valid_count,
            "records_rejected": rejects_count,
            "rows_inserted": upsert_stats["inserted"],
            "rows_updated": upsert_stats["updated"],
            "rows_unchanged": upsert_stats["unchanged"],
        }
        finish_run(conn, run_id, "SUCCESS", full_stats)
        logger.info(
            "Run %s completed with status SUCCESS: %s", run_id, full_stats
        )
        return 0

    except Exception as e:
        logger.error(
            "Pipeline run %s failed: %s", run_id, str(e), exc_info=True
        )
        conn.rollback()
        logger.warning("Active transaction rolled back.")

        error_stats = {
            "records_extracted": extracted_count,
            "records_valid": valid_count,
            "records_rejected": rejects_count,
            "rows_inserted": upsert_stats.get("inserted", 0),
            "rows_updated": upsert_stats.get("updated", 0),
            "rows_unchanged": upsert_stats.get("unchanged", 0),
        }
        finish_run(conn, run_id, "FAILED", error_stats, error=str(e)[:500])
        return 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="USGS Earthquake Medallion ETL Pipeline"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=LOOKBACK_DAYS,
        help=f"Number of lookback days (default: {LOOKBACK_DAYS})",
    )
    parser.add_argument(
        "--min-magnitude",
        type=float,
        default=MIN_MAGNITUDE,
        help=f"Minimum earthquake magnitude (default: {MIN_MAGNITUDE})",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print summary report from warehouse and exit",
    )
    args = parser.parse_args()

    if args.report:
        conn = connect(DB_PATH)
        try:
            print_report(conn)
        finally:
            conn.close()
        sys.exit(0)

    exit_code = run_pipeline(days=args.days, min_magnitude=args.min_magnitude)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
