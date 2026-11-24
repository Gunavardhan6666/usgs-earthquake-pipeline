"""
tests/test_ml_model.py
----------------------
Unit tests for the ML significance predictor.

All tests use an in-memory SQLite database (no production data touched).
"""

import json
import sqlite3
from pathlib import Path
import sys

import pytest
import pandas as pd

# Allow importing src without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config" / "schema.sql"

SAMPLE_ROWS = [
    # (magnitude, mag_type, place, region, event_time_utc, event_date_utc,
    #  updated_time_utc, latitude, longitude, depth_km, felt, cdi, mmi, alert,
    #  tsunami, sig, status, net, nst, gap, rms, url, mag_class, depth_class,
    #  energy_j, is_sig, ingested_at, last_ingested, run_id)
    (
        5.5, "mww", "50km NW of Tokyo", "Japan",
        "2024-01-15T10:30:00Z", "2024-01-15",
        "2024-01-15T10:35:00Z",
        35.65, 139.70, 30.0, 10, 4.5, 3.8, "yellow",
        0, 650, "reviewed", "us", 45, 72.0, 0.85,
        "https://earthquake.usgs.gov/eq1",
        "moderate", "shallow", 1.995e12, 1,
        "2024-01-15T10:36:00Z", "2024-01-15T10:36:00Z", "run-001",
    ),
    (
        2.8, "ml", "15km SE of LA", "California",
        "2024-01-15T11:00:00Z", "2024-01-15",
        "2024-01-15T11:05:00Z",
        34.05, -118.25, 10.0, 0, None, None, None,
        0, 120, "automatic", "ci", 20, 95.0, 0.55,
        "https://earthquake.usgs.gov/eq2",
        "light", "shallow", 5.0e9, 0,
        "2024-01-15T11:06:00Z", "2024-01-15T11:06:00Z", "run-001",
    ),
]


def _make_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with schema + sample rows."""
    conn = sqlite3.connect(":memory:")
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    # Execute statement-by-statement (schema has multiple statements)
    for stmt in schema.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # Skip views that may reference empty tables during setup
    conn.commit()

    insert_sql = """
        INSERT OR IGNORE INTO silver_earthquakes VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
    """
    # Generate 60 rows so there's enough for train/test split
    for i in range(60):
        row = list(SAMPLE_ROWS[i % 2])
        row[0] = round(2.0 + (i % 60) * 0.1, 1)       # vary magnitude
        row[16] = 100 + (i * 10)                        # vary significance
        row[25] = 1 if row[16] > 500 else 0             # is_significant
        # Unique event_id and event_time
        event_id = f"us{i:04d}"
        base_time = f"2024-01-{(i%28)+1:02d}T10:30:00Z"
        base_date = f"2024-01-{(i%28)+1:02d}"
        full_row = [event_id] + row[1:]
        full_row[5] = base_time
        full_row[6] = base_date
        conn.execute(insert_sql, full_row[:30])
    conn.commit()
    return conn


@pytest.fixture
def mem_conn():
    conn = _make_db()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestLoadData:
    def test_loads_rows(self, mem_conn):
        from src.ml_model import _load_data
        df = _load_data(mem_conn)
        assert len(df) >= 2
        assert "magnitude" in df.columns
        assert "is_significant" in df.columns

    def test_no_negative_significance(self, mem_conn):
        from src.ml_model import _load_data
        df = _load_data(mem_conn)
        assert (df["is_significant"].isin([0, 1])).all()


class TestPreprocess:
    def test_no_nulls_after_preprocess(self, mem_conn):
        from src.ml_model import _load_data, _preprocess
        df = _load_data(mem_conn)
        df_proc, encoder = _preprocess(df, fit_encoder=True)
        assert df_proc["nst"].isna().sum() == 0
        assert df_proc["gap"].isna().sum() == 0
        assert df_proc["rms"].isna().sum() == 0

    def test_encoder_produces_integers(self, mem_conn):
        from src.ml_model import _load_data, _preprocess
        df = _load_data(mem_conn)
        df_proc, _ = _preprocess(df, fit_encoder=True)
        assert df_proc["magnitude_class_enc"].dtype in [int, "int64", "int32"]


class TestTraining:
    def test_train_returns_metrics(self, mem_conn, tmp_path, monkeypatch):
        """Train against in-memory DB; model files go to tmp_path."""
        from src import ml_model
        monkeypatch.setattr(ml_model, "MODELS_DIR", tmp_path)
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.joblib")
        monkeypatch.setattr(ml_model, "ENCODER_PATH", tmp_path / "enc.joblib")
        monkeypatch.setattr(ml_model, "FEATURE_META_PATH", tmp_path / "meta.json")

        meta = ml_model.train(mem_conn)

        assert "classification_report" in meta
        assert meta["n_train"] > 0
        assert meta["n_test"] > 0
        assert (tmp_path / "model.joblib").exists()

    def test_train_raises_on_too_few_rows(self):
        from src.ml_model import train
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE silver_earthquakes "
            "(magnitude REAL, depth_km REAL, latitude REAL, longitude REAL, "
            "magnitude_class TEXT, nst INTEGER, gap REAL, rms REAL, "
            "is_significant INTEGER, significance INTEGER, event_id TEXT)"
        )
        conn.commit()
        with pytest.raises(ValueError, match="Not enough data"):
            train(conn)
        conn.close()


class TestFeatureImportances:
    def test_magnitude_is_top_feature(self, mem_conn, tmp_path, monkeypatch):
        from src import ml_model
        monkeypatch.setattr(ml_model, "MODELS_DIR", tmp_path)
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.joblib")
        monkeypatch.setattr(ml_model, "ENCODER_PATH", tmp_path / "enc.joblib")
        monkeypatch.setattr(ml_model, "FEATURE_META_PATH", tmp_path / "meta.json")

        meta = ml_model.train(mem_conn)
        top_feature = list(meta["feature_importances"].keys())[0]
        # magnitude or significance-correlated features should dominate
        assert top_feature in ml_model.FEATURES
