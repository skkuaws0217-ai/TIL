"""Radiology loader — filter chest X-ray/CT reports from radiology.csv (2.7GB)."""

import os
import re
import pandas as pd
from config.paths import (
    RADIOLOGY_NOTES, RADIOLOGY_DETAIL,
    CHEST_RADIOLOGY_PARQUET, CHEST_EXAM_PATTERNS,
)
from data_loaders.base_loader import save_parquet, load_parquet


def _build_chest_note_ids():
    """Identify note_ids corresponding to chest imaging exams.

    Loads radiology_detail.csv (292MB, fits in RAM), filters rows where
    field_name == 'exam_name' and the exam name matches any CHEST_EXAM_PATTERNS.

    Returns:
        set of note_id values for chest exams.
    """
    print(f"Loading radiology_detail: {RADIOLOGY_DETAIL}")
    detail = pd.read_csv(RADIOLOGY_DETAIL)
    print(f"  {len(detail):,} detail rows loaded")

    # Filter to exam_name field
    exam_rows = detail[detail["field_name"] == "exam_name"].copy()
    print(f"  {len(exam_rows):,} exam_name entries found")

    # Build combined regex for chest exam patterns (case insensitive)
    pattern = "|".join(re.escape(p) for p in CHEST_EXAM_PATTERNS)
    chest_mask = exam_rows["field_value"].str.contains(
        pattern, case=False, na=False, regex=True
    )

    chest_note_ids = set(exam_rows.loc[chest_mask, "note_id"].unique())
    print(f"  {len(chest_note_ids):,} chest exam note_ids identified")

    # Show sample exam names
    sample_names = exam_rows.loc[chest_mask, "field_value"].value_counts().head(10)
    print(f"  Top exam types:")
    for name, count in sample_names.items():
        print(f"    {name}: {count:,}")

    return chest_note_ids


def load_chest_radiology(force=False):
    """Load chest radiology reports from MIMIC-IV.

    Identifies chest exams via radiology_detail.csv, then streams
    radiology.csv to extract matching reports.

    Args:
        force: If True, regenerate even if cached parquet exists.

    Returns:
        pd.DataFrame of chest radiology reports.
    """
    if os.path.exists(CHEST_RADIOLOGY_PARQUET) and not force:
        print(f"Loading cached: {CHEST_RADIOLOGY_PARQUET}")
        return load_parquet(CHEST_RADIOLOGY_PARQUET)

    print("=" * 60)
    print("Filtering chest radiology reports...")
    print(f"  Notes source: {RADIOLOGY_NOTES}")
    print(f"  Detail source: {RADIOLOGY_DETAIL}")
    print("=" * 60)

    # Step 1: identify chest exam note_ids
    chest_note_ids = _build_chest_note_ids()

    if not chest_note_ids:
        print("  WARNING: No chest exam note_ids found!")
        df = pd.DataFrame()
        save_parquet(df, CHEST_RADIOLOGY_PARQUET)
        return df

    # Step 2: stream radiology.csv and filter to chest note_ids
    # radiology.csv has multi-line text fields, so use engine='python'
    print(f"\nStreaming radiology.csv to filter {len(chest_note_ids):,} chest note_ids...")

    chunks = []
    total_rows = 0
    chunk_count = 0

    for chunk in pd.read_csv(
        RADIOLOGY_NOTES,
        chunksize=5_000,
        engine="python",
    ):
        filtered = chunk[chunk["note_id"].isin(chest_note_ids)]
        if len(filtered) > 0:
            chunks.append(filtered)
            total_rows += len(filtered)

        chunk_count += 1
        if chunk_count % 100 == 0:
            print(f"  Chunk {chunk_count}: {total_rows:,} chest reports kept")

    if not chunks:
        print("  WARNING: No matching radiology reports found!")
        df = pd.DataFrame()
    else:
        df = pd.concat(chunks, ignore_index=True)

    print(f"\n  Final: {len(df):,} chest radiology reports")
    if not df.empty:
        print(f"  Unique patients: {df['subject_id'].nunique():,}")
        if "hadm_id" in df.columns:
            print(f"  Unique admissions: {df['hadm_id'].nunique():,}")

    save_parquet(df, CHEST_RADIOLOGY_PARQUET)
    return df


if __name__ == "__main__":
    df = load_chest_radiology(force=True)
    print(f"\nSample reports:")
    for _, row in df.head(3).iterrows():
        text = str(row.get("text", ""))[:200]
        print(f"  [{row['subject_id']}] note_id={row['note_id']}: {text}...")
