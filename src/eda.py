"""
src/eda.py
----------
Phase 2: Exploratory Data Analysis.

Generates 6 matplotlib/seaborn plots saved to docs/eda_plots/:
  1. Magnitude distribution histogram
  2. Depth distribution by depth class
  3. Earthquakes per day (time series)
  4. Top 15 regions by count (horizontal bar)
  5. Correlation heatmap of numeric features
  6. Significance score vs Magnitude scatter

Usage:
    python -m src.eda
    python -m src.eda --show   # also opens interactive windows
"""

import argparse
import logging
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd
import seaborn as sns

from src.settings import DB_PATH

logger = logging.getLogger(__name__)

PLOTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "eda_plots"

sns.set_theme(style="darkgrid", palette="viridis")
PALETTE = {
    "minor": "#2ecc71",
    "light": "#f1c40f",
    "moderate": "#e67e22",
    "strong": "#e74c3c",
    "major": "#8e44ad",
}


def _load(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT magnitude, depth_km, latitude, longitude,
               magnitude_class, depth_class, significance,
               is_significant, energy_joules, felt_reports,
               event_date_utc, region, alert
        FROM silver_earthquakes
        ORDER BY event_date_utc
        """,
        conn,
    )
    df["event_date_utc"] = pd.to_datetime(df["event_date_utc"])
    return df


def plot_magnitude_histogram(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    order = ["minor", "light", "moderate", "strong", "major"]
    colors = [PALETTE[c] for c in order if c in df["magnitude_class"].unique()]
    sns.histplot(
        data=df, x="magnitude", hue="magnitude_class",
        hue_order=[c for c in order if c in df["magnitude_class"].unique()],
        palette=PALETTE, bins=40, ax=ax, edgecolor="white", linewidth=0.3,
    )
    ax.set_title("Magnitude Distribution by Class", fontsize=14, fontweight="bold")
    ax.set_xlabel("Magnitude (Richter)")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(out_dir / "01_magnitude_histogram.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 01_magnitude_histogram.png")


def plot_depth_boxplot(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    order = ["shallow", "intermediate", "deep"]
    sns.boxplot(
        data=df, x="depth_class", y="depth_km",
        order=[o for o in order if o in df["depth_class"].unique()],
        palette="coolwarm", ax=ax,
    )
    ax.set_title("Depth (km) by Depth Class", fontsize=14, fontweight="bold")
    ax.set_xlabel("Depth Class")
    ax.set_ylabel("Depth (km)")
    fig.tight_layout()
    fig.savefig(out_dir / "02_depth_boxplot.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 02_depth_boxplot.png")


def plot_daily_timeseries(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    daily = df.groupby("event_date_utc").agg(
        count=("magnitude", "count"),
        avg_mag=("magnitude", "mean"),
    ).reset_index()

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.fill_between(daily["event_date_utc"], daily["count"],
                     alpha=0.4, color="#3498db")
    ax1.plot(daily["event_date_utc"], daily["count"],
             color="#2980b9", linewidth=1.5, label="Daily Count")
    ax1.set_ylabel("Earthquake Count", color="#2980b9")
    ax1.tick_params(axis="y", labelcolor="#2980b9")

    ax2 = ax1.twinx()
    ax2.plot(daily["event_date_utc"], daily["avg_mag"],
             color="#e74c3c", linewidth=1.5, linestyle="--", label="Avg Magnitude")
    ax2.set_ylabel("Avg Magnitude", color="#e74c3c")
    ax2.tick_params(axis="y", labelcolor="#e74c3c")

    ax1.set_title("Earthquakes Per Day", fontsize=14, fontweight="bold")
    ax1.set_xlabel("Date (UTC)")
    fig.legend(loc="upper left", bbox_to_anchor=(0.08, 0.92))
    fig.tight_layout()
    fig.savefig(out_dir / "03_daily_timeseries.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 03_daily_timeseries.png")


def plot_top_regions(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    top = (
        df["region"].value_counts().head(15).reset_index()
    )
    top.columns = ["region", "count"]

    fig, ax = plt.subplots(figsize=(10, 7))
    sns.barplot(data=top, y="region", x="count", palette="Blues_r", ax=ax)
    ax.set_title("Top 15 Regions by Earthquake Count", fontsize=14, fontweight="bold")
    ax.set_xlabel("Count")
    ax.set_ylabel("Region")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    fig.savefig(out_dir / "04_top_regions.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 04_top_regions.png")


def plot_correlation_heatmap(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    numeric_cols = [
        "magnitude", "depth_km", "latitude", "longitude",
        "significance", "felt_reports", "energy_joules",
    ]
    corr = df[numeric_cols].dropna(how="all").corr()

    fig, ax = plt.subplots(figsize=(9, 7))
    mask = pd.DataFrame(False, index=corr.index, columns=corr.columns)
    # mask upper triangle
    for i in range(len(corr.columns)):
        for j in range(i):
            mask.iloc[i, j] = False
        for j in range(i + 1, len(corr.columns)):
            mask.iloc[i, j] = True

    sns.heatmap(
        corr, mask=mask, annot=True, fmt=".2f", cmap="RdYlGn",
        center=0, linewidths=0.5, ax=ax, square=True,
    )
    ax.set_title("Correlation Heatmap — Numeric Features", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(out_dir / "05_correlation_heatmap.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 05_correlation_heatmap.png")


def plot_significance_scatter(df: pd.DataFrame, out_dir: Path, show: bool) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = df["is_significant"].map({1: "#e74c3c", 0: "#95a5a6"})
    ax.scatter(
        df["magnitude"], df["significance"],
        c=colors, alpha=0.4, s=15, edgecolors="none",
    )
    ax.axhline(500, color="#e74c3c", linestyle="--", linewidth=1,
               label="Significance threshold (500)")
    ax.set_title("Significance Score vs Magnitude", fontsize=14, fontweight="bold")
    ax.set_xlabel("Magnitude")
    ax.set_ylabel("Significance Score")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "06_significance_scatter.png", dpi=150)
    if show:
        plt.show()
    plt.close(fig)
    logger.info("Saved 06_significance_scatter.png")


def run_eda(show: bool = False) -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        df = _load(conn)
        logger.info("Loaded %d records for EDA.", len(df))

        plot_magnitude_histogram(df, PLOTS_DIR, show)
        plot_depth_boxplot(df, PLOTS_DIR, show)
        plot_daily_timeseries(df, PLOTS_DIR, show)
        plot_top_regions(df, PLOTS_DIR, show)
        plot_correlation_heatmap(df, PLOTS_DIR, show)
        plot_significance_scatter(df, PLOTS_DIR, show)

        print(f"\n✅ All 6 EDA plots saved to: {PLOTS_DIR}\n")
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="USGS EDA — generate analysis plots")
    parser.add_argument("--show", action="store_true",
                        help="Open interactive plot windows after saving")
    args = parser.parse_args()
    run_eda(show=args.show)


if __name__ == "__main__":
    main()
