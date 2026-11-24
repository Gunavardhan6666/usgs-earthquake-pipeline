"""
src/ml_model.py
---------------
Phase 2: Scikit-learn significance predictor.

Trains a RandomForestClassifier to predict whether an incoming earthquake
will be flagged as 'significant' (is_significant=1, i.e. significance > 500).

Usage
-----
  # Train and save model (run once after pipeline has data):
  python -m src.ml_model --train

  # Predict on the latest N records and print results:
  python -m src.ml_model --predict --top 20
"""

import argparse
import json
import logging
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

from src.settings import DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODELS_DIR / "significance_rf.joblib"
ENCODER_PATH = MODELS_DIR / "magnitude_class_encoder.joblib"
FEATURE_META_PATH = MODELS_DIR / "feature_meta.json"

FEATURES = [
    "magnitude",
    "depth_km",
    "latitude",
    "longitude",
    "magnitude_class_enc",   # encoded categorical
    "nst",
    "gap",
    "rms",
]
TARGET = "is_significant"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_data(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load all silver records needed for ML."""
    query = """
        SELECT
            magnitude, depth_km, latitude, longitude,
            magnitude_class, nst, gap, rms,
            is_significant, significance, event_id
        FROM silver_earthquakes
        ORDER BY event_time_utc DESC
    """
    df = pd.read_sql_query(query, conn)
    logger.info("Loaded %d rows from silver_earthquakes for ML.", len(df))
    return df


def _preprocess(df: pd.DataFrame, encoder: LabelEncoder | None = None,
                fit_encoder: bool = False) -> tuple[pd.DataFrame, LabelEncoder]:
    """Encode categoricals and fill nulls. Returns processed df + encoder."""
    df = df.copy()

    # Fill numeric nulls with median
    for col in ["nst", "gap", "rms"]:
        df[col] = df[col].fillna(df[col].median())

    # Encode magnitude_class (ordinal: minor < light < moderate < strong < major)
    if fit_encoder:
        encoder = LabelEncoder()
        df["magnitude_class_enc"] = encoder.fit_transform(df["magnitude_class"])
    else:
        df["magnitude_class_enc"] = encoder.transform(df["magnitude_class"])

    return df, encoder


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def train(conn: sqlite3.Connection) -> dict:
    """
    Train the RandomForest model, evaluate it, and persist to disk.
    Returns a dict of evaluation metrics.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    df = _load_data(conn)

    if len(df) < 50:
        raise ValueError(
            f"Not enough data to train: {len(df)} rows. "
            "Run the pipeline first to ingest at least 50 records."
        )

    df, encoder = _preprocess(df, fit_encoder=True)

    X = df[FEATURES]
    y = df[TARGET]

    # Class distribution info
    class_counts = y.value_counts().to_dict()
    logger.info("Class distribution: %s", class_counts)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=5,
        class_weight="balanced",   # handles imbalanced significant vs non-significant
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # Evaluate
    y_pred = model.predict(X_test)
    report = classification_report(y_test, y_pred, output_dict=True)
    cm = confusion_matrix(y_test, y_pred).tolist()

    # Feature importances
    importances = dict(
        sorted(
            zip(FEATURES, model.feature_importances_),
            key=lambda x: x[1],
            reverse=True,
        )
    )

    # Save artefacts
    joblib.dump(model, MODEL_PATH)
    joblib.dump(encoder, ENCODER_PATH)

    meta = {
        "features": FEATURES,
        "target": TARGET,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "class_distribution": {str(k): int(v) for k, v in class_counts.items()},
        "classification_report": report,
        "confusion_matrix": cm,
        "feature_importances": {k: round(float(v), 4) for k, v in importances.items()},
    }
    FEATURE_META_PATH.write_text(json.dumps(meta, indent=2))

    logger.info("Model saved to %s", MODEL_PATH)
    logger.info("Feature importances: %s", importances)
    return meta


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------
def load_model() -> tuple[RandomForestClassifier, LabelEncoder]:
    """Load persisted model and encoder from disk."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"No trained model found at {MODEL_PATH}. "
            "Run `python -m src.ml_model --train` first."
        )
    model = joblib.load(MODEL_PATH)
    encoder = joblib.load(ENCODER_PATH)
    return model, encoder


def predict_batch(df: pd.DataFrame) -> pd.Series:
    """
    Given a DataFrame with the raw silver_earthquakes columns,
    return a Series of predicted is_significant (0 or 1) values.
    Used by the pipeline to annotate new records before load.
    """
    model, encoder = load_model()
    df_proc, _ = _preprocess(df, encoder=encoder, fit_encoder=False)
    preds = model.predict(df_proc[FEATURES])
    return pd.Series(preds, index=df.index, name="ml_prediction")


def predict_latest(conn: sqlite3.Connection, top_n: int = 20) -> pd.DataFrame:
    """Return the latest top_n records with their ML predictions appended."""
    df = _load_data(conn)
    df = df.head(top_n).copy()
    model, encoder = load_model()
    df_proc, _ = _preprocess(df, encoder=encoder, fit_encoder=False)
    df["ml_prediction"] = model.predict(df_proc[FEATURES])
    df["ml_probability"] = model.predict_proba(df_proc[FEATURES])[:, 1].round(3)
    return df[["event_id", "magnitude", "depth_km", "significance",
               "is_significant", "ml_prediction", "ml_probability"]]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_metrics(meta: dict) -> None:
    print("\n========== MODEL TRAINING COMPLETE ==========")
    print(f"  Training rows : {meta['n_train']}")
    print(f"  Test rows     : {meta['n_test']}")
    print(f"  Class dist    : {meta['class_distribution']}")
    print("\n--- Classification Report ---")
    r = meta["classification_report"]
    for label in ["0", "1"]:
        m = r.get(str(label), r.get(label, {}))
        print(
            f"  Class {label}: precision={m.get('precision',0):.2f}  "
            f"recall={m.get('recall',0):.2f}  f1={m.get('f1-score',0):.2f}"
        )
    print("\n--- Top Feature Importances ---")
    for feat, imp in list(meta["feature_importances"].items())[:5]:
        bar = "█" * int(imp * 40)
        print(f"  {feat:<25} {bar} {imp:.4f}")
    print("=============================================\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="USGS ML Significance Predictor")
    parser.add_argument("--train", action="store_true", help="Train and save the model")
    parser.add_argument("--predict", action="store_true",
                        help="Predict on latest records")
    parser.add_argument("--top", type=int, default=20,
                        help="Number of latest records to predict on (default: 20)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        if args.train:
            meta = train(conn)
            _print_metrics(meta)

        if args.predict:
            results = predict_latest(conn, top_n=args.top)
            print("\n========== PREDICTIONS (latest records) ==========")
            print(results.to_string(index=False))
            print("===================================================\n")

        if not args.train and not args.predict:
            parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
