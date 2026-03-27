"""Chart loader — filter respiratory vitals from chartevents.csv.gz (3.3GB)."""

import os
import pandas as pd
from config.paths import (
    CHARTEVENTS_GZ, D_ITEMS,
    RESPIRATORY_CHART_ITEMS, RESPIRATORY_VITALS_PARQUET,
)
from data_loaders.base_loader import chunked_filter, load_small_csv, save_parquet, load_parquet


CHARTEVENTS_USECOLS = [
    "subject_id", "hadm_id", "stay_id", "charttime",
    "itemid", "value", "valuenum", "valueuom",
]

CHARTEVENTS_DTYPE = {
    "subject_id": "Int64",
    "hadm_id": "Int64",
    "stay_id": "Int64",
    "itemid": "Int64",
    "value": "str",
    "valuenum": "float64",
    "valueuom": "str",
}


def load_d_items():
    """Load d_items.csv dictionary table for ICU chartevents.

    Returns:
        pd.DataFrame with columns: itemid, label, category, etc.
    """
    print(f"Loading d_items: {D_ITEMS}")
    df = load_small_csv(D_ITEMS)
    print(f"  {len(df):,} ICU item definitions loaded")
    return df


def _filter_respiratory_items(chunk):
    """Filter function: keep rows whose itemid is in RESPIRATORY_CHART_ITEMS."""
    return chunk[chunk["itemid"].isin(RESPIRATORY_CHART_ITEMS)]


def load_respiratory_vitals(force=False):
    """Load respiratory-relevant ICU vitals from MIMIC-IV chartevents.

    Streams through chartevents.csv.gz (~3.3GB compressed), keeps only rows
    matching RESPIRATORY_CHART_ITEMS, then joins with d_items for labels.

    Args:
        force: If True, regenerate even if cached parquet exists.

    Returns:
        pd.DataFrame of respiratory vital signs with item metadata.
    """
    if os.path.exists(RESPIRATORY_VITALS_PARQUET) and not force:
        print(f"Loading cached: {RESPIRATORY_VITALS_PARQUET}")
        return load_parquet(RESPIRATORY_VITALS_PARQUET)

    print("=" * 60)
    print("Filtering respiratory vitals from chartevents.csv.gz...")
    print(f"  Source: {CHARTEVENTS_GZ}")
    print(f"  Target itemids: {len(RESPIRATORY_CHART_ITEMS)} items")
    print("=" * 60)

    # Stream and filter chartevents
    df = chunked_filter(
        filepath=CHARTEVENTS_GZ,
        filter_fn=_filter_respiratory_items,
        usecols=CHARTEVENTS_USECOLS,
        dtype=CHARTEVENTS_DTYPE,
        chunksize=500_000,
    )

    if df.empty:
        print("  WARNING: No matching chartevent rows found!")
        save_parquet(df, RESPIRATORY_VITALS_PARQUET)
        return df

    # Join with d_items for label, category, normal ranges
    d_items = load_d_items()
    join_cols = ["itemid", "label", "category", "lownormalvalue", "highnormalvalue"]
    d_items_subset = d_items[
        [c for c in join_cols if c in d_items.columns]
    ]

    df = df.merge(d_items_subset, on="itemid", how="left")

    # Parse charttime
    df["charttime"] = pd.to_datetime(df["charttime"], errors="coerce")

    print(f"\n  Final: {len(df):,} respiratory vital rows")
    print(f"  Unique patients: {df['subject_id'].nunique():,}")
    print(f"  Unique admissions: {df['hadm_id'].nunique():,}")
    print(f"  Unique ICU stays: {df['stay_id'].nunique():,}")
    print(f"  Unique vital types: {df['itemid'].nunique()}")

    save_parquet(df, RESPIRATORY_VITALS_PARQUET)
    return df


if __name__ == "__main__":
    df = load_respiratory_vitals(force=True)
    print(f"\nSample rows:")
    print(df.head(10).to_string())
    if "label" in df.columns:
        print(f"\nVital type distribution:")
        print(df["label"].value_counts().head(20))
