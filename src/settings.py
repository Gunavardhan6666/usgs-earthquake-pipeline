import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

def _path(env_name: str, default: str) -> Path:
    p = Path(os.getenv(env_name, default))
    return p if p.is_absolute() else PROJECT_ROOT / p

USGS_BASE_URL = os.getenv("USGS_BASE_URL", "https://earthquake.usgs.gov/fdsnws/event/1/query")
DB_PATH       = _path("DB_PATH", "data/warehouse/earthquakes.db")
RAW_DIR       = _path("RAW_DIR", "data/raw")
SCHEMA_PATH   = PROJECT_ROOT / "config" / "schema.sql"
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "2.5"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "30"))
MAX_RETRIES   = int(os.getenv("MAX_RETRIES", "4"))
USER_AGENT    = os.getenv("USER_AGENT", "usgs-quake-pipeline/1.0 (student project)")
TIMEOUT       = (5, 30)
API_LIMIT     = 20000
