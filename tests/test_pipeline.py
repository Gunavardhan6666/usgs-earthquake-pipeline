import copy
import pytest

from src.load import (
    connect,
    init_schema,
    insert_rejects,
    start_run,
    upsert_quakes,
)
from src.settings import DB_PATH
from src.transform import (
    build_silver_records,
    validate_features,
)


def make_feature(**overrides) -> dict:
    base = {
        "type": "Feature",
        "id": "us7000abcd",
        "properties": {
            "mag": 4.6,
            "place": "10 km SW of Ridgecrest, CA",
            "time": 1700000000000,
            "updated": 1700003600000,
            "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000abcd",
            "felt": 12,
            "cdi": 4.1,
            "mmi": 3.8,
            "alert": None,
            "status": "reviewed",
            "tsunami": 0,
            "sig": 326,
            "net": "us",
            "magType": "mb",
            "nst": 45,
            "gap": 60.0,
            "dmin": 0.5,
            "rms": 0.8,
            "type": "earthquake",
        },
        "geometry": {
            "type": "Point",
            "coordinates": [-117.6, 35.6, 8.1],
        },
    }

    feature = copy.deepcopy(base)
    for k, v in overrides.items():
        if k in feature["properties"]:
            feature["properties"][k] = v
        elif k == "coordinates":
            feature["geometry"]["coordinates"] = v
        elif k in ("geometry", "properties", "id", "type"):
            feature[k] = v
        else:
            feature["properties"][k] = v
    return feature


# ==============================================================================
# Unit Tests (No Network, In-Memory DB)
# ==============================================================================


def test_valid_feature_passes():
    feature = make_feature()
    valid_models, rejects = validate_features([feature])
    assert len(valid_models) == 1
    assert len(rejects) == 0
    assert valid_models[0].event_id == "us7000abcd"


def test_invalid_features_quarantined():
    f_null_mag = make_feature(id="inv1", mag=None)
    f_lat_oob = make_feature(id="inv2", coordinates=[-117.6, 200.0, 8.1])
    f_bad_alert = make_feature(id="inv3", alert="purple")
    f_short_coords = make_feature(id="inv4", coordinates=[1, 2])

    invalid_features = [f_null_mag, f_lat_oob, f_bad_alert, f_short_coords]
    valid_models, rejects = validate_features(invalid_features)

    assert len(valid_models) == 0
    assert len(rejects) == 4
    for r in rejects:
        assert r["reason"] != ""
        assert r["event_id"] is not None


def test_time_conversion():
    feature = make_feature(time=1700000000000, updated=1700000000000)
    valid_models, _ = validate_features([feature])
    records = build_silver_records(valid_models)
    assert len(records) == 1
    assert records[0]["event_time_utc"] == "2023-11-14T22:13:20Z"
    assert records[0]["event_date_utc"] == "2023-11-14"


def test_derived_fields():
    # Boundary tests for classes and energy
    f1 = make_feature(id="df1", mag=5.5, coordinates=[-117.6, 35.6, 10.0], sig=600)
    f2 = make_feature(id="df2", mag=4.0, coordinates=[-117.6, 35.6, 70.0], sig=599)
    f3 = make_feature(id="df3", mag=7.0, coordinates=[-117.6, 35.6, 300.0], sig=100)

    valid_models, _ = validate_features([f1, f2, f3])
    records = {r["event_id"]: r for r in build_silver_records(valid_models)}

    # Check df1: mag 5.5, depth 10 -> moderate, shallow
    assert records["df1"]["magnitude_class"] == "moderate"
    assert records["df1"]["depth_class"] == "shallow"
    assert records["df1"]["energy_joules"] == pytest.approx(10 ** (1.5 * 5.5 + 4.8))
    assert records["df1"]["is_significant"] == 1

    # Check df2: mag 4.0, depth 70 -> light, intermediate
    assert records["df2"]["magnitude_class"] == "light"
    assert records["df2"]["depth_class"] == "intermediate"
    assert records["df2"]["is_significant"] == 0

    # Check df3: mag 7.0, depth 300 -> major, deep
    assert records["df3"]["magnitude_class"] == "major"
    assert records["df3"]["depth_class"] == "deep"


def test_region_extraction():
    f1 = make_feature(id="reg1", place="10 km SW of Ridgecrest, CA")
    f2 = make_feature(id="reg2", place="central Mid-Atlantic Ridge")

    valid_models, _ = validate_features([f1, f2])
    records = {r["event_id"]: r for r in build_silver_records(valid_models)}

    assert records["reg1"]["region"] == "CA"
    assert records["reg2"]["region"] == "central Mid-Atlantic Ridge"


def test_dedupe_keeps_latest_update():
    f_old = make_feature(id="same_id", updated=1700001000000, mag=3.0)
    f_new = make_feature(id="same_id", updated=1700002000000, mag=3.5)

    valid_models, _ = validate_features([f_old, f_new])
    records = build_silver_records(valid_models)

    assert len(records) == 1
    assert records[0]["event_id"] == "same_id"
    assert records[0]["magnitude"] == 3.5


def test_upsert_idempotent():
    conn = connect(":memory:")
    init_schema(conn)

    f1 = make_feature(id="u1")
    f2 = make_feature(id="u2")
    valid_models, _ = validate_features([f1, f2])
    records = build_silver_records(valid_models)

    stats1 = upsert_quakes(conn, records, "run1")
    conn.commit()
    assert stats1 == {"inserted": 2, "updated": 0, "unchanged": 0}

    stats2 = upsert_quakes(conn, records, "run2")
    conn.commit()
    assert stats2 == {"inserted": 0, "updated": 0, "unchanged": 2}

    total = conn.execute("SELECT COUNT(*) FROM silver_earthquakes;").fetchone()[0]
    assert total == 2
    conn.close()


def test_upsert_updates_newer():
    conn = connect(":memory:")
    init_schema(conn)

    f_initial = make_feature(id="u_up", updated=1700001000000, mag=4.0)
    valid_init, _ = validate_features([f_initial])
    rec_init = build_silver_records(valid_init)
    upsert_quakes(conn, rec_init, "run1")
    conn.commit()

    first_ingested = conn.execute(
        "SELECT first_ingested_at_utc, magnitude FROM silver_earthquakes WHERE event_id = 'u_up';"
    ).fetchone()
    assert first_ingested[1] == 4.0

    f_updated = make_feature(id="u_up", updated=1700002000000, mag=4.5)
    valid_up, _ = validate_features([f_updated])
    rec_up = build_silver_records(valid_up)
    stats = upsert_quakes(conn, rec_up, "run2")
    conn.commit()

    assert stats == {"inserted": 0, "updated": 1, "unchanged": 0}

    row_after = conn.execute(
        "SELECT first_ingested_at_utc, magnitude FROM silver_earthquakes WHERE event_id = 'u_up';"
    ).fetchone()
    assert row_after[1] == 4.5
    assert row_after[0] == first_ingested[0]
    conn.close()


def test_stale_update_ignored():
    conn = connect(":memory:")
    init_schema(conn)

    f_newer = make_feature(id="stale_test", updated=1700005000000, mag=5.0)
    valid_newer, _ = validate_features([f_newer])
    rec_newer = build_silver_records(valid_newer)
    upsert_quakes(conn, rec_newer, "run1")
    conn.commit()

    f_older = make_feature(id="stale_test", updated=1700002000000, mag=4.0)
    valid_older, _ = validate_features([f_older])
    rec_older = build_silver_records(valid_older)
    stats = upsert_quakes(conn, rec_older, "run2")
    conn.commit()

    assert stats == {"inserted": 0, "updated": 0, "unchanged": 1}

    mag = conn.execute(
        "SELECT magnitude FROM silver_earthquakes WHERE event_id = 'stale_test';"
    ).fetchone()[0]
    assert mag == 5.0
    conn.close()


def test_quarantine_persisted():
    conn = connect(":memory:")
    init_schema(conn)

    start_run(conn, "q_run", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    rejects = [
        {"event_id": "bad1", "reason": "invalid magnitude", "raw_json": '{"bad": 1}'}
    ]
    insert_rejects(conn, "q_run", rejects)
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) FROM rejected_records WHERE run_id = 'q_run';"
    ).fetchone()[0]
    assert count == 1
    conn.close()


# ==============================================================================
# Warehouse Audit Tests (Guarded by DB Existence)
# ==============================================================================


@pytest.mark.skipif(not DB_PATH.exists(), reason="Warehouse DB not found")
def test_warehouse_no_null_keys():
    conn = connect(DB_PATH)
    cur = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE event_id IS NULL OR magnitude IS NULL OR event_time_utc IS NULL;
        """
    )
    assert cur.fetchone()[0] == 0
    conn.close()


@pytest.mark.skipif(not DB_PATH.exists(), reason="Warehouse DB not found")
def test_warehouse_timestamp_format():
    conn = connect(DB_PATH)
    cur = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE event_time_utc NOT GLOB '????-??-??T??:??:??Z';
        """
    )
    assert cur.fetchone()[0] == 0
    conn.close()


@pytest.mark.skipif(not DB_PATH.exists(), reason="Warehouse DB not found")
def test_warehouse_magnitude_range():
    conn = connect(DB_PATH)
    cur = conn.execute(
        """
        SELECT COUNT(*)
        FROM silver_earthquakes
        WHERE magnitude < -2 OR magnitude > 10;
        """
    )
    assert cur.fetchone()[0] == 0
    conn.close()


@pytest.mark.skipif(not DB_PATH.exists(), reason="Warehouse DB not found")
def test_warehouse_gold_daily_summary():
    conn = connect(DB_PATH)
    cur = conn.execute("SELECT COUNT(*) FROM gold_daily_summary;")
    assert cur.fetchone()[0] >= 1
    conn.close()


@pytest.mark.skipif(not DB_PATH.exists(), reason="Warehouse DB not found")
def test_warehouse_no_duplicates():
    conn = connect(DB_PATH)
    cur = conn.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT event_id)
        FROM silver_earthquakes;
        """
    )
    total, distinct = cur.fetchone()
    assert total == distinct
    conn.close()
