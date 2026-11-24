# Power BI Dashboard Setup Guide

This guide explains how to connect Power BI Desktop to the local SQLite warehouse
(`data/warehouse/earthquakes.db`) and build the USGS Earthquake Analytics Dashboard.

---

## Prerequisites

| Tool | Download |
|------|----------|
| Power BI Desktop (free) | https://powerbi.microsoft.com/desktop |
| SQLite ODBC Driver (free) | http://www.ch-werner.de/sqliteodbc/ — download `sqliteodbc_w64.exe` |

Install both before proceeding.

---

## Step 1 — Install the SQLite ODBC Driver

1. Run `sqliteodbc_w64.exe` as Administrator.
2. Accept all defaults. It registers `SQLite3 ODBC Driver` in Windows ODBC Data Sources.
3. Verify: open **ODBC Data Source Administrator (64-bit)** (search in Start Menu) → **Drivers** tab → you should see `SQLite3 ODBC Driver`.

---

## Step 2 — Create an ODBC DSN (Data Source Name)

1. Open **ODBC Data Source Administrator (64-bit)**.
2. Go to **User DSN** tab → click **Add**.
3. Select **SQLite3 ODBC Driver** → click **Finish**.
4. Fill in:
   - **Data Source Name:** `USGS_Earthquakes`
   - **Database Name:** Browse to your project folder and select:
     `d:\Desktop\Semester 5\Data_Project\data\warehouse\earthquakes.db`
5. Click **OK**.

---

## Step 3 — Connect Power BI to the DSN

1. Open **Power BI Desktop**.
2. Click **Get Data** → search for **ODBC** → click **Connect**.
3. In the dropdown, select **USGS_Earthquakes** (the DSN you just created).
4. Click **OK** (no credentials needed for local SQLite).
5. In the **Navigator** panel, you will see all tables and views:
   - `silver_earthquakes` ← main fact table
   - `gold_daily_summary` ← pre-aggregated daily view
   - `gold_region_summary` ← pre-aggregated regional view
   - `gold_magnitude_distribution` ← view for histogram
   - `gold_pipeline_health` ← view for run monitoring
   - `pipeline_runs` ← run log
   - `rejected_records` ← quarantine log

6. Select all 7 → click **Load**.

---

## Step 4 — Build the Dashboard (5 Visuals)

### Visual 1 — World Map (Earthquake Locations)
- Visual type: **Map** (or **Azure Maps** if available)
- **Latitude:** `silver_earthquakes[latitude]`
- **Longitude:** `silver_earthquakes[longitude]`
- **Size / Bubble size:** `silver_earthquakes[magnitude]`
- **Color saturation:** `silver_earthquakes[significance]`
- **Tooltip:** add `place`, `event_time_utc`, `depth_km`, `alert`

> This gives an interactive world map where bigger bubbles = stronger earthquakes.

---

### Visual 2 — Earthquakes Per Day (Line Chart)
- Visual type: **Line Chart**
- **X-axis:** `gold_daily_summary[event_date_utc]`
- **Y-axis:** `gold_daily_summary[event_count]`
- **Secondary Y-axis:** `gold_daily_summary[avg_magnitude]`
- Format: add markers, enable data labels

---

### Visual 3 — Magnitude Class Distribution (Bar Chart)
- Visual type: **Clustered Bar Chart**
- **Y-axis:** `gold_magnitude_distribution[magnitude_class]`
- **X-axis:** `gold_magnitude_distribution[count]`
- **Color:** format each bar a different color (minor=green, light=yellow, moderate=orange, strong=red, major=darkred)
- Sort by count descending

---

### Visual 4 — Top Regions by Earthquake Count (Horizontal Bar)
- Visual type: **Clustered Bar Chart**
- **Y-axis:** `gold_region_summary[region]`
- **X-axis:** `gold_region_summary[event_count]`
- **Secondary bar:** `gold_region_summary[avg_magnitude]`
- Apply a Top N filter: Top 15 by `event_count`

---

### Visual 5 — KPI Cards (Summary Row at the Top)
Add 5 **Card** visuals across the top of the dashboard:

| Card Label | Field |
|---|---|
| Total Earthquakes | `COUNT(silver_earthquakes[event_id])` |
| Avg Magnitude | `AVERAGE(silver_earthquakes[magnitude])` |
| Significant Events | `SUM(silver_earthquakes[is_significant])` |
| Tsunami Alerts | `SUM(silver_earthquakes[tsunami])` |
| Last Pipeline Run | `MAX(pipeline_runs[finished_at_utc])` |

---

## Step 5 — Add Slicers (Filters)

Add these **Slicer** visuals so users can filter the entire dashboard:

| Slicer | Field | Type |
|---|---|---|
| Date Range | `silver_earthquakes[event_date_utc]` | Date range slider |
| Magnitude Class | `silver_earthquakes[magnitude_class]` | Checkbox list |
| Depth Class | `silver_earthquakes[depth_class]` | Checkbox list |
| Alert Level | `silver_earthquakes[alert]` | Dropdown |
| Significant Only | `silver_earthquakes[is_significant]` | Toggle |

---

## Step 6 — Refresh Data After Pipeline Run

Whenever you run `python -m src.pipeline --days 30`, new records are written to `earthquakes.db`.

To refresh Power BI:
- Click **Refresh** in the Home ribbon (or press `F5`).
- Power BI re-reads the SQLite file via ODBC and updates all visuals instantly.

> No need to re-import — the ODBC connection is live.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `USGS_Earthquakes` not in dropdown | Make sure you created a **64-bit** DSN, not 32-bit |
| "Driver not found" error | Re-install `sqliteodbc_w64.exe` as Administrator |
| Map visual not showing | Enable **Azure Maps** in Power BI settings or use the basic Map visual |
| Data looks stale | Click **Home → Refresh** in Power BI |
