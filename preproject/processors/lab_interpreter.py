"""Lab value interpreter — convert numeric values to medical terms (hyper/hypo).

Reference ranges and medical terminology are loaded from:
  config/lab_reference_ranges.yaml

All ranges sourced from:
  - Tietz Clinical Guide to Laboratory Tests, 6th Ed (2018)
  - Harrison's Principles of Internal Medicine, 21st Ed (2022)
  - WHO/IFCC Reference Intervals (2020)
  - Disease-specific guidelines (ESC, ADA, KDIGO, etc.)

See lab_reference_ranges.yaml for per-item citations.
"""

import os
import yaml
import pandas as pd
from typing import Optional


def _load_lab_references():
    """Load reference ranges from YAML (교과서 근거 기반)."""
    yaml_path = os.path.join(
        os.path.dirname(__file__), "..", "config", "lab_reference_ranges.yaml"
    )
    try:
        with open(yaml_path, "r") as f:
            raw = yaml.safe_load(f)
    except Exception:
        raw = {}

    terms = {}
    for itemid, info in raw.items():
        if not isinstance(info, dict):
            continue
        itemid = int(itemid)
        ranges = info.get("ranges", {})
        critical = info.get("critical", {})
        med_terms = info.get("medical_terms", {})
        terms[itemid] = {
            "name": info.get("name", ""),
            "unit": info.get("unit", ""),
            "ref_source": info.get("ref_source", ""),
            "default_lower": ranges.get("lower"),
            "default_upper": ranges.get("upper"),
            "critical_low": critical.get("low"),
            "critical_high": critical.get("high"),
            "low_term": med_terms.get("low", "Below reference"),
            "high_term": med_terms.get("high", "Above reference"),
            "critical_low_term": med_terms.get("critical_low", ""),
            "critical_high_term": med_terms.get("critical_high", ""),
            "note": info.get("note", ""),
        }
    return terms


LAB_MEDICAL_TERMS = _load_lab_references()

# ─── Hardcoded fallback (only if YAML fails to load) ───
if not LAB_MEDICAL_TERMS:
    LAB_MEDICAL_TERMS = {
    # Blood Gas
    50821: {"name": "pO2", "unit": "mmHg", "low_term": "Hypoxemia", "high_term": "Hyperoxemia",
            "default_lower": 80, "default_upper": 100, "critical_low": 60},
    50818: {"name": "pCO2", "unit": "mmHg", "low_term": "Hypocapnia", "high_term": "Hypercapnia",
            "default_lower": 35, "default_upper": 45, "critical_high": 60},
    50820: {"name": "pH", "unit": "", "low_term": "Acidemia", "high_term": "Alkalemia",
            "default_lower": 7.35, "default_upper": 7.45, "critical_low": 7.20, "critical_high": 7.55},
    50803: {"name": "Bicarbonate", "unit": "mEq/L", "low_term": "Metabolic Acidosis (low HCO3)",
            "high_term": "Metabolic Alkalosis (high HCO3)", "default_lower": 22, "default_upper": 28},
    50802: {"name": "Base Excess", "unit": "mEq/L", "low_term": "Base Deficit", "high_term": "Base Excess",
            "default_lower": -2, "default_upper": 2},
    50813: {"name": "Lactate", "unit": "mmol/L", "low_term": "Normal Lactate", "high_term": "Hyperlactatemia",
            "default_lower": 0.5, "default_upper": 2.0, "critical_high": 4.0},
    50801: {"name": "A-a Gradient", "unit": "mmHg", "low_term": "Normal", "high_term": "Elevated A-a Gradient",
            "default_lower": 0, "default_upper": 20},
    50825: {"name": "O2 Saturation", "unit": "%", "low_term": "Desaturation", "high_term": "Normal",
            "default_lower": 95, "default_upper": 100, "critical_low": 90},

    # CBC
    51301: {"name": "WBC", "unit": "K/uL", "low_term": "Leukopenia", "high_term": "Leukocytosis",
            "default_lower": 4.0, "default_upper": 11.0, "critical_high": 30.0},
    51300: {"name": "WBC", "unit": "K/uL", "low_term": "Leukopenia", "high_term": "Leukocytosis",
            "default_lower": 4.0, "default_upper": 11.0},
    51222: {"name": "Hemoglobin", "unit": "g/dL", "low_term": "Anemia", "high_term": "Polycythemia",
            "default_lower": 12.0, "default_upper": 17.5},
    51265: {"name": "Platelet Count", "unit": "K/uL", "low_term": "Thrombocytopenia", "high_term": "Thrombocytosis",
            "default_lower": 150, "default_upper": 400},
    51256: {"name": "Neutrophils %", "unit": "%", "low_term": "Neutropenia", "high_term": "Neutrophilia",
            "default_lower": 40, "default_upper": 70},
    51200: {"name": "Eosinophils %", "unit": "%", "low_term": "Normal", "high_term": "Eosinophilia",
            "default_lower": 0, "default_upper": 5},
    51244: {"name": "Lymphocytes %", "unit": "%", "low_term": "Lymphopenia", "high_term": "Lymphocytosis",
            "default_lower": 20, "default_upper": 40},
    51146: {"name": "Basophils %", "unit": "%", "low_term": "Normal", "high_term": "Basophilia",
            "default_lower": 0, "default_upper": 1},
    51279: {"name": "RBC", "unit": "M/uL", "low_term": "Erythrocytopenia", "high_term": "Erythrocytosis",
            "default_lower": 4.2, "default_upper": 5.9},
    51277: {"name": "RDW", "unit": "%", "low_term": "Normal", "high_term": "Anisocytosis (elevated RDW)",
            "default_lower": 11.5, "default_upper": 14.5},

    # Inflammatory Markers
    50889: {"name": "CRP", "unit": "mg/L", "low_term": "Normal", "high_term": "Elevated CRP (inflammation)",
            "default_lower": 0, "default_upper": 10, "critical_high": 100},
    51652: {"name": "Procalcitonin", "unit": "ng/mL", "low_term": "Normal",
            "high_term": "Elevated Procalcitonin (bacterial infection likely)",
            "default_lower": 0, "default_upper": 0.5, "critical_high": 2.0},

    # Chemistry
    50862: {"name": "Albumin", "unit": "g/dL", "low_term": "Hypoalbuminemia", "high_term": "Hyperalbuminemia",
            "default_lower": 3.5, "default_upper": 5.5},
    50882: {"name": "Bicarbonate (Serum)", "unit": "mEq/L", "low_term": "Low Bicarbonate",
            "high_term": "High Bicarbonate", "default_lower": 22, "default_upper": 29},
    50912: {"name": "Creatinine", "unit": "mg/dL", "low_term": "Low Creatinine",
            "high_term": "Elevated Creatinine (renal impairment)", "default_lower": 0.6, "default_upper": 1.2},
    50931: {"name": "Glucose", "unit": "mg/dL", "low_term": "Hypoglycemia", "high_term": "Hyperglycemia",
            "default_lower": 70, "default_upper": 100, "critical_low": 50, "critical_high": 400},
    50960: {"name": "Magnesium", "unit": "mg/dL", "low_term": "Hypomagnesemia", "high_term": "Hypermagnesemia",
            "default_lower": 1.7, "default_upper": 2.2},
    50971: {"name": "Potassium", "unit": "mEq/L", "low_term": "Hypokalemia", "high_term": "Hyperkalemia",
            "default_lower": 3.5, "default_upper": 5.0, "critical_low": 3.0, "critical_high": 6.0},
    50983: {"name": "Sodium", "unit": "mEq/L", "low_term": "Hyponatremia", "high_term": "Hypernatremia",
            "default_lower": 136, "default_upper": 145},
    50954: {"name": "LDH", "unit": "IU/L", "low_term": "Normal", "high_term": "Elevated LDH",
            "default_lower": 100, "default_upper": 250},

    # Coagulation
    51214: {"name": "PT", "unit": "sec", "low_term": "Normal", "high_term": "Prolonged PT (coagulopathy)",
            "default_lower": 11, "default_upper": 13.5},
    51237: {"name": "INR", "unit": "", "low_term": "Normal", "high_term": "Elevated INR",
            "default_lower": 0.9, "default_upper": 1.1},
    51196: {"name": "D-Dimer", "unit": "ng/mL FEU", "low_term": "Normal",
            "high_term": "Elevated D-dimer (thrombotic risk)", "default_lower": 0, "default_upper": 500},

    # Cardiac
    51003: {"name": "Troponin T", "unit": "ng/mL", "low_term": "Normal",
            "high_term": "Elevated Troponin (myocardial injury)",
            "default_lower": 0, "default_upper": 0.01, "critical_high": 0.1},
    51002: {"name": "Troponin I", "unit": "ng/mL", "low_term": "Normal",
            "high_term": "Elevated Troponin (myocardial injury)",
            "default_lower": 0, "default_upper": 0.04},
    50963: {"name": "BNP", "unit": "pg/mL", "low_term": "Normal",
            "high_term": "Elevated BNP (cardiac strain/fluid overload)",
            "default_lower": 0, "default_upper": 100, "critical_high": 500},
    50911: {"name": "NT-proBNP", "unit": "pg/mL", "low_term": "Normal",
            "high_term": "Elevated NT-proBNP (heart failure)",
            "default_lower": 0, "default_upper": 300, "critical_high": 900},
}

# ─── Text result interpretation ───
TEXT_INTERPRETATIONS = {
    "neg": ("Normal", "Negative"),
    "negative": ("Normal", "Negative"),
    "pos": ("Abnormal", "Positive"),
    "positive": ("Abnormal", "Positive"),
    "detected": ("Abnormal", "Detected"),
    "not detected": ("Normal", "Not Detected"),
    "reactive": ("Abnormal", "Reactive"),
    "nonreactive": ("Normal", "Nonreactive"),
    "non-reactive": ("Normal", "Non-reactive"),
    "normal": ("Normal", "Normal"),
    "abnormal": ("Abnormal", "Abnormal"),
    "few": ("Borderline", "Few"),
    "many": ("Abnormal", "Many"),
    "moderate": ("Abnormal", "Moderate"),
    "rare": ("Borderline", "Rare"),
    "none": ("Normal", "None"),
    "none seen": ("Normal", "None seen"),
    "present": ("Abnormal", "Present"),
    "absent": ("Normal", "Absent"),
}


def interpret_lab_value(itemid: int, value: str, valuenum: Optional[float],
                        ref_lower: Optional[float], ref_upper: Optional[float],
                        flag: str = "") -> dict:
    """Interpret a single lab result.

    Returns:
        dict with keys: interpretation, medical_term, severity, reference_source, memo
    """
    result = {
        "interpretation": "",
        "medical_term": "",
        "severity": "normal",
        "reference_source": "",
        "ref_range": "",        # 소견서에 표시될 정상범위
        "ref_lower": None,
        "ref_upper": None,
        "memo": "",
    }

    term_info = LAB_MEDICAL_TERMS.get(itemid, {})
    unit = term_info.get("unit", "")

    # ─── Case 1: Numeric value with MIMIC-provided reference range (우선 적용) ───
    if valuenum is not None and ref_lower is not None and ref_upper is not None:
        result["reference_source"] = "mimic_provided"
        result["ref_lower"] = ref_lower
        result["ref_upper"] = ref_upper
        result["ref_range"] = f"{ref_lower}-{ref_upper}"

        if valuenum < ref_lower:
            result["interpretation"] = "Low"
            result["medical_term"] = term_info.get("low_term", f"Below reference ({valuenum} < {ref_lower})")
            result["severity"] = "abnormal_low"
            if term_info.get("critical_low") and valuenum < term_info["critical_low"]:
                result["severity"] = "critical_low"
                result["memo"] = f"CRITICAL: {valuenum} below critical threshold {term_info['critical_low']}"
        elif valuenum > ref_upper:
            result["interpretation"] = "High"
            result["medical_term"] = term_info.get("high_term", f"Above reference ({valuenum} > {ref_upper})")
            result["severity"] = "abnormal_high"
            if term_info.get("critical_high") and valuenum > term_info["critical_high"]:
                result["severity"] = "critical_high"
                result["memo"] = f"CRITICAL: {valuenum} above critical threshold {term_info['critical_high']}"
        else:
            result["interpretation"] = "Normal"
            result["medical_term"] = "Within reference range"
            result["severity"] = "normal"
        return result

    # ─── Case 2: Numeric value, no MIMIC reference → official standard fallback ───
    if valuenum is not None and term_info:
        default_lower = term_info.get("default_lower")
        default_upper = term_info.get("default_upper")
        if default_lower is not None and default_upper is not None:
            result["reference_source"] = "official_standard"
            result["ref_lower"] = default_lower
            result["ref_upper"] = default_upper
            result["ref_range"] = f"{default_lower}-{default_upper}"
            ref_src_cite = term_info.get("ref_source", "")
            result["memo"] = f"Ref: {ref_src_cite}" if ref_src_cite else f"Official standard: {default_lower}-{default_upper} {unit}"

            if valuenum < default_lower:
                result["interpretation"] = "Low"
                result["medical_term"] = term_info.get("low_term", "Below normal")
                result["severity"] = "abnormal_low"
                if term_info.get("critical_low") and valuenum < term_info["critical_low"]:
                    result["severity"] = "critical_low"
            elif valuenum > default_upper:
                result["interpretation"] = "High"
                result["medical_term"] = term_info.get("high_term", "Above normal")
                result["severity"] = "abnormal_high"
                if term_info.get("critical_high") and valuenum > term_info["critical_high"]:
                    result["severity"] = "critical_high"
            else:
                result["interpretation"] = "Normal"
                result["medical_term"] = "Within reference range"
                result["severity"] = "normal"
            return result

    # ─── Case 3: Text value ───
    if value and isinstance(value, str):
        value_clean = value.strip().lower().replace("___", "")
        if not value_clean:
            result["interpretation"] = "De-identified"
            result["memo"] = "Value de-identified (___)"
            return result

        # Check known text patterns
        for pattern, (status, desc) in TEXT_INTERPRETATIONS.items():
            if value_clean == pattern or value_clean.startswith(pattern):
                result["interpretation"] = status
                result["medical_term"] = desc
                result["severity"] = "abnormal" if status == "Abnormal" else "normal"
                result["reference_source"] = "text_standard"
                return result

        # Numeric-like text: ">1.00", "<0.01"
        if value_clean.startswith(">") or value_clean.startswith("<"):
            result["interpretation"] = value
            result["medical_term"] = value
            result["reference_source"] = "text_value"
            result["memo"] = "Threshold value — interpret in clinical context"
            return result

        # Keep text as-is
        result["interpretation"] = value
        result["medical_term"] = value
        result["reference_source"] = "text_raw"
        result["memo"] = "Text result — kept as original"

    # ─── Case 4: Flag only ───
    if flag and flag.lower() == "abnormal":
        result["interpretation"] = "Abnormal"
        result["severity"] = "abnormal"
        result["memo"] = "Flagged abnormal by MIMIC (no further detail)"
        result["reference_source"] = "mimic_flag"

    return result


def interpret_lab_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Interpret all lab results in a DataFrame.

    Expects columns: itemid, value, valuenum, ref_range_lower, ref_range_upper, flag
    Adds columns: interpretation, medical_term, severity, reference_source, memo
    """
    interpretations = []
    for _, row in df.iterrows():
        interp = interpret_lab_value(
            itemid=int(row.get("itemid", 0)),
            value=str(row.get("value", "")),
            valuenum=row.get("valuenum") if pd.notna(row.get("valuenum")) else None,
            ref_lower=row.get("ref_range_lower") if pd.notna(row.get("ref_range_lower")) else None,
            ref_upper=row.get("ref_range_upper") if pd.notna(row.get("ref_range_upper")) else None,
            flag=str(row.get("flag", "")),
        )
        interpretations.append(interp)

    interp_df = pd.DataFrame(interpretations)
    return pd.concat([df.reset_index(drop=True), interp_df], axis=1)
