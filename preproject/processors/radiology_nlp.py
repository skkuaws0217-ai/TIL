"""Radiology report NLP — extract structured findings from free text."""

import re
from typing import List, Dict


# ─── Lung findings with categories ───
LUNG_FINDINGS = {
    # Infection
    "consolidation": {"category": "infection", "weight": 0.9},
    "infiltrate": {"category": "infection", "weight": 0.7},
    "air bronchogram": {"category": "infection", "weight": 0.8},
    "opacity": {"category": "general", "weight": 0.5},
    "opacification": {"category": "general", "weight": 0.5},
    "airspace disease": {"category": "infection", "weight": 0.7},

    # Effusion
    "pleural effusion": {"category": "effusion", "weight": 0.9},
    "effusion": {"category": "effusion", "weight": 0.7},
    "blunting": {"category": "effusion", "weight": 0.6},
    "meniscus sign": {"category": "effusion", "weight": 0.8},

    # Pneumothorax
    "pneumothorax": {"category": "pneumothorax", "weight": 0.95},

    # Atelectasis
    "atelectasis": {"category": "atelectasis", "weight": 0.8},
    "volume loss": {"category": "atelectasis", "weight": 0.6},

    # Tumor/Mass
    "mass": {"category": "neoplasm", "weight": 0.9},
    "nodule": {"category": "neoplasm", "weight": 0.7},
    "lesion": {"category": "neoplasm", "weight": 0.6},
    "tumor": {"category": "neoplasm", "weight": 0.9},
    "malignancy": {"category": "neoplasm", "weight": 0.9},

    # Interstitial lung disease
    "ground glass": {"category": "ild", "weight": 0.8},
    "ground-glass": {"category": "ild", "weight": 0.8},
    "fibrosis": {"category": "ild", "weight": 0.9},
    "honeycombing": {"category": "ild", "weight": 0.95},
    "interstitial": {"category": "ild", "weight": 0.7},
    "reticular": {"category": "ild", "weight": 0.7},
    "septal thickening": {"category": "ild", "weight": 0.8},

    # COPD/Emphysema
    "emphysema": {"category": "copd", "weight": 0.9},
    "hyperinflation": {"category": "copd", "weight": 0.8},
    "hyperexpansion": {"category": "copd", "weight": 0.8},
    "flattened diaphragm": {"category": "copd", "weight": 0.7},
    "bullae": {"category": "copd", "weight": 0.8},

    # Bronchiectasis
    "bronchiectasis": {"category": "bronchiectasis", "weight": 0.95},

    # Cardiac/Edema
    "cardiomegaly": {"category": "cardiac", "weight": 0.8},
    "pulmonary edema": {"category": "edema", "weight": 0.9},
    "vascular congestion": {"category": "edema", "weight": 0.8},
    "cephalization": {"category": "edema", "weight": 0.7},
    "kerley": {"category": "edema", "weight": 0.8},
    "pulmonary venous hypertension": {"category": "edema", "weight": 0.8},

    # Other
    "cavitation": {"category": "cavitary", "weight": 0.8},
    "cavity": {"category": "cavitary", "weight": 0.8},
    "abscess": {"category": "abscess", "weight": 0.9},
    "empyema": {"category": "empyema", "weight": 0.9},
    "calcification": {"category": "calcification", "weight": 0.5},
    "lymphadenopathy": {"category": "lymph", "weight": 0.7},
    "mediastinal widening": {"category": "mediastinal", "weight": 0.7},
    "pulmonary embolism": {"category": "pe", "weight": 0.95},
    "filling defect": {"category": "pe", "weight": 0.9},
}

# ─── Negation triggers ───
NEGATION_TRIGGERS = [
    "no ", "no evidence of ", "without ", "absent ", "negative for ",
    "clear ", "unremarkable ", "not ", "normal ", "resolved ",
    "no significant ", "no acute ", "no new ", "no definite ",
    "no focal ", "no large ", "no overt ", "denies ",
    "no radiographic evidence", "has resolved",
]

# ─── Pseudo-negation (finding IS present despite negation-like words) ───
PSEUDO_NEGATION = [
    "no change in ", "no interval change", "known ", "stable ",
    "chronic ", "unchanged ", "persistent ", "previously noted",
]

# ─── Location patterns ───
LOCATIONS = [
    "right upper lobe", "right middle lobe", "right lower lobe",
    "left upper lobe", "left lower lobe", "lingula",
    "bilateral", "right", "left", "bibasilar", "basilar",
    "apical", "perihilar", "diffuse",
    "RUL", "RML", "RLL", "LUL", "LLL",
]


def extract_sections(text: str) -> dict:
    """Split radiology report into sections."""
    sections = {}
    section_names = ["EXAMINATION", "INDICATION", "TECHNIQUE", "COMPARISON",
                     "FINDINGS", "IMPRESSION", "CONCLUSION", "RECOMMENDATION"]

    for name in section_names:
        pattern = rf'{name}[:\s]*\n?(.*?)(?=(?:{"|".join(section_names)})[:\s]|\Z)'
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            sections[name.lower()] = match.group(1).strip()

    # If no sections found, treat entire text as findings
    if not sections:
        sections["findings"] = text

    return sections


def is_negated(text: str, finding: str, position: int) -> bool:
    """Check if a finding at a given position is negated."""
    # Get context window (60 chars before the finding)
    start = max(0, position - 60)
    context = text[start:position].lower()

    # Check pseudo-negation first (overrides negation)
    for pseudo in PSEUDO_NEGATION:
        if pseudo in context:
            return False  # Finding IS present

    # Check actual negation
    for trigger in NEGATION_TRIGGERS:
        if context.rstrip().endswith(trigger.rstrip()) or trigger in context[-len(trigger)-10:]:
            return True

    return False


def extract_location(text: str, finding_pos: int) -> str:
    """Extract anatomical location near a finding mention."""
    # Search in a window around the finding
    window = text[max(0, finding_pos - 80):finding_pos + 80].lower()
    for loc in LOCATIONS:
        if loc.lower() in window:
            return loc
    return ""


def parse_radiology_report(text: str) -> Dict:
    """Parse a single radiology report into structured findings.

    Returns:
        dict with: sections, findings (list), impression_text
    """
    sections = extract_sections(text)
    findings = []

    # Focus on IMPRESSION first, then FINDINGS
    search_text = ""
    if "impression" in sections:
        search_text = sections["impression"]
    if "findings" in sections:
        search_text = sections["findings"] + "\n" + search_text

    if not search_text:
        search_text = text

    text_lower = search_text.lower()

    for finding, info in LUNG_FINDINGS.items():
        # Find all occurrences
        start = 0
        while True:
            pos = text_lower.find(finding, start)
            if pos == -1:
                break

            negated = is_negated(text_lower, finding, pos)
            location = extract_location(search_text, pos)

            findings.append({
                "finding": finding,
                "present": not negated,
                "negated": negated,
                "category": info["category"],
                "weight": info["weight"],
                "location": location,
            })

            start = pos + len(finding)

    # Deduplicate: keep strongest signal per finding
    deduped = {}
    for f in findings:
        key = f["finding"]
        if key not in deduped:
            deduped[key] = f
        elif f["present"] and not deduped[key]["present"]:
            deduped[key] = f  # Positive overrides negative

    return {
        "sections": sections,
        "findings": list(deduped.values()),
        "positive_findings": [f for f in deduped.values() if f["present"]],
        "negative_findings": [f for f in deduped.values() if not f["present"]],
        "impression": sections.get("impression", ""),
        "indication": sections.get("indication", ""),
    }


def get_finding_summary(parsed: Dict) -> str:
    """Generate a one-line finding summary."""
    positive = [f["finding"] for f in parsed["positive_findings"]]
    if not positive:
        return "No significant lung findings"
    return "; ".join(positive)
