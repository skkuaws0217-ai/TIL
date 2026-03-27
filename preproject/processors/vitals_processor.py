"""ICU respiratory vitals processor -- summarize and derive clinical indicators.

IMPORTANT: 사용 제한 사항 (Usage Policy)
    MIMIC-IV chartevents는 ICU 모니터링/ventilator 데이터로,
    일반 외래 폐기능검사(PFT: spirometry, DLCO)와 다릅니다.
    ATS/ERS PFT Standards (Eur Respir J 2005)의 표준 조건을 충족하지 못하므로,
    ICU 폐기능 지표(FiO2, PEEP, Tidal Volume, Plateau Pressure 등)는
    "잠재적 위험 질병 식별(screening)" 용도로만 사용합니다.
    진단 확정은 lab data(ABG) 및 영상 소견 기반으로 합니다.

    usage_level 분류:
        - diagnostic:  확정 진단에 사용 가능 → lab, 영상 결과에 해당
        - screening:   잠재적 위험 식별 전용 → ICU ventilator/monitor 지표
        - supportive:  보조 정보 → 활력징후 (HR, BP, Temp)

Reference Sources:
    - Harrison's Principles of Internal Medicine, 21st Ed (2022)
      Ch.33 "Assessment of Respiratory Function"
    - Berlin Definition for ARDS (JAMA 2012;307:2526-2533)
    - Rice et al. "Comparison of SpO2/FiO2 and PaO2/FiO2" Chest 2007
    - ATS/ERS Standards for PFT (Eur Respir J 2005;26:319-338)
    - See config/chartevents_usage_policy.yaml for per-item usage level
"""

import logging
from typing import Dict, List, Optional, Any
from collections import Counter

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# MIMIC-IV chartevents itemid mapping
# From config/paths.py RESPIRATORY_CHART_ITEMS
# ═══════════════════════════════════════════════════════════════

VITALS_ITEMIDS = {
    # Respiratory Rate
    220210: "respiratory_rate",
    # SpO2
    220277: "spo2",
    # FiO2
    223835: "fio2",
    # PEEP
    220339: "peep",
    # Tidal Volume (observed, set, spontaneous)
    224684: "tidal_volume",
    224685: "tidal_volume",
    224686: "tidal_volume",
    # Minute Volume
    224690: "minute_volume",
    # Lung Sounds (LUL, LLL, RUL, RLL)
    223986: "lung_sounds",
    223987: "lung_sounds",
    223988: "lung_sounds",
    223989: "lung_sounds",
    # Ventilator Mode
    226873: "ventilator_mode",
    # O2 Delivery Device
    226732: "o2_device",
    # Peak Inspiratory Pressure
    220235: "pip",
    # Plateau Pressure
    224696: "plateau_pressure",
    # Heart Rate (supportive)
    220045: "heart_rate",
    # Blood Pressure (supportive)
    220179: "bp_systolic",
    220180: "bp_diastolic",
    # Temperature
    223761: "temperature_f",
    223762: "temperature_c",
}

# ═══════════════════════════════════════════════════════════════
# Usage Level Policy
# ICU ventilator/monitor 지표는 screening only (잠재적 위험 식별)
# 활력징후는 supportive (보조 정보)
# Ref: ATS/ERS PFT Standards (Eur Respir J 2005) — ICU 데이터는 표준 PFT 미충족
# See config/chartevents_usage_policy.yaml
# ═══════════════════════════════════════════════════════════════
USAGE_LEVEL = {
    "respiratory_rate": "supportive",
    "spo2":             "supportive",
    "heart_rate":       "supportive",
    "bp_systolic":      "supportive",
    "bp_diastolic":     "supportive",
    "temperature_f":    "supportive",
    "temperature_c":    "supportive",
    # ICU 폐기능 지표 → screening only (진단 확정 불가)
    "fio2":             "screening",
    "peep":             "screening",
    "tidal_volume":     "screening",
    "minute_volume":    "screening",
    "pip":              "screening",
    "plateau_pressure": "screening",
    "lung_sounds":      "screening",
    "ventilator_mode":  "screening",
    "o2_device":        "screening",
}

SCREENING_DISCLAIMER = (
    "ICU monitoring data — 일반 외래 PFT(ATS/ERS 2005 기준)와 다름. "
    "잠재적 위험 질병 식별(screening) 용도로만 사용. 진단 확정은 ABG/영상 기반."
)

# ═══════════════════════════════════════════════════════════════
# Normal Ranges and Thresholds
# ═══════════════════════════════════════════════════════════════

# Ref: Harrison's 21st Ed, Ch.33 "Normal Values"
NORMAL_RANGES = {
    "respiratory_rate": {"lower": 12, "upper": 20, "unit": "breaths/min",
                         "ref": "Harrison's 21st Ed Ch.33"},
    "spo2": {"lower": 95, "upper": 100, "unit": "%",
             "ref": "Harrison's 21st Ed Ch.33"},
    "heart_rate": {"lower": 60, "upper": 100, "unit": "bpm",
                   "ref": "Harrison's 21st Ed Ch.33"},
    "temperature_c": {"lower": 36.1, "upper": 37.2, "unit": "C",
                      "ref": "Harrison's 21st Ed Ch.33"},
    "temperature_f": {"lower": 97.0, "upper": 99.0, "unit": "F",
                      "ref": "Harrison's 21st Ed Ch.33"},
}

# Ref: Berlin Definition 2012 -- S/F ratio thresholds
# Rice et al. 2007 correlation: S/F 235 ~ P/F 200, S/F 315 ~ P/F 300
SF_THRESHOLDS = {
    "mild_ards": 315,      # Corresponds to P/F ~ 300
    "moderate_ards": 235,  # Corresponds to P/F ~ 200
    "severe_ards": 148,    # Corresponds to P/F ~ 100
}


def _safe_float(value: Any) -> Optional[float]:
    """Convert value to float, returning None on failure."""
    if value is None:
        return None
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _compute_numeric_summary(values: List[float]) -> Dict[str, Optional[float]]:
    """Compute min, max, mean, latest for a list of numeric values."""
    if not values:
        return {"min": None, "max": None, "mean": None, "latest": None, "count": 0}
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
        "latest": round(values[-1], 2),  # Last recorded value
        "count": len(values),
    }


def _check_abnormal(summary: Dict, vital_name: str) -> Dict[str, Any]:
    """Check if a vital sign summary is abnormal.

    Returns the summary dict augmented with abnormal_flag and abnormal_detail.
    """
    result = dict(summary)
    result["abnormal_flag"] = False
    result["abnormal_detail"] = ""

    if vital_name not in NORMAL_RANGES:
        return result

    ranges = NORMAL_RANGES[vital_name]
    latest = summary.get("latest")
    minimum = summary.get("min")
    maximum = summary.get("max")

    abnormalities = []

    if latest is not None:
        if latest < ranges["lower"]:
            abnormalities.append(f"latest {latest} below normal ({ranges['lower']}-{ranges['upper']} {ranges['unit']})")
        elif latest > ranges["upper"]:
            abnormalities.append(f"latest {latest} above normal ({ranges['lower']}-{ranges['upper']} {ranges['unit']})")

    if minimum is not None and minimum < ranges["lower"]:
        abnormalities.append(f"minimum {minimum} below normal")

    if maximum is not None and maximum > ranges["upper"]:
        abnormalities.append(f"maximum {maximum} above normal")

    if abnormalities:
        result["abnormal_flag"] = True
        result["abnormal_detail"] = "; ".join(abnormalities)
        result["reference"] = ranges["ref"]

    return result


def _mode_value(values: List[str]) -> str:
    """Return the most common string value (mode)."""
    if not values:
        return ""
    counter = Counter(values)
    return counter.most_common(1)[0][0]


def summarize_patient_vitals(df: pd.DataFrame) -> Dict[str, Any]:
    """Summarize respiratory vitals for a single patient stay.

    Args:
        df: DataFrame filtered to one (subject_id, hadm_id, stay_id) group.
            Expected columns: itemid, valuenum, value, charttime

    Returns:
        Dict with vital summaries and derived indicators.
    """
    if df.empty:
        return {"status": "no_data"}

    # Sort by charttime
    if "charttime" in df.columns:
        df = df.sort_values("charttime")

    # Collect values by vital type
    numeric_vitals: Dict[str, List[float]] = {
        "respiratory_rate": [],
        "spo2": [],
        "fio2": [],
        "peep": [],
        "tidal_volume": [],
        "minute_volume": [],
        "pip": [],
        "plateau_pressure": [],
        "heart_rate": [],
        "bp_systolic": [],
        "bp_diastolic": [],
        "temperature_c": [],
        "temperature_f": [],
    }
    text_vitals: Dict[str, List[str]] = {
        "lung_sounds": [],
        "ventilator_mode": [],
        "o2_device": [],
    }

    for _, row in df.iterrows():
        itemid = int(row.get("itemid", 0))
        vital_name = VITALS_ITEMIDS.get(itemid)
        if not vital_name:
            continue

        if vital_name in numeric_vitals:
            val = _safe_float(row.get("valuenum"))
            if val is not None:
                # Basic sanity bounds
                if vital_name == "spo2" and (val < 0 or val > 100):
                    continue
                if vital_name == "fio2":
                    # MIMIC stores as percentage or fraction
                    if val > 1.0:
                        val = val / 100.0
                    if val < 0.0 or val > 1.0:
                        continue
                if vital_name == "respiratory_rate" and (val < 0 or val > 80):
                    continue
                numeric_vitals[vital_name].append(val)
        elif vital_name in text_vitals:
            val = str(row.get("value", ""))
            if val and val.lower() not in ("", "nan", "none"):
                text_vitals[vital_name].append(val)

    # ─── Build summary ───
    result: Dict[str, Any] = {}

    # Respiratory Rate
    # Ref: Harrison's 21st Ed -- Normal 12-20 breaths/min
    rr_summary = _compute_numeric_summary(numeric_vitals["respiratory_rate"])
    result["respiratory_rate"] = _check_abnormal(rr_summary, "respiratory_rate")

    # SpO2
    # Ref: Harrison's 21st Ed -- Normal >= 95%
    spo2_summary = _compute_numeric_summary(numeric_vitals["spo2"])
    result["spo2"] = _check_abnormal(spo2_summary, "spo2")

    # FiO2
    fio2_summary = _compute_numeric_summary(numeric_vitals["fio2"])
    result["fio2"] = fio2_summary

    # PEEP
    peep_summary = _compute_numeric_summary(numeric_vitals["peep"])
    result["peep"] = peep_summary

    # Tidal Volume
    tv_summary = _compute_numeric_summary(numeric_vitals["tidal_volume"])
    result["tidal_volume"] = tv_summary

    # Lung Sounds (mode)
    result["lung_sounds"] = {
        "mode": _mode_value(text_vitals["lung_sounds"]),
        "all_values": list(set(text_vitals["lung_sounds"])),
    }

    # Ventilator Mode
    result["ventilator_mode"] = {
        "mode": _mode_value(text_vitals["ventilator_mode"]),
        "all_values": list(set(text_vitals["ventilator_mode"])),
    }

    # O2 Delivery Device
    result["o2_device"] = {
        "mode": _mode_value(text_vitals["o2_device"]),
        "all_values": list(set(text_vitals["o2_device"])),
    }

    # Supportive vitals
    hr_summary = _compute_numeric_summary(numeric_vitals["heart_rate"])
    result["heart_rate"] = _check_abnormal(hr_summary, "heart_rate")
    result["bp_systolic"] = _compute_numeric_summary(numeric_vitals["bp_systolic"])
    result["bp_diastolic"] = _compute_numeric_summary(numeric_vitals["bp_diastolic"])

    # ─── Derived Indicators ───
    result["derived"] = compute_derived_indicators(
        spo2_values=numeric_vitals["spo2"],
        fio2_values=numeric_vitals["fio2"],
        rr_values=numeric_vitals["respiratory_rate"],
        peep_values=numeric_vitals["peep"],
    )

    # ─── Usage Level 태깅 ───
    result["usage_levels"] = {}
    for vital_name in list(numeric_vitals.keys()) + list(text_vitals.keys()):
        result["usage_levels"][vital_name] = USAGE_LEVEL.get(vital_name, "unknown")

    result["screening_disclaimer"] = SCREENING_DISCLAIMER
    result["derived"]["usage_level"] = "screening"
    result["derived"]["note"] = (
        "SpO2/FiO2 ratio, ARDS category, ventilator dependence는 "
        "ICU 데이터 기반 잠재적 위험 식별(screening)입니다. "
        "확정 진단은 ABG(PaO2/FiO2)와 영상 소견을 기반으로 합니다. "
        "Ref: Berlin Definition (JAMA 2012), Rice et al. (Chest 2007)"
    )

    return result


def compute_derived_indicators(
    spo2_values: List[float],
    fio2_values: List[float],
    rr_values: List[float],
    peep_values: List[float],
) -> Dict[str, Any]:
    """Compute derived respiratory indicators.

    Returns dict with:
        - spo2_fio2_ratio: SpO2/FiO2 ratio (P/F surrogate)
        - ards_category: mild/moderate/severe/none based on S/F ratio
        - ventilator_dependence: bool
        - respiratory_distress: bool
        - respiratory_distress_detail: str
    """
    derived: Dict[str, Any] = {}

    # ─── SpO2/FiO2 ratio (surrogate P/F ratio) ───
    # Ref: Rice et al. Chest 2007
    # Use the latest paired SpO2 and FiO2
    sf_ratio = None
    if spo2_values and fio2_values:
        latest_spo2 = spo2_values[-1]
        latest_fio2 = fio2_values[-1]
        if latest_fio2 > 0:
            sf_ratio = round(latest_spo2 / latest_fio2, 1)

    derived["spo2_fio2_ratio"] = sf_ratio

    # ARDS category from S/F ratio
    # Ref: Rice et al. 2007 correlation with Berlin Definition
    if sf_ratio is not None:
        if sf_ratio <= SF_THRESHOLDS["severe_ards"]:
            derived["ards_category"] = "severe"
        elif sf_ratio <= SF_THRESHOLDS["moderate_ards"]:
            derived["ards_category"] = "moderate"
        elif sf_ratio <= SF_THRESHOLDS["mild_ards"]:
            derived["ards_category"] = "mild"
        else:
            derived["ards_category"] = "none"
    else:
        derived["ards_category"] = "unknown"

    # ─── Ventilator dependence ───
    # PEEP > 0 or FiO2 > 0.21 (room air) indicates supplemental O2 / ventilation
    max_peep = max(peep_values) if peep_values else 0
    max_fio2 = max(fio2_values) if fio2_values else 0.21

    derived["ventilator_dependence"] = (max_peep > 0) or (max_fio2 > 0.21)
    derived["max_peep"] = round(max_peep, 1) if peep_values else None
    derived["max_fio2"] = round(max_fio2, 2) if fio2_values else None

    # ─── Respiratory distress flag ───
    # Ref: Harrison's -- RR > 30 = severe tachypnea; SpO2 < 90% = significant hypoxemia
    distress = False
    distress_details = []

    if rr_values:
        max_rr = max(rr_values)
        if max_rr > 30:
            distress = True
            distress_details.append(f"Severe tachypnea (RR max {max_rr})")

    if spo2_values:
        min_spo2 = min(spo2_values)
        if min_spo2 < 90:
            distress = True
            distress_details.append(f"Significant hypoxemia (SpO2 min {min_spo2}%)")

    derived["respiratory_distress"] = distress
    derived["respiratory_distress_detail"] = "; ".join(distress_details) if distress_details else ""

    return derived


def process_vitals_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Process vitals for all patient stays in a DataFrame.

    Groups by (subject_id, hadm_id, stay_id) and summarizes each.

    Args:
        df: DataFrame with MIMIC chartevents columns:
            subject_id, hadm_id, stay_id, itemid, valuenum, value, charttime

    Returns:
        DataFrame with one row per stay, columns for each vital summary.
    """
    if df.empty:
        return pd.DataFrame()

    group_cols = ["subject_id", "hadm_id"]
    if "stay_id" in df.columns:
        group_cols.append("stay_id")

    records = []

    for group_key, group_df in df.groupby(group_cols, dropna=False):
        if len(group_cols) == 3:
            subject_id, hadm_id, stay_id = group_key
        else:
            subject_id, hadm_id = group_key
            stay_id = None

        summary = summarize_patient_vitals(group_df)

        # Flatten into a single row
        row = {
            "subject_id": subject_id,
            "hadm_id": hadm_id,
        }
        if stay_id is not None:
            row["stay_id"] = stay_id

        # Respiratory rate
        rr = summary.get("respiratory_rate", {})
        row["rr_min"] = rr.get("min")
        row["rr_max"] = rr.get("max")
        row["rr_mean"] = rr.get("mean")
        row["rr_latest"] = rr.get("latest")
        row["rr_abnormal"] = rr.get("abnormal_flag", False)

        # SpO2
        spo2 = summary.get("spo2", {})
        row["spo2_min"] = spo2.get("min")
        row["spo2_max"] = spo2.get("max")
        row["spo2_mean"] = spo2.get("mean")
        row["spo2_latest"] = spo2.get("latest")
        row["spo2_abnormal"] = spo2.get("abnormal_flag", False)

        # FiO2
        fio2 = summary.get("fio2", {})
        row["fio2_max"] = fio2.get("max")

        # PEEP
        peep = summary.get("peep", {})
        row["peep_max"] = peep.get("max")

        # Tidal Volume
        tv = summary.get("tidal_volume", {})
        row["tidal_volume_mean"] = tv.get("mean")

        # Lung Sounds
        ls = summary.get("lung_sounds", {})
        row["lung_sounds_mode"] = ls.get("mode", "")

        # Derived
        derived = summary.get("derived", {})
        row["spo2_fio2_ratio"] = derived.get("spo2_fio2_ratio")
        row["ards_category"] = derived.get("ards_category", "unknown")
        row["ventilator_dependence"] = derived.get("ventilator_dependence", False)
        row["respiratory_distress"] = derived.get("respiratory_distress", False)
        row["respiratory_distress_detail"] = derived.get("respiratory_distress_detail", "")

        records.append(row)

    return pd.DataFrame(records)
