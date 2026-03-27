"""Demographics loader — patients, admissions, and diagnoses."""

import os
import pandas as pd
from config.paths import (
    PATIENTS, ADMISSIONS,
    DIAGNOSES_ICD_GZ, D_ICD_DIAGNOSES,
)
from data_loaders.base_loader import load_small_csv


def load_demographics():
    """Load patients and admissions, merge, and compute age at admission.

    Returns:
        pd.DataFrame with patient demographics and admission info,
        including computed 'age' column.
    """
    print("Loading demographics...")

    # Load patients
    print(f"  Patients: {PATIENTS}")
    patients = load_small_csv(PATIENTS)
    print(f"    {len(patients):,} patients loaded")

    # Load admissions
    print(f"  Admissions: {ADMISSIONS}")
    admissions = load_small_csv(ADMISSIONS)
    print(f"    {len(admissions):,} admissions loaded")

    # Parse datetime columns
    admissions["admittime"] = pd.to_datetime(admissions["admittime"], errors="coerce")
    admissions["dischtime"] = pd.to_datetime(admissions["dischtime"], errors="coerce")
    admissions["deathtime"] = pd.to_datetime(admissions["deathtime"], errors="coerce")

    # Merge on subject_id
    df = admissions.merge(patients, on="subject_id", how="left")

    # Compute age at admission
    # MIMIC-IV stores anchor_age and anchor_year; actual age = anchor_age + (admit_year - anchor_year)
    if "anchor_age" in df.columns and "anchor_year" in df.columns:
        df["age"] = df["anchor_age"] + (df["admittime"].dt.year - df["anchor_year"])
    else:
        # Fallback: compute from dob if available
        if "dob" in df.columns:
            df["dob"] = pd.to_datetime(df["dob"], errors="coerce")
            df["age"] = (
                (df["admittime"] - df["dob"]).dt.days / 365.25
            ).round(1)
        else:
            df["age"] = None

    # Compute length of stay in days
    df["los_days"] = (df["dischtime"] - df["admittime"]).dt.total_seconds() / 86400

    print(f"\n  Merged: {len(df):,} admission records")
    print(f"  Unique patients: {df['subject_id'].nunique():,}")
    if "age" in df.columns and df["age"].notna().any():
        print(f"  Age range: {df['age'].min():.0f} - {df['age'].max():.0f}")
        print(f"  Median age: {df['age'].median():.0f}")

    return df


def load_diagnoses():
    """Load diagnoses_icd with full ICD code titles.

    Merges diagnoses_icd.csv.gz with d_icd_diagnoses.csv to get
    human-readable diagnosis titles.

    Returns:
        pd.DataFrame with subject_id, hadm_id, seq_num, icd_code,
        icd_version, long_title.
    """
    print("Loading diagnoses...")

    # Load diagnoses
    print(f"  Diagnoses: {DIAGNOSES_ICD_GZ}")
    diagnoses = load_small_csv(DIAGNOSES_ICD_GZ, dtype={"icd_code": str})
    print(f"    {len(diagnoses):,} diagnosis entries loaded")

    # Load ICD dictionary
    print(f"  ICD dictionary: {D_ICD_DIAGNOSES}")
    d_icd = load_small_csv(D_ICD_DIAGNOSES, dtype={"icd_code": str})
    print(f"    {len(d_icd):,} ICD code definitions loaded")

    # Merge for full titles
    df = diagnoses.merge(
        d_icd[["icd_code", "icd_version", "long_title"]],
        on=["icd_code", "icd_version"],
        how="left",
    )

    print(f"\n  Merged: {len(df):,} diagnosis records with titles")
    print(f"  Unique patients: {df['subject_id'].nunique():,}")
    print(f"  Unique ICD codes: {df['icd_code'].nunique():,}")
    print(f"  Missing titles: {df['long_title'].isna().sum():,}")

    return df


if __name__ == "__main__":
    print("=" * 60)
    print("DEMOGRAPHICS")
    print("=" * 60)
    demo = load_demographics()
    print(demo.head(5).to_string())

    print("\n" + "=" * 60)
    print("DIAGNOSES")
    print("=" * 60)
    diag = load_diagnoses()
    print(diag.head(10).to_string())
    print("\nTop 20 diagnoses:")
    print(diag["long_title"].value_counts().head(20))
