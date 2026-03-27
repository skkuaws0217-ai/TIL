"""Microbiology loader — filter respiratory specimens from microbiologyevents.csv."""

import os
import pandas as pd
from config.paths import (
    MICROBIOLOGYEVENTS, RESPIRATORY_SPECIMENS,
    RESPIRATORY_MICRO_PARQUET,
)
from data_loaders.base_loader import load_small_csv, save_parquet, load_parquet


MICRO_USECOLS = [
    "subject_id", "hadm_id", "chartdate",
    "spec_type_desc", "test_name", "org_name",
    "ab_name", "interpretation", "comments",
]

MICRO_KEEP_COLS = [
    "subject_id", "hadm_id", "chartdate",
    "spec_type_desc", "test_name", "org_name",
    "ab_name", "interpretation", "comments",
]


def load_respiratory_micro(force=False):
    """Load respiratory-relevant microbiology results from MIMIC-IV.

    Loads microbiologyevents.csv (~867MB, fits in RAM with usecols) and
    filters to specimens matching RESPIRATORY_SPECIMENS (case insensitive).

    Args:
        force: If True, regenerate even if cached parquet exists.

    Returns:
        pd.DataFrame of respiratory microbiology results.
    """
    if os.path.exists(RESPIRATORY_MICRO_PARQUET) and not force:
        print(f"Loading cached: {RESPIRATORY_MICRO_PARQUET}")
        return load_parquet(RESPIRATORY_MICRO_PARQUET)

    print("=" * 60)
    print("Filtering respiratory microbiology specimens...")
    print(f"  Source: {MICROBIOLOGYEVENTS}")
    print(f"  Target specimens: {RESPIRATORY_SPECIMENS}")
    print("=" * 60)

    # Load with usecols to reduce memory footprint
    available_cols = pd.read_csv(MICROBIOLOGYEVENTS, nrows=0).columns.tolist()
    usecols = [c for c in MICRO_USECOLS if c in available_cols]

    print(f"  Loading with {len(usecols)} columns...")
    df = load_small_csv(MICROBIOLOGYEVENTS, usecols=usecols)
    print(f"  {len(df):,} total microbiology rows loaded")

    # Build case-insensitive set for matching
    respiratory_specs_upper = {s.upper() for s in RESPIRATORY_SPECIMENS}

    # Filter to respiratory specimens (case insensitive)
    mask = df["spec_type_desc"].str.upper().str.strip().isin(respiratory_specs_upper)
    df = df[mask].copy()

    print(f"  {len(df):,} respiratory specimen rows after filtering")

    # Keep only required columns (that exist)
    keep_cols = [c for c in MICRO_KEEP_COLS if c in df.columns]
    df = df[keep_cols]

    # Parse chartdate
    df["chartdate"] = pd.to_datetime(df["chartdate"], errors="coerce")

    if not df.empty:
        print(f"\n  Final: {len(df):,} respiratory micro rows")
        print(f"  Unique patients: {df['subject_id'].nunique():,}")
        print(f"  Unique admissions: {df['hadm_id'].nunique():,}")
        print(f"\n  Specimen type distribution:")
        print(df["spec_type_desc"].value_counts().to_string())
        if "org_name" in df.columns:
            orgs = df["org_name"].dropna()
            if len(orgs) > 0:
                print(f"\n  Top organisms:")
                print(orgs.value_counts().head(15).to_string())
    else:
        print("  WARNING: No matching respiratory specimens found!")

    save_parquet(df, RESPIRATORY_MICRO_PARQUET)
    return df


if __name__ == "__main__":
    df = load_respiratory_micro(force=True)
    print(f"\nSample rows:")
    print(df.head(10).to_string())
