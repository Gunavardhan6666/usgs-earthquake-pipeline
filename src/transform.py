import json
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from pydantic import ValidationError

from src.models import QuakeRecord
from src.settings import RAW_DIR

logger = logging.getLogger(__name__)

SILVER_COLUMNS = [
    "event_id",
    "magnitude",
    "mag_type",
    "place",
    "region",
    "event_time_utc",
    "event_date_utc",
    "updated_time_utc",
    "latitude",
    "longitude",
    "depth_km",
    "felt_reports",
    "cdi",
    "mmi",
    "alert",
    "tsunami",
    "significance",
    "status",
    "network",
    "nst",
    "gap",
    "rms",
    "url",
    "magnitude_class",
    "depth_class",
    "energy_joules",
    "is_significant",
]


def flatten_feature(feature: dict) -> dict:
    if not isinstance(feature, dict):
        raise ValueError("flatten_error: feature must be a dict")

    props = feature.get("properties")
    if not isinstance(props, dict):
        raise ValueError("flatten_error: 'properties' must be a dict")

    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        raise ValueError("flatten_error: 'geometry' must be a dict")

    coords = geom.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 3:
        raise ValueError("flatten_error: 'coordinates' must contain at least 3 elements")

    return {
        "event_id": feature.get("id"),
        "event_type": props.get("type"),
        "magnitude": props.get("mag"),
        "mag_type": props.get("magType"),
        "place": props.get("place"),
        "time_ms": props.get("time"),
        "updated_ms": props.get("updated"),
        "longitude": coords[0],
        "latitude": coords[1],
        "depth_km": coords[2],
        "felt": props.get("felt"),
        "cdi": props.get("cdi"),
        "mmi": props.get("mmi"),
        "alert": props.get("alert"),
        "tsunami": props.get("tsunami"),
        "sig": props.get("sig"),
        "status": props.get("status"),
        "net": props.get("net"),
        "nst": props.get("nst"),
        "gap": props.get("gap"),
        "rms": props.get("rms"),
        "url": props.get("url"),
    }


def validate_features(features: list[dict]) -> tuple[list[QuakeRecord], list[dict]]:
    valid_models: list[QuakeRecord] = []
    rejects: list[dict] = []

    for feature in features:
        raw_json = json.dumps(feature)
        event_id = feature.get("id") if isinstance(feature, dict) else None

        try:
            flat = flatten_feature(feature)
            model = QuakeRecord(**flat)
            valid_models.append(model)
        except ValidationError as err:
            reason = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()
            )[:500]
            rejects.append(
                {"event_id": event_id, "reason": reason, "raw_json": raw_json}
            )
        except ValueError as err:
            reason = str(err)[:500]
            rejects.append(
                {"event_id": event_id, "reason": reason, "raw_json": raw_json}
            )
        except Exception as err:
            reason = f"unexpected_error: {str(err)}"[:500]
            rejects.append(
                {"event_id": event_id, "reason": reason, "raw_json": raw_json}
            )

    return valid_models, rejects


def _to_py(v: Any) -> Any:
    if pd.isna(v):
        return None
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    return v


def build_silver_records(valid_models: list[QuakeRecord]) -> list[dict]:
    if not valid_models:
        return []

    df = pd.DataFrame([m.model_dump() for m in valid_models])

    # 1. Epoch to UTC ISO timestamps
    df["event_time_utc"] = (
        pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    df["updated_time_utc"] = (
        pd.to_datetime(df["updated_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # 2. Event date UTC
    df["event_date_utc"] = (
        pd.to_datetime(df["time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    )

    # 3. Rounding BEFORE derived metrics
    df["magnitude"] = df["magnitude"].round(2)
    df["latitude"] = df["latitude"].round(4)
    df["longitude"] = df["longitude"].round(4)
    df["depth_km"] = df["depth_km"].round(2)

    # 4. Clean place
    df["place"] = df["place"].apply(
        lambda p: p.strip() if isinstance(p, str) and p.strip() else None
    )

    # 5. Renames
    df = df.rename(
        columns={"felt": "felt_reports", "sig": "significance", "net": "network"}
    )

    # 6. Dedupe within batch (sort updated_ms ascending, keep last)
    df = df.sort_values(by="updated_ms", ascending=True)
    df = df.drop_duplicates(subset=["event_id"], keep="last")

    # 7. Derived metrics
    def _derive_region(place: Optional[str]) -> Optional[str]:
        if place is None or not isinstance(place, str):
            return None
        if ", " in place:
            return place.rsplit(", ", 1)[-1]
        return place

    df["region"] = df["place"].apply(_derive_region)

    df["magnitude_class"] = pd.cut(
        df["magnitude"],
        bins=[-float("inf"), 4, 5, 6, 7, float("inf")],
        right=False,
        labels=["minor", "light", "moderate", "strong", "major"],
    ).astype(str)

    df["depth_class"] = pd.cut(
        df["depth_km"],
        bins=[-float("inf"), 70, 300, float("inf")],
        right=False,
        labels=["shallow", "intermediate", "deep"],
    ).astype(str)

    df["energy_joules"] = 10 ** (1.5 * df["magnitude"] + 4.8)

    df["is_significant"] = df["significance"].apply(
        lambda s: 1 if s is not None and s >= 600 else 0
    )

    # 8. Python & SQLite type conversion
    int_cols = {"felt_reports", "nst", "tsunami", "significance", "is_significant"}
    records: list[dict] = []

    for row in df[SILVER_COLUMNS].to_dict(orient="records"):
        cleaned_row = {}
        for k, v in row.items():
            val = _to_py(v)
            if k in int_cols and val is not None:
                val = int(val)
            cleaned_row[k] = val
        records.append(cleaned_row)

    return records


def transform_payload(payload: dict) -> tuple[list[dict], list[dict]]:
    features = payload.get("features", []) if isinstance(payload, dict) else []
    valid_models, rejects = validate_features(features)
    records = build_silver_records(valid_models)
    return records, rejects


if __name__ == "__main__":
    raw_files = sorted(RAW_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not raw_files:
        print(f"No raw JSON files found in {RAW_DIR}")
        exit(1)

    latest_file = raw_files[0]
    print(f"Loading raw payload from: {latest_file}")
    payload = json.loads(latest_file.read_text(encoding="utf-8"))

    valid_records, rejects = transform_payload(payload)
    print(f"Valid records: {len(valid_records)}")
    print(f"Rejected records: {len(rejects)}")
    if valid_records:
        print("First valid record:")
        print(json.dumps(valid_records[0], indent=2))
