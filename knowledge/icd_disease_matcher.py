"""Disease matching engine -- score patient records against lung disease profiles.

Loads disease profiles from YAML and computes weighted diagnostic scores
by matching patient findings across four categories: symptoms, lab results,
radiology findings, and microbiology results.

Reference Sources:
    - Harrison's Principles of Internal Medicine, 21st Ed (2022)
    - Murray & Nadel's Textbook of Respiratory Medicine, 7th Ed (2022)
    - Mandell's Infectious Diseases, 9th Ed (2020)
    - WHO ICD-10 Classification
    - See config/lung_disease_profiles.yaml for per-disease citations
"""

import os
import yaml
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any

logger = logging.getLogger(__name__)

# ─── Paths ───
_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
SYMPTOMS_YAML = os.path.join(_CONFIG_DIR, "lung_disease_symptoms.yaml")
PROFILES_YAML = os.path.join(_CONFIG_DIR, "lung_disease_profiles.yaml")


# ─── Data classes ───
@dataclass
class DiagnosticEvidence:
    """A single piece of evidence supporting or opposing a diagnosis."""
    category: str          # "symptom", "lab", "radiology", "micro"
    finding: str           # The specific finding
    matched: bool          # Whether it matched the disease profile
    weight: float          # Category weight from the profile


@dataclass
class DiseaseScore:
    """Scored disease candidate with supporting evidence."""
    disease: str
    icd10_codes: List[str]
    score: float
    confidence: str        # "strong", "moderate", "weak"
    evidence: List[DiagnosticEvidence] = field(default_factory=list)
    matched_count: int = 0
    total_criteria: int = 0


@dataclass
class PatientRecord:
    """Structured patient record assembled from all processors.

    Each field holds the output of the corresponding processor.
    Lists of strings use normalized / canonical terms.
    """
    subject_id: int = 0
    hadm_id: int = 0
    # Chief complaint / symptoms (from discharge_loader)
    chief_complaint: str = ""
    symptoms_present: List[str] = field(default_factory=list)
    symptoms_denied: List[str] = field(default_factory=list)
    # Lab interpretations (from lab_interpreter) -- medical_term strings
    lab_interpretations: List[str] = field(default_factory=list)
    lab_details: List[Dict[str, Any]] = field(default_factory=list)
    # Radiology findings (from radiology_nlp) -- finding dicts
    radiology_findings: List[str] = field(default_factory=list)
    radiology_details: List[Dict[str, Any]] = field(default_factory=list)
    # Microbiology (from micro_interpreter) -- organism names
    micro_findings: List[str] = field(default_factory=list)
    micro_details: List[Dict[str, Any]] = field(default_factory=list)
    # ICU vitals (from vitals_processor)
    vitals_summary: Dict[str, Any] = field(default_factory=dict)
    # Actual ICD diagnoses for validation
    icd_diagnoses: List[str] = field(default_factory=list)


def _load_yaml(path: str) -> dict:
    """Safely load a YAML file, returning empty dict on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        logger.warning("YAML file not found: %s", path)
        return {}
    except yaml.YAMLError as exc:
        logger.error("Failed to parse YAML %s: %s", path, exc)
        return {}


def _normalize(term: str) -> str:
    """Lowercase, strip whitespace for fuzzy matching."""
    return term.strip().lower()


class DiagnosticScorer:
    """Score patient records against lung disease profiles.

    Loads both the symptom-only YAML (lung_disease_symptoms.yaml) and the
    full diagnostic profile YAML (lung_disease_profiles.yaml).  The profile
    YAML is the primary scoring source; the symptom YAML provides fallback
    symptom lists and ICD-10 mappings.

    Scoring algorithm (per disease):
        For each category (symptoms, lab, radiology, micro):
            match_ratio = |patient_findings INTERSECT profile_findings| /
                          |profile_findings|
        score = SUM(weight_c * match_ratio_c) / SUM(weight_c)

    Confidence levels (Ref: clinical decision support thresholds):
        strong   : score > 0.7
        moderate : 0.4 <= score <= 0.7
        weak     : score < 0.4
    """

    def __init__(self,
                 profiles_path: str = PROFILES_YAML,
                 symptoms_path: str = SYMPTOMS_YAML):
        self.profiles: Dict[str, dict] = _load_yaml(profiles_path)
        self.symptoms_db: Dict[str, dict] = _load_yaml(symptoms_path)

        if not self.profiles:
            logger.warning("No disease profiles loaded -- scoring will be empty.")

        # Build a quick ICD-10 -> disease name lookup for validation
        self._icd_to_disease: Dict[str, str] = {}
        for disease, info in self.profiles.items():
            for code in info.get("icd10", []):
                self._icd_to_disease[str(code).upper()] = disease

    # ─── Public API ──────────────────────────────────────────────

    def score_patient(self,
                      patient: PatientRecord,
                      top_n: int = 5) -> List[DiseaseScore]:
        """Score a patient record against all disease profiles.

        Args:
            patient: Assembled PatientRecord with findings from all processors.
            top_n: Return the top-N highest-scoring diseases.

        Returns:
            List of DiseaseScore objects sorted descending by score.
        """
        results: List[DiseaseScore] = []

        # Normalise patient findings once
        pt_symptoms = {_normalize(s) for s in patient.symptoms_present}
        pt_labs = {_normalize(l) for l in patient.lab_interpretations}
        pt_radiology = {_normalize(r) for r in patient.radiology_findings}
        pt_micro = {_normalize(m) for m in patient.micro_findings}

        for disease, profile in self.profiles.items():
            score_result = self._score_disease(
                disease, profile,
                pt_symptoms, pt_labs, pt_radiology, pt_micro,
            )
            results.append(score_result)

        # Sort descending by score
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_n]

    def validate_against_icd(self,
                             scored: List[DiseaseScore],
                             actual_icd_codes: List[str]) -> Dict[str, Any]:
        """Cross-reference scored diseases with actual ICD diagnoses.

        Args:
            scored: Output of score_patient().
            actual_icd_codes: ICD-10 codes from the patient's admission.

        Returns:
            dict with validation metrics: hits, misses, unexpected.
        """
        actual_set = {code.upper().strip() for code in actual_icd_codes}

        # Map actual codes to disease names
        actual_diseases = set()
        for code in actual_set:
            # Try exact match first, then prefix match (J18 matches J18.x)
            if code in self._icd_to_disease:
                actual_diseases.add(self._icd_to_disease[code])
            else:
                for icd, disease in self._icd_to_disease.items():
                    if code.startswith(icd) or icd.startswith(code):
                        actual_diseases.add(disease)

        predicted_diseases = {s.disease for s in scored if s.confidence != "weak"}

        hits = predicted_diseases & actual_diseases
        misses = actual_diseases - predicted_diseases
        false_positives = predicted_diseases - actual_diseases

        return {
            "actual_icd_codes": sorted(actual_set),
            "actual_diseases": sorted(actual_diseases),
            "predicted_diseases": sorted(predicted_diseases),
            "hits": sorted(hits),
            "misses": sorted(misses),
            "false_positives": sorted(false_positives),
            "sensitivity": len(hits) / max(len(actual_diseases), 1),
            "precision": len(hits) / max(len(predicted_diseases), 1),
        }

    def get_disease_icd_codes(self, disease_name: str) -> List[str]:
        """Return ICD-10 codes for a named disease."""
        profile = self.profiles.get(disease_name, {})
        return [str(c) for c in profile.get("icd10", [])]

    # ─── Internal scoring ────────────────────────────────────────

    def _score_disease(self,
                       disease: str,
                       profile: dict,
                       pt_symptoms: set,
                       pt_labs: set,
                       pt_radiology: set,
                       pt_micro: set) -> DiseaseScore:
        """Compute weighted match score for a single disease."""
        weights = profile.get("weights", {
            "symptoms": 0.25, "lab": 0.25,
            "radiology": 0.25, "micro": 0.25,
        })

        evidence: List[DiagnosticEvidence] = []
        total_matched = 0
        total_criteria = 0

        # --- Symptoms ---
        profile_symptoms = {_normalize(s) for s in profile.get("symptoms", [])}
        sym_match, sym_total, sym_evidence = self._match_category(
            "symptom", pt_symptoms, profile_symptoms
        )
        evidence.extend(sym_evidence)

        # --- Lab patterns ---
        profile_labs = {_normalize(l) for l in profile.get("lab_patterns", [])}
        lab_match, lab_total, lab_evidence = self._match_category(
            "lab", pt_labs, profile_labs
        )
        evidence.extend(lab_evidence)

        # --- Radiology ---
        profile_rad = {_normalize(r) for r in profile.get("radiology_findings", [])}
        rad_match, rad_total, rad_evidence = self._match_category(
            "radiology", pt_radiology, profile_rad
        )
        evidence.extend(rad_evidence)

        # --- Microbiology ---
        profile_micro = {_normalize(m) for m in profile.get("micro_findings", [])}
        micro_match, micro_total, micro_evidence = self._match_category(
            "micro", pt_micro, profile_micro
        )
        evidence.extend(micro_evidence)

        # --- Weighted score ---
        # score = sum(weight_i * match_ratio_i) / sum(weight_i)
        # If a category has zero criteria, its weight does not contribute.
        numerator = 0.0
        denominator = 0.0

        for cat_key, cat_match, cat_total in [
            ("symptoms", sym_match, sym_total),
            ("lab", lab_match, lab_total),
            ("radiology", rad_match, rad_total),
            ("micro", micro_match, micro_total),
        ]:
            w = weights.get(cat_key, 0.0)
            if cat_total > 0:
                ratio = cat_match / cat_total
                numerator += w * ratio
                denominator += w

        total_matched = sym_match + lab_match + rad_match + micro_match
        total_criteria = sym_total + lab_total + rad_total + micro_total

        score = numerator / denominator if denominator > 0 else 0.0

        # Confidence classification
        if score > 0.7:
            confidence = "strong"
        elif score >= 0.4:
            confidence = "moderate"
        else:
            confidence = "weak"

        return DiseaseScore(
            disease=disease,
            icd10_codes=[str(c) for c in profile.get("icd10", [])],
            score=round(score, 4),
            confidence=confidence,
            evidence=evidence,
            matched_count=total_matched,
            total_criteria=total_criteria,
        )

    @staticmethod
    def _match_category(
        category: str,
        patient_set: set,
        profile_set: set,
    ) -> Tuple[int, int, List[DiagnosticEvidence]]:
        """Match patient findings against a profile category.

        Uses substring matching in addition to exact matching so that
        e.g. patient term "elevated crp (inflammation)" matches profile
        term "elevated crp (inflammation)" and partial overlaps work.

        Returns:
            (match_count, total_in_profile, evidence_list)
        """
        if not profile_set:
            return 0, 0, []

        evidence = []
        match_count = 0

        for criterion in profile_set:
            matched = False
            # Exact match
            if criterion in patient_set:
                matched = True
            else:
                # Substring matching: patient finding contains criterion
                # or criterion contains patient finding
                for pt_finding in patient_set:
                    if criterion in pt_finding or pt_finding in criterion:
                        matched = True
                        break

            evidence.append(DiagnosticEvidence(
                category=category,
                finding=criterion,
                matched=matched,
                weight=1.0,
            ))
            if matched:
                match_count += 1

        return match_count, len(profile_set), evidence
