# USGS Earthquake Data Pipeline

This is a simple, local ETL pipeline built with Python and SQLite that pulls earthquake data from the public USGS API, cleans and validates it, and stores it in a local database for querying. 

I am new to data engineering and built this as a beginner learning project to understand how core pipeline concepts work in practice—specifically how to handle nested JSON from an API, validate data before loading, and avoid duplicate rows when running a script multiple times. Rather than using cloud tools or building a production-scale system, I kept everything local so I could focus on the fundamentals.

## Why the USGS API?

Instead of using a static CSV file, I picked the [USGS Earthquake Catalog API](https://earthquake.usgs.gov/fdsnws/event/1/) because earthquake records get updated after they are first published (for example, when seismologists revise the magnitude or status of an event). This gave me a practical way to test `INSERT ... ON CONFLICT DO UPDATE` (upsert) logic in SQLite rather than just appending rows every time the script runs.

## How It Works

The project follows a basic three-step layout loosely inspired by the Medallion (Bronze / Silver / Gold) pattern, kept completely local using folders and SQLite:

1. **Extract (`src/extract.py` - Bronze):**
   Fetches raw GeoJSON data from the USGS endpoint for a given lookback window (default is 30 days, magnitude 2.5+). It saves a copy of the raw JSON file in `data/raw/` so I can inspect the original payload or re-run transformations without hitting the API every time.

2. **Transform & Validate (`src/models.py`, `src/transform.py` - Silver prep):**
   Flattens the nested GeoJSON structure and checks each record using a `Pydantic` model.
   - If a record is missing required fields or has out-of-range values, it gets set aside for a `rejected_records` table instead of crashing the script.
   - Valid records are cleaned with `pandas` (converting millisecond timestamps to UTC ISO strings, rounding coordinates, deduplicating within the batch, and adding basic derived columns like `magnitude_class` and `depth_class`).

3. **Load & Check (`src/load.py`, `src/quality.py` - Silver & Gold):**
   Loads the cleaned records into `data/warehouse/earthquakes.db`.
   - Uses an upsert query on `event_id` that only updates an existing row if the incoming `updated_time_utc` is newer than what is already in the table.
   - Runs a few basic sanity checks (e.g., making sure primary keys aren't null and timestamps match the expected format) before committing the transaction.
   - Logs basic run counts (extracted, inserted, updated, unchanged) in a `pipeline_runs` table.
   - Creates two SQL views (`gold_daily_summary` and `gold_region_summary`) for basic aggregation queries.

## Project Structure

```text
├── config/
│   └── schema.sql          # SQLite tables, indexes, and SQL views
├── data/
│   ├── raw/                # Saved raw GeoJSON files (ignored by git)
│   └── warehouse/          # Local SQLite database file (ignored by git)
├── src/
│   ├── settings.py         # Loads config variables from .env
│   ├── models.py           # Pydantic validation schema
│   ├── extract.py          # API request and retry logic
│   ├── transform.py        # JSON flattening and Pandas cleaning
│   ├── load.py             # SQLite connection and upsert queries
│   ├── quality.py          # Post-load SQL sanity checks
│   └── pipeline.py         # Main CLI script to run the steps in order
├── tests/
│   └── test_pipeline.py    # Unit tests using in-memory SQLite
├── .env.example
└── requirements.txt
```

## How to Run Locally

Since this is a backend data script with no web frontend, it runs from the terminal on your local machine.

**1. Clone the repository:**
```powershell
git clone https://github.com/Gunavardhan6666/usgs-earthquake-pipeline.git
cd usgs-earthquake-pipeline
```

**2. Create a virtual environment and install dependencies:**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**3. Set up the environment file:**
```powershell
cp .env.example .env
```
*(The USGS API is public and does not require an API key, so `.env` just holds basic default settings like lookback days and file paths).*

**4. Run the pipeline:**
```powershell
# Run for the last 7 days
python -m src.pipeline --days 7

# Run for the full 30-day window
python -m src.pipeline --days 30
```
If you run the same command twice in a row, the second run will show almost all records as `unchanged` rather than inserting duplicates.

**5. View summary tables:**
```powershell
python -m src.pipeline --report
```
This prints the recent run history from `pipeline_runs`, the last 5 days of stats from `gold_daily_summary`, and the top regions by earthquake count from `gold_region_summary`.

**6. Run tests:**
```powershell
pytest -v
```

## What I Learned & Current Limitations

Because I built this to learn data engineering basics on my laptop, there are a few things I learned and several limitations compared to a real production system:

- **Idempotency & Upserts:** I learned how `ON CONFLICT(event_id) DO UPDATE ... WHERE excluded.updated_time_utc > silver_earthquakes.updated_time_utc` prevents duplicate rows and stops older data from overwriting newer revisions.
- **Schema Validation:** Using Pydantic helped me see how to catch bad records early and put them in a separate `rejected_records` table so one bad row doesn't break the whole batch.
- **Transactions:** Wrapping the database load and checks in a single SQLite transaction taught me how `conn.rollback()` can undo a partial load if something goes wrong.
- **Scope:** Everything runs manually from the command line on a local SQLite file rather than using a scheduled cloud workflow.
