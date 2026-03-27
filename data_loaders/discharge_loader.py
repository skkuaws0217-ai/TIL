"""Discharge note loader — extract Chief Complaint, HPI, symptoms."""

import re
import pandas as pd
from config.paths import DISCHARGE_NOTES, DISCHARGE_CC_PARQUET
from data_loaders.base_loader import save_parquet


# Common abbreviation expansions
ABBREV_MAP = {
    "sob": "shortness of breath",
    "doe": "dyspnea on exertion",
    "abd": "abdominal",
    "r/o": "rule out",
    "c/o": "complaining of",
    "w/": "with",
    "w/o": "without",
    "htn": "hypertension",
    "dm": "diabetes mellitus",
    "cad": "coronary artery disease",
    "chf": "congestive heart failure",
    "copd": "chronic obstructive pulmonary disease",
    "uti": "urinary tract infection",
    "pe": "pulmonary embolism",
    "dvt": "deep vein thrombosis",
    "pna": "pneumonia",
    "ams": "altered mental status",
    "n/v": "nausea/vomiting",
    "cp": "chest pain",
    "ha": "headache",
    "loc": "loss of consciousness",
    "bib": "brought in by",
    "pt": "patient",
    "hx": "history",
    "fx": "fracture",
    "tx": "treatment",
    "sx": "symptoms",
    "dx": "diagnosis",
    "rx": "prescription",
}

# ─── 폐 질환 관련 증상 ───
# lung_disease_symptoms.yaml에서 동적 로드 (교과서 기반 fact only)
# Reference: Harrison's 21st, Murray & Nadel 7th, GOLD/GINA/ESC Guidelines
def _load_lung_symptoms():
    """YAML 프로파일에서 모든 증상 추출 + 동의어 매핑."""
    import yaml, os
    yaml_path = os.path.join(os.path.dirname(__file__), "..", "config", "lung_disease_symptoms.yaml")
    symptoms = set()
    try:
        with open(yaml_path, "r") as f:
            profiles = yaml.safe_load(f)
        for disease, info in profiles.items():
            for s in info.get("symptoms", []):
                symptoms.add(s["name"])
    except Exception:
        pass

    # 교과서 기반 동의어 매핑 (Harrison's 용어 ↔ 환자 기록 용어)
    synonyms = {
        "shortness of breath": "dyspnea",
        "breathlessness": "dyspnea",
        "sob": "dyspnea",
        "doe": "dyspnea",
        "blood-tinged sputum": "hemoptysis",
        "productive cough": "sputum production",
        "leg swelling": "peripheral edema",
        "lower extremity edema": "peripheral edema",
        "appetite loss": "anorexia",
        "unintentional weight loss": "weight loss",
    }
    for syn in synonyms:
        symptoms.add(syn)

    return sorted(symptoms), synonyms

LUNG_SYMPTOMS, SYMPTOM_SYNONYMS = _load_lung_symptoms()

# Negation patterns
NEGATION_PATTERNS = [
    r"denies?\s+", r"no\s+", r"without\s+", r"negative\s+for\s+",
    r"absent\s+", r"not\s+complaining\s+of\s+",
]


def extract_section(text, section_name):
    """Extract a named section from discharge note."""
    pattern = rf'{section_name}:\s*\n(.*?)(?:\n\s*\n|\n[A-Z][a-z]+ [A-Z]|\n___|\Z)'
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def extract_chief_complaint(text):
    """Extract Chief Complaint from discharge note."""
    cc = extract_section(text, "Chief Complaint")
    if not cc:
        cc = extract_section(text, "CC")
    if not cc:
        cc = extract_section(text, "Reason for admission")
    return cc.replace("___", "[de-identified]").strip()


def extract_hpi(text):
    """Extract History of Present Illness."""
    hpi = extract_section(text, "History of Present Illness")
    if not hpi:
        hpi = extract_section(text, "HPI")
    return hpi[:2000]  # Limit length


def normalize_text(text):
    """Normalize clinical text with abbreviation expansion."""
    lower = text.lower()
    for abbr, full in ABBREV_MAP.items():
        lower = re.sub(rf'\b{re.escape(abbr)}\b', full, lower)
    return lower


def extract_symptoms(text):
    """Extract present and denied symptoms from clinical text."""
    normalized = normalize_text(text)
    present = []
    denied = []

    for symptom in LUNG_SYMPTOMS:
        if symptom not in normalized:
            continue

        # Check if negated
        is_negated = False
        for neg_pat in NEGATION_PATTERNS:
            pattern = neg_pat + r'.*?' + re.escape(symptom)
            if re.search(pattern, normalized):
                is_negated = True
                break

        if is_negated:
            denied.append(symptom)
        else:
            present.append(symptom)

    return present, denied


def load_discharge_notes(force=False):
    """Extract Chief Complaint + symptoms from all discharge notes.

    Saves to DISCHARGE_CC_PARQUET.
    """
    import os
    if os.path.exists(DISCHARGE_CC_PARQUET) and not force:
        print(f"Loading cached: {DISCHARGE_CC_PARQUET}")
        return pd.read_parquet(DISCHARGE_CC_PARQUET)

    print("Extracting Chief Complaints from discharge notes...")
    print(f"  Source: {DISCHARGE_NOTES}")

    records = []
    chunk_count = 0

    for chunk in pd.read_csv(DISCHARGE_NOTES, chunksize=1000, engine="python"):
        for _, row in chunk.iterrows():
            text = str(row.get("text", ""))
            if not text or text == "nan":
                continue

            cc = extract_chief_complaint(text)
            hpi = extract_hpi(text)

            # Extract symptoms from CC + HPI combined
            combined = f"{cc} {hpi}"
            symptoms_present, symptoms_denied = extract_symptoms(combined)

            records.append({
                "note_id": row.get("note_id", ""),
                "subject_id": row.get("subject_id"),
                "hadm_id": row.get("hadm_id"),
                "charttime": row.get("charttime"),
                "chief_complaint": cc,
                "chief_complaint_normalized": normalize_text(cc) if cc else "",
                "hpi_summary": hpi[:500],
                "symptoms_present": "|".join(symptoms_present),
                "symptoms_denied": "|".join(symptoms_denied),
            })

        chunk_count += 1
        if chunk_count % 50 == 0:
            print(f"  Processed {chunk_count * 1000:,} notes, {len(records):,} extracted...")

    df = pd.DataFrame(records)
    save_parquet(df, DISCHARGE_CC_PARQUET)
    print(f"  Total: {len(df):,} discharge notes with chief complaints extracted")
    return df


if __name__ == "__main__":
    df = load_discharge_notes(force=True)
    print(f"\nSample chief complaints:")
    for _, row in df.head(10).iterrows():
        print(f"  [{row['subject_id']}] CC: {row['chief_complaint'][:80]}")
        if row['symptoms_present']:
            print(f"    Symptoms: {row['symptoms_present']}")
