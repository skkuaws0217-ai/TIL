"""Microbiology result interpreter -- classify organisms and flag resistance.

Interprets microbiology culture results from MIMIC-IV microbiologyevents,
classifying organisms into clinical categories and detecting multi-drug
resistance patterns.

Reference Sources:
    - Mandell, Douglas, and Bennett's Principles and Practice of
      Infectious Diseases, 9th Ed (2020)
        - Ch.17: Principles of Anti-infective Therapy
        - Ch.67-69: Pneumonia chapters
        - Ch.249-256: Mycobacterial infections
    - CDC/NHSN MDR definitions (2024):
      "Multidrug-resistant organism = resistant to >= 3 antibiotic classes"
      https://www.cdc.gov/nhsn/
    - CLSI M100 Performance Standards for Antimicrobial Susceptibility
      Testing, 34th Ed (2024)
    - ATS/IDSA Guidelines for HAP/VAP (2016)
    - WHO Global AMR Surveillance System (GLASS)
"""

import re
import logging
from typing import List, Dict, Optional, Any
from collections import defaultdict

import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Organism Classification
# Ref: Mandell's Infectious Diseases 9th Ed, organism chapters
# ═══════════════════════════════════════════════════════════════

ORGANISM_CATEGORIES: Dict[str, Dict[str, Any]] = {
    # ─── Typical Bacteria ───
    # Ref: Mandell's Ch.67 "Acute Pneumonia" -- Common CAP pathogens
    "streptococcus pneumoniae": {
        "category": "typical_bacteria",
        "clinical_significance": "Most common cause of CAP",
        "gram": "gram_positive_cocci",
        "ref": "Mandell's Ch.200",
    },
    "haemophilus influenzae": {
        "category": "typical_bacteria",
        "clinical_significance": "Common CAP pathogen, especially in COPD",
        "gram": "gram_negative_coccobacillus",
        "ref": "Mandell's Ch.225",
    },
    "moraxella catarrhalis": {
        "category": "typical_bacteria",
        "clinical_significance": "CAP/COPD exacerbation pathogen",
        "gram": "gram_negative_diplococcus",
        "ref": "Mandell's Ch.212",
    },
    "staphylococcus aureus": {
        "category": "typical_bacteria",
        "clinical_significance": "CAP/HAP, post-influenza pneumonia, cavitary lesions",
        "gram": "gram_positive_cocci",
        "ref": "Mandell's Ch.194",
    },

    # ─── Atypical Bacteria ───
    # Ref: Mandell's Ch.183, 184, 230
    "mycoplasma pneumoniae": {
        "category": "atypical_bacteria",
        "clinical_significance": "Common atypical CAP, younger patients",
        "gram": "no_cell_wall",
        "ref": "Mandell's Ch.183",
    },
    "chlamydia pneumoniae": {
        "category": "atypical_bacteria",
        "clinical_significance": "Atypical CAP",
        "gram": "obligate_intracellular",
        "ref": "Mandell's Ch.184",
    },
    "chlamydophila pneumoniae": {
        "category": "atypical_bacteria",
        "clinical_significance": "Atypical CAP (synonym of C. pneumoniae)",
        "gram": "obligate_intracellular",
        "ref": "Mandell's Ch.184",
    },
    "legionella pneumophila": {
        "category": "atypical_bacteria",
        "clinical_significance": "Legionnaires' disease, severe CAP",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.230",
    },
    "legionella": {
        "category": "atypical_bacteria",
        "clinical_significance": "Legionnaires' disease",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.230",
    },

    # ─── Gram-Negative Bacilli ───
    # Ref: Mandell's Ch.68 "HAP/VAP Pathogens", Ch.217-222
    "pseudomonas aeruginosa": {
        "category": "gram_negative",
        "clinical_significance": "HAP/VAP, CF, bronchiectasis; intrinsically resistant",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.219",
    },
    "klebsiella pneumoniae": {
        "category": "gram_negative",
        "clinical_significance": "HAP, aspiration, cavitary pneumonia; carbapenem-resistance concern",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.217",
    },
    "escherichia coli": {
        "category": "gram_negative",
        "clinical_significance": "HAP, immunocompromised; ESBL concern",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.217",
    },
    "acinetobacter baumannii": {
        "category": "gram_negative",
        "clinical_significance": "HAP/VAP, ICU; extensively drug-resistant",
        "gram": "gram_negative_coccobacillus",
        "ref": "Mandell's Ch.221",
    },
    "acinetobacter": {
        "category": "gram_negative",
        "clinical_significance": "HAP/VAP, ICU",
        "gram": "gram_negative_coccobacillus",
        "ref": "Mandell's Ch.221",
    },
    "enterobacter": {
        "category": "gram_negative",
        "clinical_significance": "HAP, AmpC beta-lactamase producer",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.217",
    },
    "stenotrophomonas maltophilia": {
        "category": "gram_negative",
        "clinical_significance": "HAP in immunocompromised, intrinsically MDR",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.220",
    },
    "serratia marcescens": {
        "category": "gram_negative",
        "clinical_significance": "HAP, intrinsic AmpC",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.217",
    },

    # ─── Anaerobes ───
    # Ref: Mandell's Ch.69 "Lung Abscess and Anaerobic Infections"
    "bacteroides fragilis": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration pneumonia, lung abscess",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.247",
    },
    "bacteroides": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration / anaerobic lung infection",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.247",
    },
    "fusobacterium nucleatum": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration pneumonia, lung abscess, empyema",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.248",
    },
    "fusobacterium": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration / anaerobic infection",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.248",
    },
    "peptostreptococcus": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration pneumonia, mixed anaerobic infection",
        "gram": "gram_positive_cocci",
        "ref": "Mandell's Ch.246",
    },
    "prevotella": {
        "category": "anaerobe",
        "clinical_significance": "Aspiration / anaerobic infection",
        "gram": "gram_negative_bacillus",
        "ref": "Mandell's Ch.247",
    },

    # ─── Fungi ───
    # Ref: Mandell's Ch.257-267
    "aspergillus": {
        "category": "fungus",
        "clinical_significance": "Invasive aspergillosis (immunocompromised), ABPA, aspergilloma",
        "gram": "fungal_hypha",
        "ref": "Mandell's Ch.258",
    },
    "aspergillus fumigatus": {
        "category": "fungus",
        "clinical_significance": "Most common Aspergillus species in lung infection",
        "gram": "fungal_hypha",
        "ref": "Mandell's Ch.258",
    },
    "candida": {
        "category": "fungus",
        "clinical_significance": "Usually colonizer in sputum; rarely true pulmonary candidiasis",
        "gram": "yeast",
        "ref": "Mandell's Ch.257",
    },
    "candida albicans": {
        "category": "fungus",
        "clinical_significance": "Most common Candida; sputum isolate often colonizer",
        "gram": "yeast",
        "ref": "Mandell's Ch.257",
    },
    "pneumocystis jirovecii": {
        "category": "fungus",
        "clinical_significance": "PJP pneumonia in immunocompromised (HIV, transplant)",
        "gram": "atypical_fungus",
        "ref": "Mandell's Ch.264",
    },
    "pjp": {
        "category": "fungus",
        "clinical_significance": "Pneumocystis jirovecii pneumonia",
        "gram": "atypical_fungus",
        "ref": "Mandell's Ch.264",
    },
    "pneumocystis": {
        "category": "fungus",
        "clinical_significance": "PJP pneumonia",
        "gram": "atypical_fungus",
        "ref": "Mandell's Ch.264",
    },
    "cryptococcus": {
        "category": "fungus",
        "clinical_significance": "Pulmonary cryptococcosis, immunocompromised",
        "gram": "yeast",
        "ref": "Mandell's Ch.262",
    },
    "histoplasma": {
        "category": "fungus",
        "clinical_significance": "Endemic mycosis, granulomatous lung disease",
        "gram": "dimorphic_fungus",
        "ref": "Mandell's Ch.263",
    },
    "coccidioides": {
        "category": "fungus",
        "clinical_significance": "Valley fever, endemic mycosis",
        "gram": "dimorphic_fungus",
        "ref": "Mandell's Ch.265",
    },

    # ─── Mycobacteria ───
    # Ref: Mandell's Ch.249-256
    "mycobacterium tuberculosis": {
        "category": "mycobacterium",
        "clinical_significance": "Tuberculosis -- public health emergency, contact tracing required",
        "gram": "acid_fast_bacillus",
        "ref": "Mandell's Ch.249",
    },
    "mycobacterium avium": {
        "category": "mycobacterium_ntm",
        "clinical_significance": "MAC -- most common NTM lung disease",
        "gram": "acid_fast_bacillus",
        "ref": "Mandell's Ch.254",
    },
    "mycobacterium avium complex": {
        "category": "mycobacterium_ntm",
        "clinical_significance": "MAC pulmonary disease",
        "gram": "acid_fast_bacillus",
        "ref": "Mandell's Ch.254",
    },
    "mycobacterium kansasii": {
        "category": "mycobacterium_ntm",
        "clinical_significance": "NTM lung disease, mimics TB radiographically",
        "gram": "acid_fast_bacillus",
        "ref": "Mandell's Ch.254",
    },
    "mycobacterium abscessus": {
        "category": "mycobacterium_ntm",
        "clinical_significance": "Rapidly growing NTM, difficult to treat",
        "gram": "acid_fast_bacillus",
        "ref": "Mandell's Ch.254",
    },

    # ─── Viruses ───
    # Ref: Mandell's Ch.154-165
    "influenza": {
        "category": "virus",
        "clinical_significance": "Influenza pneumonia, secondary bacterial pneumonia risk",
        "gram": "virus",
        "ref": "Mandell's Ch.165",
    },
    "influenza a": {
        "category": "virus",
        "clinical_significance": "Influenza A pneumonia",
        "gram": "virus",
        "ref": "Mandell's Ch.165",
    },
    "influenza b": {
        "category": "virus",
        "clinical_significance": "Influenza B pneumonia",
        "gram": "virus",
        "ref": "Mandell's Ch.165",
    },
    "rsv": {
        "category": "virus",
        "clinical_significance": "RSV pneumonia/bronchiolitis",
        "gram": "virus",
        "ref": "Mandell's Ch.158",
    },
    "respiratory syncytial virus": {
        "category": "virus",
        "clinical_significance": "RSV pneumonia/bronchiolitis",
        "gram": "virus",
        "ref": "Mandell's Ch.158",
    },
    "sars-cov-2": {
        "category": "virus",
        "clinical_significance": "COVID-19 pneumonia",
        "gram": "virus",
        "ref": "Mandell's Ch.155 (supplement)",
    },
    "coronavirus": {
        "category": "virus",
        "clinical_significance": "COVID-19 or other coronavirus",
        "gram": "virus",
        "ref": "Mandell's Ch.155",
    },
    "cmv": {
        "category": "virus",
        "clinical_significance": "CMV pneumonitis in immunocompromised",
        "gram": "virus",
        "ref": "Mandell's Ch.140",
    },
    "cytomegalovirus": {
        "category": "virus",
        "clinical_significance": "CMV pneumonitis in immunocompromised",
        "gram": "virus",
        "ref": "Mandell's Ch.140",
    },
    "adenovirus": {
        "category": "virus",
        "clinical_significance": "Viral pneumonia, military/institutional outbreaks",
        "gram": "virus",
        "ref": "Mandell's Ch.142",
    },
    "rhinovirus": {
        "category": "virus",
        "clinical_significance": "COPD/asthma exacerbation trigger",
        "gram": "virus",
        "ref": "Mandell's Ch.160",
    },
    "parainfluenza": {
        "category": "virus",
        "clinical_significance": "Viral pneumonia, croup",
        "gram": "virus",
        "ref": "Mandell's Ch.159",
    },
    "metapneumovirus": {
        "category": "virus",
        "clinical_significance": "Human metapneumovirus pneumonia",
        "gram": "virus",
        "ref": "Mandell's Ch.158",
    },
}


# ═══════════════════════════════════════════════════════════════
# Antibiotic Classes for MDR Detection
# Ref: CDC/NHSN MDR definitions, CLSI M100
# An organism resistant to >= 3 antibiotic classes is MDR
# ═══════════════════════════════════════════════════════════════

ANTIBIOTIC_CLASSES: Dict[str, str] = {
    # Antibiotic name (lowercase) -> class name
    # ─── Penicillins ───
    "penicillin": "penicillins",
    "ampicillin": "penicillins",
    "amoxicillin": "penicillins",
    "oxacillin": "penicillins",
    "nafcillin": "penicillins",
    "piperacillin": "penicillins",

    # ─── Beta-lactam/inhibitor combos ───
    "ampicillin/sulbactam": "beta_lactam_inhibitor",
    "amoxicillin/clavulanate": "beta_lactam_inhibitor",
    "piperacillin/tazobactam": "beta_lactam_inhibitor",

    # ─── Cephalosporins (1st gen) ───
    "cefazolin": "cephalosporin_1",
    "cephalexin": "cephalosporin_1",

    # ─── Cephalosporins (2nd gen) ───
    "cefuroxime": "cephalosporin_2",
    "cefoxitin": "cephalosporin_2",

    # ─── Cephalosporins (3rd gen) ───
    "ceftriaxone": "cephalosporin_3",
    "cefotaxime": "cephalosporin_3",
    "ceftazidime": "cephalosporin_3",

    # ─── Cephalosporins (4th gen) ───
    "cefepime": "cephalosporin_4",

    # ─── Cephalosporins (5th gen) ───
    "ceftaroline": "cephalosporin_5",

    # ─── Carbapenems ───
    "imipenem": "carbapenems",
    "meropenem": "carbapenems",
    "ertapenem": "carbapenems",
    "doripenem": "carbapenems",

    # ─── Aminoglycosides ───
    "gentamicin": "aminoglycosides",
    "tobramycin": "aminoglycosides",
    "amikacin": "aminoglycosides",

    # ─── Fluoroquinolones ───
    "ciprofloxacin": "fluoroquinolones",
    "levofloxacin": "fluoroquinolones",
    "moxifloxacin": "fluoroquinolones",

    # ─── Macrolides ───
    "azithromycin": "macrolides",
    "erythromycin": "macrolides",
    "clarithromycin": "macrolides",

    # ─── Tetracyclines ───
    "tetracycline": "tetracyclines",
    "doxycycline": "tetracyclines",
    "minocycline": "tetracyclines",
    "tigecycline": "tetracyclines",

    # ─── Sulfonamides ───
    "trimethoprim/sulfamethoxazole": "sulfonamides",
    "trimethoprim-sulfamethoxazole": "sulfonamides",

    # ─── Glycopeptides ───
    "vancomycin": "glycopeptides",
    "teicoplanin": "glycopeptides",

    # ─── Lincosamides ───
    "clindamycin": "lincosamides",

    # ─── Polymyxins ───
    "colistin": "polymyxins",
    "polymyxin b": "polymyxins",

    # ─── Oxazolidinones ───
    "linezolid": "oxazolidinones",

    # ─── Lipopeptides ───
    "daptomycin": "lipopeptides",

    # ─── Nitrofurans ───
    "nitrofurantoin": "nitrofurans",

    # ─── Monobactams ───
    "aztreonam": "monobactams",

    # ─── Glycylcyclines ───
    "tigecycline": "glycylcyclines",

    # ─── Rifamycins ───
    "rifampin": "rifamycins",
    "rifampicin": "rifamycins",

    # ─── Metronidazole ───
    "metronidazole": "nitroimidazoles",
}


# ═══════════════════════════════════════════════════════════════
# Susceptibility Interpretation
# Ref: CLSI M100 34th Ed
# S = Susceptible, I = Intermediate (SDD), R = Resistant
# ═══════════════════════════════════════════════════════════════

SUSCEPTIBILITY_MAP = {
    "S": "susceptible",
    "s": "susceptible",
    "I": "intermediate",
    "i": "intermediate",
    "R": "resistant",
    "r": "resistant",
    # Some MIMIC data may use these variants
    "SUSCEPTIBLE": "susceptible",
    "INTERMEDIATE": "intermediate",
    "RESISTANT": "resistant",
    "SDD": "susceptible_dose_dependent",
    "NI": "not_interpretable",
}


def classify_organism(organism_name: str) -> Dict[str, Any]:
    """Classify an organism into its clinical category.

    Args:
        organism_name: Organism name from culture result.

    Returns:
        Dict with category, clinical_significance, gram, ref.
        Returns unknown category if organism not in database.
    """
    if not organism_name or not isinstance(organism_name, str):
        return {
            "organism": str(organism_name),
            "category": "unknown",
            "clinical_significance": "",
            "gram": "",
            "ref": "",
        }

    name_lower = organism_name.strip().lower()

    # Direct lookup
    if name_lower in ORGANISM_CATEGORIES:
        result = dict(ORGANISM_CATEGORIES[name_lower])
        result["organism"] = organism_name.strip()
        return result

    # Partial match -- organism name may contain extra info
    for key, info in ORGANISM_CATEGORIES.items():
        if key in name_lower or name_lower in key:
            result = dict(info)
            result["organism"] = organism_name.strip()
            return result

    # Generic gram stain patterns from the name
    result = {
        "organism": organism_name.strip(),
        "category": "unknown",
        "clinical_significance": "Not in standard database -- review manually",
        "gram": "",
        "ref": "",
    }

    # Try to infer from common patterns
    if "streptococcus" in name_lower:
        result["category"] = "typical_bacteria"
        result["gram"] = "gram_positive_cocci"
    elif "staphylococcus" in name_lower:
        result["category"] = "typical_bacteria"
        result["gram"] = "gram_positive_cocci"
    elif "mycobacterium" in name_lower:
        result["category"] = "mycobacterium"
        result["gram"] = "acid_fast_bacillus"
    elif "candida" in name_lower:
        result["category"] = "fungus"
        result["gram"] = "yeast"

    return result


def interpret_susceptibility(interpretation: str) -> str:
    """Interpret a susceptibility code.

    Args:
        interpretation: S, I, R, or full text.

    Returns:
        Standardized interpretation string.

    Ref: CLSI M100 34th Ed, "Interpretive Categories"
    """
    if not interpretation or not isinstance(interpretation, str):
        return "not_tested"
    cleaned = interpretation.strip()
    return SUSCEPTIBILITY_MAP.get(cleaned, f"unknown ({cleaned})")


def detect_mdr(susceptibility_results: List[Dict[str, str]]) -> Dict[str, Any]:
    """Detect multi-drug resistance from susceptibility results.

    An organism is MDR if resistant to >= 3 antibiotic classes.
    Ref: CDC/NHSN MDR definition (2024):
         "MDR = non-susceptible to >= 1 agent in >= 3 antimicrobial categories"

    Args:
        susceptibility_results: List of dicts with keys:
            - "antibiotic": antibiotic name
            - "interpretation": S/I/R

    Returns:
        Dict with: is_mdr, resistant_classes, total_classes_tested,
                   resistant_antibiotics, susceptible_antibiotics
    """
    resistant_classes = set()
    tested_classes = set()
    resistant_antibiotics = []
    susceptible_antibiotics = []

    for result in susceptibility_results:
        abx_name = result.get("antibiotic", "").strip().lower()
        interp = interpret_susceptibility(result.get("interpretation", ""))

        # Determine antibiotic class
        abx_class = ANTIBIOTIC_CLASSES.get(abx_name, "")
        if not abx_class:
            # Try partial match
            for key, cls in ANTIBIOTIC_CLASSES.items():
                if key in abx_name or abx_name in key:
                    abx_class = cls
                    break

        if abx_class:
            tested_classes.add(abx_class)

        if interp == "resistant":
            resistant_antibiotics.append(result.get("antibiotic", abx_name))
            if abx_class:
                resistant_classes.add(abx_class)
        elif interp == "susceptible":
            susceptible_antibiotics.append(result.get("antibiotic", abx_name))

    is_mdr = len(resistant_classes) >= 3

    return {
        "is_mdr": is_mdr,
        "resistant_classes": sorted(resistant_classes),
        "resistant_class_count": len(resistant_classes),
        "total_classes_tested": len(tested_classes),
        "resistant_antibiotics": resistant_antibiotics,
        "susceptible_antibiotics": susceptible_antibiotics,
        "mdr_definition": "CDC/NHSN: resistant to >= 1 agent in >= 3 antimicrobial categories",
    }


def interpret_micro_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """Interpret a single microbiology record from MIMIC-IV.

    Expects MIMIC microbiologyevents columns:
        org_name, ab_name, interpretation, isolate_num, spec_type_desc

    Returns:
        Dict with organism classification, susceptibility, and flags.
    """
    organism_name = str(row.get("org_name", ""))
    antibiotic = str(row.get("ab_name", ""))
    interp_code = str(row.get("interpretation", ""))
    specimen = str(row.get("spec_type_desc", ""))

    result = {
        "specimen_type": specimen,
        "organism": organism_name,
        "antibiotic": antibiotic,
        "susceptibility": "",
        "organism_info": {},
    }

    # Classify organism
    if organism_name and organism_name.lower() not in ("", "nan", "none"):
        result["organism_info"] = classify_organism(organism_name)

    # Interpret susceptibility
    if interp_code and interp_code.lower() not in ("", "nan", "none"):
        result["susceptibility"] = interpret_susceptibility(interp_code)

    return result


def interpret_micro_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Interpret all microbiology results in a DataFrame.

    Groups by (subject_id, hadm_id, org_name) to:
        1. Classify each organism
        2. Aggregate susceptibility results
        3. Detect MDR per organism

    Args:
        df: DataFrame with MIMIC microbiologyevents columns.

    Returns:
        DataFrame with one row per (subject_id, hadm_id, organism)
        including classification, susceptibility summary, and MDR flag.
    """
    if df.empty:
        return pd.DataFrame()

    # Ensure required columns exist
    required_cols = {"subject_id", "hadm_id"}
    missing = required_cols - set(df.columns)
    if missing:
        logger.error("Missing required columns: %s", missing)
        return pd.DataFrame()

    records = []

    # Group by patient admission and organism
    group_cols = ["subject_id", "hadm_id"]
    if "org_name" in df.columns:
        group_cols.append("org_name")

    for group_key, group_df in df.groupby(group_cols, dropna=False):
        if len(group_cols) == 3:
            subject_id, hadm_id, org_name = group_key
        else:
            subject_id, hadm_id = group_key
            org_name = ""

        org_str = str(org_name) if pd.notna(org_name) else ""

        # Classify organism
        org_info = classify_organism(org_str) if org_str else {}

        # Collect susceptibility results
        suscept_results = []
        for _, row in group_df.iterrows():
            ab_name = str(row.get("ab_name", ""))
            interp = str(row.get("interpretation", ""))
            if ab_name and ab_name.lower() not in ("", "nan", "none"):
                suscept_results.append({
                    "antibiotic": ab_name,
                    "interpretation": interp,
                })

        # Detect MDR
        mdr_info = detect_mdr(suscept_results) if suscept_results else {
            "is_mdr": False, "resistant_classes": [],
            "resistant_class_count": 0, "total_classes_tested": 0,
            "resistant_antibiotics": [], "susceptible_antibiotics": [],
        }

        # Specimen type (take first non-null)
        specimen = ""
        if "spec_type_desc" in group_df.columns:
            specimens = group_df["spec_type_desc"].dropna().unique()
            if len(specimens) > 0:
                specimen = str(specimens[0])

        records.append({
            "subject_id": subject_id,
            "hadm_id": hadm_id,
            "organism": org_str,
            "category": org_info.get("category", "unknown"),
            "clinical_significance": org_info.get("clinical_significance", ""),
            "gram": org_info.get("gram", ""),
            "reference": org_info.get("ref", ""),
            "specimen_type": specimen,
            "is_mdr": mdr_info["is_mdr"],
            "resistant_classes": "|".join(mdr_info["resistant_classes"]),
            "resistant_class_count": mdr_info["resistant_class_count"],
            "resistant_antibiotics": "|".join(mdr_info["resistant_antibiotics"]),
            "susceptible_antibiotics": "|".join(mdr_info["susceptible_antibiotics"]),
        })

    return pd.DataFrame(records)
