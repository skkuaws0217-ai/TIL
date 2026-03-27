"""Lab loader — filter lung-relevant labs from labevents.csv (17GB)."""

import os
import pandas as pd
from config.paths import LABEVENTS_GZ, D_LABITEMS, LUNG_LAB_ITEMIDS, LUNG_LABS_PARQUET
from data_loaders.base_loader import chunked_filter, load_small_csv, save_parquet, load_parquet


LABEVENTS_USECOLS = [
    "subject_id", "hadm_id", "itemid", "charttime",
    "value", "valuenum", "valueuom",
    "ref_range_lower", "ref_range_upper", "flag",
]

LABEVENTS_DTYPE = {
    "subject_id": "Int64",
    "hadm_id": "Int64",
    "itemid": "Int64",
    "value": "str",
    "valuenum": "float64",
    "valueuom": "str",
    "ref_range_lower": "float64",
    "ref_range_upper": "float64",
    "flag": "str",
}


def load_d_labitems():
    """Load d_labitems.csv dictionary table.

    Returns:
        pd.DataFrame with columns: itemid, label, fluid, category, loinc_code
    """
    print(f"Loading d_labitems: {D_LABITEMS}")
    df = load_small_csv(D_LABITEMS)
    print(f"  {len(df):,} lab item definitions loaded")
    return df


def _filter_lung_labs(chunk):
    """Filter function: keep rows whose itemid is in LUNG_LAB_ITEMIDS."""
    return chunk[chunk["itemid"].isin(LUNG_LAB_ITEMIDS)]


def load_lung_labs(force=False):
    """Load lung-relevant lab results from MIMIC-IV labevents.

    Streams through labevents.csv.gz (~17GB compressed), keeps only rows
    matching LUNG_LAB_ITEMIDS, then joins with d_labitems for labels.

    Args:
        force: If True, regenerate even if cached parquet exists.

    Returns:
        pd.DataFrame of lung-relevant lab results with item metadata.
    """
    if os.path.exists(LUNG_LABS_PARQUET) and not force:
        print(f"Loading cached: {LUNG_LABS_PARQUET}")
        return load_parquet(LUNG_LABS_PARQUET)

    print("=" * 60)
    print("Filtering lung-relevant labs from labevents.csv.gz...")
    print(f"  Source: {LABEVENTS_GZ}")
    print(f"  Target itemids: {len(LUNG_LAB_ITEMIDS)} items")
    print("=" * 60)

    # Stream and filter labevents
    df = chunked_filter(
        filepath=LABEVENTS_GZ,
        filter_fn=_filter_lung_labs,
        usecols=LABEVENTS_USECOLS,
        dtype=LABEVENTS_DTYPE,
        chunksize=500_000,
    )

    if df.empty:
        print("  WARNING: No matching lab rows found!")
        save_parquet(df, LUNG_LABS_PARQUET)
        return df

    # Join with d_labitems for label, fluid, category
    d_labitems = load_d_labitems()
    join_cols = ["itemid", "label", "fluid", "category"]
    d_labitems_subset = d_labitems[
        [c for c in join_cols if c in d_labitems.columns]
    ]

    df = df.merge(d_labitems_subset, on="itemid", how="left")

    # Parse charttime
    df["charttime"] = pd.to_datetime(df["charttime"], errors="coerce")

    print(f"\n  Final: {len(df):,} lung-relevant lab rows")
    print(f"  Unique patients: {df['subject_id'].nunique():,}")
    print(f"  Unique admissions: {df['hadm_id'].nunique():,}")
    print(f"  Unique lab types: {df['itemid'].nunique()}")

    save_parquet(df, LUNG_LABS_PARQUET)
    return df


if __name__ == "__main__":
    df = load_lung_labs(force=True)
    print(f"\nSample rows:")
    print(df.head(10).to_string())
    if "label" in df.columns:
        print(f"\nLab type distribution:")
        print(df["label"].value_counts().head(20))
