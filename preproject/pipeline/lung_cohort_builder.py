"""Lung cohort builder — identify all admissions with lung-relevant conditions.

Inclusion criteria (OR logic):
  1. ICD-10 codes in lung-relevant chapters:
     - J00-J99: Diseases of the respiratory system
     - A15-A19: Tuberculosis
     - C34:     Malignant neoplasm of bronchus and lung
     - I26:     Pulmonary embolism
  2. Has chest imaging in radiology data
"""

import os
import re
import pandas as pd
from config.paths import (
    DIAGNOSES_ICD_GZ, D_ICD_DIAGNOSES,
    CHEST_RADIOLOGY_PARQUET, LUNG_COHORT_PARQUET,
)
from data_loaders.base_loader import load_small_csv, save_parquet, load_parquet


# ICD-10 patterns for lung-relevant diagnoses
LUNG_ICD10_PATTERNS = [
    r"^J",           # J00-J99: Diseases of the respiratory system
    r"^A1[5-9]",     # A15-A19: Tuberculosis
    r"^C34",         # C34: Malignant neoplasm of bronchus and lung
    r"^I26",         # I26: Pulmonary embolism
]

# ICD-9 equivalents for older codes (MIMIC-IV has both versions)
LUNG_ICD9_PATTERNS = [
    r"^46[0-9]",     # Upper respiratory infections
    r"^4[7-9][0-9]", # Other diseases of respiratory system (470-519)
    r"^0[01][0-8]",  # Tuberculosis (010-018)
    r"^162",         # Malignant neoplasm of trachea, bronchus, lung
    r"^415\.1",      # Pulmonary embolism
]


def _is_lung_icd10(code):
    """Check if an ICD-10 code is lung-relevant."""
    code = str(code).strip().upper()
    return any(re.match(p, code) for p in LUNG_ICD10_PATTERNS)


def _is_lung_icd9(code):
    """Check if an ICD-9 code is lung-relevant."""
    code = str(code).strip()
    return any(re.match(p, code) for p in LUNG_ICD9_PATTERNS)


def _get_lung_diagnoses():
    """Identify (subject_id, hadm_id) pairs with lung-related ICD codes.

    Returns:
        pd.DataFrame with subject_id, hadm_id, and diagnosis metadata.
    """
    print("Step 1: Identifying lung-related ICD diagnoses...")

    # Load diagnoses
    diagnoses = load_small_csv(DIAGNOSES_ICD_GZ, dtype={"icd_code": str})
    print(f"  {len(diagnoses):,} total diagnosis entries")

    # Load ICD dictionary for titles
    d_icd = load_small_csv(D_ICD_DIAGNOSES, dtype={"icd_code": str})

    # Separate ICD-9 and ICD-10
    icd9 = diagnoses[diagnoses["icd_version"] == 9].copy()
    icd10 = diagnoses[diagnoses["icd_version"] == 10].copy()

    # Apply lung filters
    icd10_mask = icd10["icd_code"].apply(_is_lung_icd10)
    icd9_mask = icd9["icd_code"].apply(_is_lung_icd9)

    lung_icd10 = icd10[icd10_mask]
    lung_icd9 = icd9[icd9_mask]

    print(f"  Lung ICD-10 matches: {len(lung_icd10):,}")
    print(f"  Lung ICD-9 matches: {len(lung_icd9):,}")

    lung_diag = pd.concat([lung_icd10, lung_icd9], ignore_index=True)

    # Merge for titles
    lung_diag = lung_diag.merge(
        d_icd[["icd_code", "icd_version", "long_title"]],
        on=["icd_code", "icd_version"],
        how="left",
    )

    # Get unique (subject_id, hadm_id) pairs
    lung_pairs = lung_diag[["subject_id", "hadm_id"]].drop_duplicates()
    lung_pairs["source_diagnosis"] = True

    print(f"  Unique (subject, admission) pairs from diagnoses: {len(lung_pairs):,}")

    # Show top diagnoses
    if "long_title" in lung_diag.columns:
        print(f"\n  Top lung diagnoses:")
        top = lung_diag["long_title"].value_counts().head(15)
        for title, count in top.items():
            print(f"    {title}: {count:,}")

    return lung_pairs, lung_diag


def _get_chest_imaging_pairs():
    """Identify (subject_id, hadm_id) pairs with chest imaging.

    Returns:
        pd.DataFrame with subject_id, hadm_id from chest radiology.
    """
    print("\nStep 2: Identifying admissions with chest imaging...")

    if not os.path.exists(CHEST_RADIOLOGY_PARQUET):
        print(f"  WARNING: {CHEST_RADIOLOGY_PARQUET} not found.")
        print("  Run radiology_loader.py first to generate chest radiology parquet.")
        return pd.DataFrame(columns=["subject_id", "hadm_id", "source_imaging"])

    chest = load_parquet(CHEST_RADIOLOGY_PARQUET)
    print(f"  {len(chest):,} chest radiology reports loaded")

    if chest.empty:
        return pd.DataFrame(columns=["subject_id", "hadm_id", "source_imaging"])

    # Get unique (subject_id, hadm_id) pairs
    cols = ["subject_id"]
    if "hadm_id" in chest.columns:
        cols.append("hadm_id")

    imaging_pairs = chest[cols].drop_duplicates()
    if "hadm_id" not in imaging_pairs.columns:
        imaging_pairs["hadm_id"] = pd.NA

    imaging_pairs["source_imaging"] = True

    print(f"  Unique (subject, admission) pairs from imaging: {len(imaging_pairs):,}")
    return imaging_pairs


def build_lung_cohort(force=False):
    """Build the lung-relevant cohort by combining diagnosis and imaging criteria.

    Inclusion (OR logic):
      - Has a lung-related ICD diagnosis code
      - Has chest imaging in radiology

    Args:
        force: If True, regenerate even if cached.

    Returns:
        pd.DataFrame with subject_id, hadm_id, source_diagnosis, source_imaging.
    """
    if os.path.exists(LUNG_COHORT_PARQUET) and not force:
        print(f"Loading cached: {LUNG_COHORT_PARQUET}")
        return load_parquet(LUNG_COHORT_PARQUET)

    print("=" * 60)
    print("Building lung-relevant cohort")
    print("=" * 60)

    # Step 1: diagnosis-based pairs
    diag_pairs, lung_diag_detail = _get_lung_diagnoses()

    # Step 2: imaging-based pairs
    imaging_pairs = _get_chest_imaging_pairs()

    # Combine with OR logic (outer merge)
    print("\nStep 3: Combining criteria (OR logic)...")

    if imaging_pairs.empty:
        cohort = diag_pairs.copy()
        cohort["source_imaging"] = False
    elif diag_pairs.empty:
        cohort = imaging_pairs.copy()
        cohort["source_diagnosis"] = False
    else:
        cohort = diag_pairs.merge(
            imaging_pairs,
            on=["subject_id", "hadm_id"],
            how="outer",
        )

    # Fill NaN source flags with False
    cohort["source_diagnosis"] = cohort["source_diagnosis"].fillna(False).astype(bool)
    cohort["source_imaging"] = cohort["source_imaging"].fillna(False).astype(bool)

    # Drop duplicates
    cohort = cohort.drop_duplicates(subset=["subject_id", "hadm_id"])

    # Summary statistics
    both = cohort["source_diagnosis"] & cohort["source_imaging"]
    diag_only = cohort["source_diagnosis"] & ~cohort["source_imaging"]
    img_only = ~cohort["source_diagnosis"] & cohort["source_imaging"]

    print(f"\n{'=' * 40}")
    print(f"  LUNG COHORT SUMMARY")
    print(f"{'=' * 40}")
    print(f"  Total unique admissions: {len(cohort):,}")
    print(f"  Total unique patients:   {cohort['subject_id'].nunique():,}")
    print(f"  Diagnosis only:          {diag_only.sum():,}")
    print(f"  Imaging only:            {img_only.sum():,}")
    print(f"  Both diagnosis + imaging: {both.sum():,}")

    save_parquet(cohort, LUNG_COHORT_PARQUET)
    return cohort


if __name__ == "__main__":
    cohort = build_lung_cohort(force=True)
    print(f"\nCohort shape: {cohort.shape}")
    print(cohort.head(10).to_string())
