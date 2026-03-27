"""Rare disease matcher -- match patient findings to rare lung diseases via HPO.

Converts clinical findings from processors into HPO-compatible terms,
scores overlap against Orphadata disease-HPO associations, and recommends
genetic testing for top candidates.

Reference Sources:
    - HPO (Human Phenotype Ontology): https://hpo.jax.org/
    - Orphanet rare disease database: https://www.orpha.net/
    - Koehler et al. "The Human Phenotype Ontology in 2021" NAR 2021
    - Robinson et al. "Improved exome prioritization of disease genes" Genome Res 2014
    - ACMG Secondary Findings v3.2 (2023) for gene panel context

HPO Code References (from hpo.jax.org, accessed 2025):
    HP:0001880 - Eosinophilia
    HP:0020163 - Ground glass opacity on pulmonary HRCT
    HP:0002206 - Pulmonary fibrosis
    HP:0002105 - Hemoptysis
    HP:0002202 - Pleural effusion
    HP:0002110 - Bronchiectasis
    HP:0001217 - Clubbing
    HP:0002090 - Pneumonia
    HP:0002094 - Dyspnea
    HP:0002093 - Respiratory insufficiency
    HP:0002795 - Abnormal respiratory system physiology
    HP:0012735 - Cough
    HP:0001945 - Fever
    HP:0002088 - Abnormal lung morphology
    HP:0100749 - Chest pain
    HP:0001824 - Weight loss
    HP:0001744 - Splenomegaly
    HP:0002240 - Hepatomegaly
    HP:0030830 - Crackles
    HP:0000975 - Hyperhidrosis (night sweats context)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Clinical Finding -> HPO Term Mapping
# Source: HPO ontology (hpo.jax.org), each code verified 2025
# ═══════════════════════════════════════════════════════════════

FINDING_TO_HPO: Dict[str, Dict[str, str]] = {
    # ─── Lab findings ───
    # Ref: HPO "Abnormality of blood and blood-forming tissues" branch
    "eosinophilia": {
        "hpo_id": "HP:0001880",
        "hpo_term": "Eosinophilia",
    },
    "leukocytosis": {
        "hpo_id": "HP:0001974",
        "hpo_term": "Leukocytosis",
    },
    "leukopenia": {
        "hpo_id": "HP:0001882",
        "hpo_term": "Leukopenia",
    },
    "lymphopenia": {
        "hpo_id": "HP:0001888",
        "hpo_term": "Lymphopenia",
    },
    "anemia": {
        "hpo_id": "HP:0001903",
        "hpo_term": "Anemia",
    },
    "thrombocytopenia": {
        "hpo_id": "HP:0001873",
        "hpo_term": "Thrombocytopenia",
    },
    "thrombocytosis": {
        "hpo_id": "HP:0001894",
        "hpo_term": "Thrombocytosis",
    },
    "polycythemia": {
        "hpo_id": "HP:0001901",
        "hpo_term": "Polycythemia",
    },
    "neutrophilia": {
        "hpo_id": "HP:0011897",
        "hpo_term": "Neutrophilia",
    },
    "hypoxemia": {
        "hpo_id": "HP:0012418",
        "hpo_term": "Hypoxemia",
    },

    # ─── Radiology findings ───
    # Ref: HPO "Abnormality of the respiratory system" branch
    "ground glass": {
        "hpo_id": "HP:0020163",
        "hpo_term": "Ground glass opacity on pulmonary HRCT",
    },
    "ground-glass": {
        "hpo_id": "HP:0020163",
        "hpo_term": "Ground glass opacity on pulmonary HRCT",
    },
    "ground glass opacity": {
        "hpo_id": "HP:0020163",
        "hpo_term": "Ground glass opacity on pulmonary HRCT",
    },
    "pulmonary fibrosis": {
        "hpo_id": "HP:0002206",
        "hpo_term": "Pulmonary fibrosis",
    },
    "fibrosis": {
        "hpo_id": "HP:0002206",
        "hpo_term": "Pulmonary fibrosis",
    },
    "honeycombing": {
        "hpo_id": "HP:0025390",
        "hpo_term": "Honeycombing on pulmonary HRCT",
    },
    "bronchiectasis": {
        "hpo_id": "HP:0002110",
        "hpo_term": "Bronchiectasis",
    },
    "pleural effusion": {
        "hpo_id": "HP:0002202",
        "hpo_term": "Pleural effusion",
    },
    "pneumothorax": {
        "hpo_id": "HP:0002107",
        "hpo_term": "Pneumothorax",
    },
    "consolidation": {
        "hpo_id": "HP:0032149",
        "hpo_term": "Pulmonary consolidation",
    },
    "atelectasis": {
        "hpo_id": "HP:0100750",
        "hpo_term": "Atelectasis",
    },
    "emphysema": {
        "hpo_id": "HP:0002097",
        "hpo_term": "Emphysema",
    },
    "cardiomegaly": {
        "hpo_id": "HP:0001640",
        "hpo_term": "Cardiomegaly",
    },
    "lymphadenopathy": {
        "hpo_id": "HP:0002716",
        "hpo_term": "Lymphadenopathy",
    },

    # ─── Symptoms ───
    # Ref: HPO "Clinical phenotype" branches
    "hemoptysis": {
        "hpo_id": "HP:0002105",
        "hpo_term": "Hemoptysis",
    },
    "dyspnea": {
        "hpo_id": "HP:0002094",
        "hpo_term": "Dyspnea",
    },
    "cough": {
        "hpo_id": "HP:0012735",
        "hpo_term": "Cough",
    },
    "chronic cough": {
        "hpo_id": "HP:0012735",
        "hpo_term": "Cough",
    },
    "dry cough": {
        "hpo_id": "HP:0031245",
        "hpo_term": "Nonproductive cough",
    },
    "wheezing": {
        "hpo_id": "HP:0030828",
        "hpo_term": "Wheezing",
    },
    "clubbing": {
        "hpo_id": "HP:0001217",
        "hpo_term": "Clubbing",
    },
    "fever": {
        "hpo_id": "HP:0001945",
        "hpo_term": "Fever",
    },
    "weight loss": {
        "hpo_id": "HP:0001824",
        "hpo_term": "Weight loss",
    },
    "fatigue": {
        "hpo_id": "HP:0012378",
        "hpo_term": "Fatigue",
    },
    "chest pain": {
        "hpo_id": "HP:0100749",
        "hpo_term": "Chest pain",
    },
    "pleuritic chest pain": {
        "hpo_id": "HP:0100749",
        "hpo_term": "Chest pain",
    },
    "night sweats": {
        "hpo_id": "HP:0000975",
        "hpo_term": "Hyperhidrosis",
    },
    "tachypnea": {
        "hpo_id": "HP:0002789",
        "hpo_term": "Tachypnea",
    },
    "cyanosis": {
        "hpo_id": "HP:0000961",
        "hpo_term": "Cyanosis",
    },
    "peripheral edema": {
        "hpo_id": "HP:0012398",
        "hpo_term": "Peripheral edema",
    },
    "syncope": {
        "hpo_id": "HP:0001279",
        "hpo_term": "Syncope",
    },
    "sputum production": {
        "hpo_id": "HP:0033709",
        "hpo_term": "Sputum production",
    },
    "pneumonia": {
        "hpo_id": "HP:0002090",
        "hpo_term": "Pneumonia",
    },
}


# ═══════════════════════════════════════════════════════════════
# Rare disease triggers
# These findings, when present, should prompt rare disease workup
# Ref: Murray & Nadel Ch.72 "Approach to Rare Lung Diseases"
# ═══════════════════════════════════════════════════════════════

RARE_DISEASE_TRIGGERS = {
    # Finding -> rationale
    "eosinophilia": "Eosinophilic lung diseases (EGPA, CEP, HES)",
    "honeycombing": "Familial pulmonary fibrosis, Hermansky-Pudlak syndrome",
    "clubbing": "Familial IPF, hypertrophic osteoarthropathy",
    "bronchiectasis": "Primary ciliary dyskinesia, CF, immunodeficiency",
    "ground glass": "Pulmonary alveolar proteinosis, surfactant disorders",
    "cavitation": "Granulomatosis with polyangiitis (GPA), NTM",
    "lymphadenopathy": "Sarcoidosis, Castleman disease, lymphangioleiomyomatosis",
    "pneumothorax": "LAM, Birt-Hogg-Dube, Marfan syndrome",
    "pulmonary fibrosis": "Telomere disorders, Hermansky-Pudlak, familial IPF",
}


@dataclass
class RareDiseaseMatch:
    """A rare disease candidate with matching evidence."""
    orpha_code: str
    disease_name: str
    score: float                    # 0.0 - 1.0
    matched_hpo: List[str]         # HPO IDs that matched
    total_hpo: int                  # Total HPO terms for this disease
    genes: List[Dict[str, str]]    # Associated genes
    gene_panel: List[str]          # Recommended gene symbols for testing
    confidence: str                 # "strong", "moderate", "weak"


@dataclass
class RareDiseaseResult:
    """Complete rare disease assessment for a patient."""
    triggered: bool
    trigger_reasons: List[str]
    matches: List[RareDiseaseMatch]
    recommended_gene_panel: List[str]
    summary: str


class RareDiseaseMatcher:
    """Match patient findings to rare lung diseases using HPO overlap.

    Scoring:
        For each rare disease in the Orphadata lung disease set:
            patient_hpo = set of HPO terms derived from patient findings
            disease_hpo = set of HPO terms associated with the disease
            overlap = |patient_hpo INTERSECT disease_hpo|
            score = overlap / |disease_hpo|   (Jaccard-like, denominator = disease terms)

        Frequency weighting (from Orphadata):
            Obligate (100%)  -> 1.0
            Very frequent    -> 0.8
            Frequent         -> 0.6
            Occasional       -> 0.3
            Very rare        -> 0.1
            Excluded         -> 0.0

    Ref: Robinson et al. "Improved exome prioritization" -- phenotype-based
         scoring methodology
    """

    # HPO frequency labels from Orphadata -> numeric weights
    # Ref: Orphanet HPO frequency classification
    FREQUENCY_WEIGHTS: Dict[str, float] = {
        "obligate (100%)": 1.0,
        "very frequent (99-80%)": 0.8,
        "frequent (79-30%)": 0.6,
        "occasional (29-5%)": 0.3,
        "very rare (<4-1%)": 0.1,
        "excluded (0%)": 0.0,
    }

    def __init__(self, orphadata_manager=None):
        """Initialize with an OrphadataManager instance.

        Args:
            orphadata_manager: An OrphadataManager with data loaded.
                If None, will attempt to create and load from cache.
        """
        self.orphadata = orphadata_manager
        self._lung_diseases: Dict[str, Dict] = {}

        if self.orphadata is not None:
            self._lung_diseases = self.orphadata.get_lung_rare_diseases()

    def load_from_cache(self) -> None:
        """Load Orphadata from JSON cache if no manager provided."""
        if self.orphadata is None:
            from knowledge.orphadata_manager import OrphadataManager
            self.orphadata = OrphadataManager()
            self.orphadata.load_cache()
        self._lung_diseases = self.orphadata.get_lung_rare_diseases()

    # ─── Public API ──────────────────────────────────────────────

    def assess_patient(self,
                       symptoms: List[str],
                       lab_terms: List[str],
                       radiology_findings: List[str],
                       micro_findings: Optional[List[str]] = None,
                       top_n: int = 10) -> RareDiseaseResult:
        """Full rare disease assessment for a patient.

        Args:
            symptoms: List of symptom strings (from chief_complaint_parser).
            lab_terms: List of medical_term strings (from lab_interpreter).
            radiology_findings: List of finding strings (from radiology_nlp).
            micro_findings: List of organism strings (from micro_interpreter).
            top_n: Number of top matches to return.

        Returns:
            RareDiseaseResult with matches and gene panel recommendations.
        """
        # Step 1: Convert findings to HPO terms
        patient_hpo = self._findings_to_hpo(symptoms, lab_terms, radiology_findings)

        # Step 2: Check for rare disease triggers
        triggered, trigger_reasons = self._check_triggers(
            symptoms, lab_terms, radiology_findings
        )

        # Step 3: Score against rare diseases
        if not self._lung_diseases:
            self.load_from_cache()

        matches = self._score_all_diseases(patient_hpo, top_n=top_n)

        # Step 4: Aggregate gene panel
        recommended_genes = self._build_gene_panel(matches)

        # Step 5: Summary
        if matches:
            top = matches[0]
            summary = (
                f"Top rare disease candidate: {top.disease_name} "
                f"(ORPHA:{top.orpha_code}, score={top.score:.2f}). "
                f"{len(matches)} candidates identified. "
                f"Recommended gene panel: {', '.join(recommended_genes[:10]) if recommended_genes else 'None'}."
            )
        else:
            summary = "No rare disease candidates identified from current findings."

        return RareDiseaseResult(
            triggered=triggered,
            trigger_reasons=trigger_reasons,
            matches=matches,
            recommended_gene_panel=recommended_genes,
            summary=summary,
        )

    def should_trigger_rare_workup(self,
                                   symptoms: List[str],
                                   lab_terms: List[str],
                                   radiology_findings: List[str]) -> Tuple[bool, List[str]]:
        """Quick check whether rare disease workup is warranted.

        Returns:
            (triggered: bool, reasons: list of str)
        """
        return self._check_triggers(symptoms, lab_terms, radiology_findings)

    # ─── Internal ────────────────────────────────────────────────

    def _findings_to_hpo(self,
                         symptoms: List[str],
                         lab_terms: List[str],
                         radiology_findings: List[str]) -> Dict[str, str]:
        """Convert clinical findings to HPO terms.

        Returns:
            Dict[hpo_id -> hpo_term] for all mappable findings.
        """
        patient_hpo: Dict[str, str] = {}

        all_findings = []
        all_findings.extend(symptoms)
        all_findings.extend(lab_terms)
        all_findings.extend(radiology_findings)

        for finding in all_findings:
            finding_lower = finding.strip().lower()

            # Direct lookup
            if finding_lower in FINDING_TO_HPO:
                entry = FINDING_TO_HPO[finding_lower]
                patient_hpo[entry["hpo_id"]] = entry["hpo_term"]
                continue

            # Substring match: e.g. "elevated crp (inflammation)" contains no
            # direct key, but we check if any key is a substring
            for key, entry in FINDING_TO_HPO.items():
                if key in finding_lower or finding_lower in key:
                    patient_hpo[entry["hpo_id"]] = entry["hpo_term"]
                    break

        logger.debug("Mapped %d findings to %d HPO terms", len(all_findings), len(patient_hpo))
        return patient_hpo

    def _check_triggers(self,
                        symptoms: List[str],
                        lab_terms: List[str],
                        radiology_findings: List[str]) -> Tuple[bool, List[str]]:
        """Check if any rare disease triggers are present."""
        all_findings = set()
        for f in symptoms + lab_terms + radiology_findings:
            all_findings.add(f.strip().lower())

        reasons = []
        for trigger, rationale in RARE_DISEASE_TRIGGERS.items():
            for finding in all_findings:
                if trigger in finding or finding in trigger:
                    reasons.append(f"{trigger}: {rationale}")
                    break

        return len(reasons) > 0, reasons

    def _score_all_diseases(self,
                            patient_hpo: Dict[str, str],
                            top_n: int = 10) -> List[RareDiseaseMatch]:
        """Score all lung rare diseases against patient HPO terms."""
        if not patient_hpo:
            return []

        patient_hpo_ids = set(patient_hpo.keys())
        results: List[RareDiseaseMatch] = []

        for orpha_code, disease_info in self._lung_diseases.items():
            disease_name = disease_info.get("name", f"ORPHA:{orpha_code}")
            hpo_terms = disease_info.get("hpo_terms", [])

            if not hpo_terms:
                continue

            # Weighted scoring
            weighted_sum = 0.0
            weight_total = 0.0
            matched_hpo_ids = []

            for hpo_entry in hpo_terms:
                hpo_id = hpo_entry.get("hpo_id", "")
                freq_label = hpo_entry.get("frequency", "").lower()

                # Determine weight from frequency
                weight = 0.5  # Default if frequency unknown
                for label, w in self.FREQUENCY_WEIGHTS.items():
                    if label in freq_label:
                        weight = w
                        break

                if weight <= 0:
                    continue  # Excluded

                weight_total += weight

                if hpo_id in patient_hpo_ids:
                    weighted_sum += weight
                    matched_hpo_ids.append(hpo_id)

            if weight_total == 0:
                continue

            score = weighted_sum / weight_total

            # Skip very low scores
            if score < 0.05 and not matched_hpo_ids:
                continue

            # Get genes
            genes = disease_info.get("genes", [])
            gene_symbols = [g.get("symbol", "") for g in genes if g.get("symbol")]

            # Confidence
            if score > 0.7:
                confidence = "strong"
            elif score >= 0.4:
                confidence = "moderate"
            else:
                confidence = "weak"

            results.append(RareDiseaseMatch(
                orpha_code=orpha_code,
                disease_name=disease_name,
                score=round(score, 4),
                matched_hpo=matched_hpo_ids,
                total_hpo=len(hpo_terms),
                genes=genes,
                gene_panel=gene_symbols,
                confidence=confidence,
            ))

        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_n]

    @staticmethod
    def _build_gene_panel(matches: List[RareDiseaseMatch],
                          max_genes: int = 20) -> List[str]:
        """Aggregate recommended genes from top matches, deduplicated.

        Prioritises genes from higher-scoring disease matches.

        Returns:
            List of unique gene symbols, up to max_genes.
        """
        seen = set()
        panel = []

        for match in matches:
            if match.confidence == "weak":
                continue
            for symbol in match.gene_panel:
                if symbol and symbol not in seen:
                    seen.add(symbol)
                    panel.append(symbol)
                if len(panel) >= max_genes:
                    return panel

        return panel
